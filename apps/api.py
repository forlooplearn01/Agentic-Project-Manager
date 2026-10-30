import concurrent.futures
import hashlib
import json
import re
import threading
import uuid
from datetime import date, timedelta
from typing import List, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from apps.agent.chat import run_chat_agent
from apps.graph import build_graph
from apps.utils.raid_ai import suggest_raid_items_from_text
from apps.utils.hours_budget_ai import suggest_budget_hours
from apps.utils import hours_import, timesheet_service, timesheet_watcher
from apps.utils.plan_export import build_plan_workbook
from apps.db.plan_db import create_plan_task, list_plan_changes, plan_has_content, plan_summary, remove_plan_task, set_plan_baseline
from apps.utils.hours import week_start as _week_start
from apps.utils.registers_export import build_registers_workbook
from apps.utils.deliverable_ai import propose_assignments, suggest_deliverables_from_text
from apps.db.hours_db import (
    apply_plan_import,
    apply_timesheet_import,
    availability_report,
    create_import_record,
    ensure_hours_schema,
    fetch_active_tasks_for_hours,
    fetch_hours_inputs,
    fetch_time_entries,
    get_hours_settings,
    get_import,
    get_timesheet_week,
    last_applied_at,
    list_folder_imports,
    list_imports,
    reassign_import_group,
    project_hours_report,
    project_ids_with_hours,
    save_timesheet_week,
    set_hours_settings,
    set_person_capacity,
    set_task_hours,
    undo_import,
)
from apps.db.db import (
    assign_tasks_to_deliverables,
    add_task_dependency,
    approve_meeting_record,
    cache_pipeline_result,
    cleanup_old_jobs,
    complete_job,
    create_deliverable,
    create_deliverables_from_tasks,
    create_job,
    create_raid_item,
    delete_deliverable,
    delete_project,
    delete_raid_item,
    dismiss_possible_duplicate,
    ensure_document_chunks_table,
    ensure_duplicate_handling_schema,
    ensure_registers_schema,
    ensure_jobs_table,
    ensure_meetings_table,
    ensure_project_people_email_nullable,
    ensure_source_documents_project_column,
    ensure_tasks_jira_column,
    fail_job,
    fetch_chat_history,
    fetch_documents_to_scan,
    fetch_latest_sow_text,
    fetch_project_tasks_with_dependencies,
    fetch_unassigned_tasks,
    find_tasks_similar_to_deliverables,
    fetch_raci_matrix,
    list_deliverables,
    list_raid_items,
    mark_documents_scanned,
    merge_tasks,
    promote_risk_to_issue,
    set_deliverable_raci,
    set_task_deliverable,
    store_suggested_deliverables,
    store_suggested_raid_items,
    set_task_raci,
    update_deliverable,
    update_raid_item,
    fetch_projects,
    fetch_recent_agent_activity,
    get_cached_pipeline_result,
    get_job,
    get_meeting,
    get_project_jira_key,
    insert_chat_message,
    list_meetings,
    delete_project_person,
    list_project_people,
    upsert_project_person,
    reject_meeting_record,
    remove_task_dependency,
    set_project_jira_key,
)
from apps.logger import get_logger
from apps.utils.document_extract import ExtractionError, extract_text
from mcp_server.tools.calendar import schedule_meeting
from mcp_server.tools.postgres import get_task_by_id, search_tasks, update_task_status
from mcp_server.tools.report import generate_report

load_dotenv()
logger = get_logger("API")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Agentic Project Manager",
    description="Multi-agent system: SOW -> project plan, meeting minutes -> plan updates, "
                "with autonomous prioritization, sync/reporting, and follow-up scheduling.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    # 3010, not the usual 3000 - this project's docker-compose maps the frontend there
    # since 3000 is already used by AI Assistant's own frontend on this machine.
    allow_origins=["http://localhost:3010"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    ensure_jobs_table()
    ensure_meetings_table()
    ensure_tasks_jira_column()
    ensure_source_documents_project_column()
    ensure_project_people_email_nullable()
    ensure_document_chunks_table()
    ensure_duplicate_handling_schema()
    ensure_registers_schema()
    ensure_hours_schema()
    timesheet_watcher.start_watcher()
    deleted = cleanup_old_jobs(max_age_hours=24)
    if deleted:
        logger.info(f"Startup cleanup: removed {deleted} stale job row(s)")
    # Warm up the sentence transformer inside a ThreadPoolExecutor thread - the same
    # execution context where the graph runs. PyTorch allocates thread-local memory
    # on first use, so warming up here ensures it's ready before any real request arrives.
    from apps.utils.embeddings import get_embedding
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(get_embedding, "warmup").result()
    logger.info("Embedding model warmed up")


MAX_JOB_SECONDS = 600  # 10 minutes - hard cap on total run time

_graph = build_graph()


def _initial_state(input_text: str, project_id: str) -> dict:
    return {
        "raw_input": input_text,
        "project_id": project_id,
        "document_type": "",
        "tasks": [],
        "normalized_events": [],
        "followup_signal": False,
        "matched_events": [],
        "events_to_update": [],
        "events_to_create": [],
        "unresolved_events": [],
        "planned_tasks": [],
        "planning_trace": [],
        "sync_reporting_triggered": False,
        "sync_reporting_trace": [],
        "sync_reporting_result": None,
        "meeting_coordinator_triggered": False,
        "meeting_coordinator_trace": [],
        "scheduled_meeting": None,
        "final_output": {},
    }


def _run_graph_job(job_id: str, input_text: str, project_id: str) -> None:
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_graph.invoke, _initial_state(input_text, project_id))
            try:
                result_state = future.result(timeout=MAX_JOB_SECONDS)
            except concurrent.futures.TimeoutError:
                msg = f"Run timed out after {MAX_JOB_SECONDS // 60} minutes"
                logger.error(f"Job {job_id}: {msg}")
                fail_job(job_id, msg)
                return

        final_output = result_state.get("final_output", {})
        # document_type isn't in final_output but is useful on the job record for the dashboard
        final_output["document_type"] = result_state.get("document_type")
        complete_job(job_id, final_output)
        if final_output.get("tasks_count", 0) > 0:
            cache_pipeline_result(input_text, final_output, project_id)
    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        fail_job(job_id, str(e))


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    input_text: str
    project_id: str = "default"
    force_run: bool = False


class TaskPatchRequest(BaseModel):
    field: str   # status | priority | owner | deadline | blocker
    value: str


class DependencyRequest(BaseModel):
    depends_on_task_id: str


class ProjectIntegrationsRequest(BaseModel):
    jira_project_key: Optional[str] = None


class ProjectPersonRequest(BaseModel):
    name: str
    email: str
    role: Optional[str] = None


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ScheduleMeetingRequest(BaseModel):
    title: str
    attendee_emails: list[str]
    start_iso: str
    end_iso: str
    description: Optional[str] = None


class MeetingApprovalRequest(BaseModel):
    # All optional - a person reviewing the proposal in the UI can edit any field
    # before approving; anything omitted falls back to what the agent proposed.
    title: Optional[str] = None
    attendee_emails: Optional[list[str]] = None
    start_iso: Optional[str] = None
    end_iso: Optional[str] = None
    description: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Liveness check - returns ok when the server is running."""
    return {"status": "ok"}


@app.post("/extract-text")
async def extract_text_from_file(file: UploadFile = File(...)):
    """Extract plain text from an uploaded .txt/.docx/.pdf so the frontend can preview it
    before submitting via POST /run - this endpoint does not touch the graph/pipeline at
    all, it only turns a file into the same input_text string that pasting would produce."""
    content = await file.read()
    try:
        text = extract_text(file.filename or "", content)
    except ExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {"text": text, "filename": file.filename, "char_count": len(text)}


@app.post("/run")
async def run(request: RunRequest):
    """Submit a document (SOW or MoM) for processing. Returns a job_id immediately;
    poll GET /jobs/{job_id} for results. Set force_run=true to bypass the cache."""
    job_id = str(uuid.uuid4())
    content_hash = hashlib.sha256(request.input_text.encode()).hexdigest()

    if not request.force_run:
        cached = get_cached_pipeline_result(content_hash, request.project_id)
        if cached:
            create_job(job_id, request.project_id)
            complete_job(job_id, {"_cached_result": cached})
            logger.info(f"POST /run - cache hit, job_id={job_id} hash={content_hash[:12]}…")
            return {"job_id": job_id, "from_cache": True}

    create_job(job_id, request.project_id)
    thread = threading.Thread(
        target=_run_graph_job,
        args=(job_id, request.input_text, request.project_id),
        daemon=True,
    )
    thread.start()
    logger.info(f"POST /run - job_id={job_id} project_id={request.project_id} force={request.force_run}")
    return {"job_id": job_id, "from_cache": False}


@app.get("/jobs/{job_id}")
def poll_job(job_id: str):
    """Poll the status of a run. Returns status: running | completed | failed."""
    job = get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] == "completed":
        raw = job["result"]
        if isinstance(raw, dict) and "_cached_result" in raw:
            return {"status": "completed", "result": raw["_cached_result"], "from_cache": True}
        return {"status": "completed", "result": raw, "from_cache": False}

    if job["status"] == "failed":
        return {"status": "failed", "error": job.get("error", "unknown error")}

    return {"status": "running"}


@app.get("/jobs/{job_id}/trace")
def get_job_trace(job_id: str):
    """Which agents actually ran for this job, and their tool calls - powers the
    dashboard's run/trace panel. Planning always ran (if there were tasks); Sync/Reporting
    and Meeting Coordinator only show tool calls if they were actually triggered."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        return {"status": job["status"], "agents": []}

    raw = job["result"]
    if isinstance(raw, dict) and "_cached_result" in raw:
        raise HTTPException(status_code=404, detail="Trace not available for a cache-served result")

    return {
        "status": "completed",
        "document_type": raw.get("document_type"),
        "agents": [
            {"agent": "planning", "triggered": True, "trace": raw.get("planning_trace", [])},
            {
                "agent": "sync_reporting",
                "triggered": raw.get("sync_reporting_triggered", False),
                "trace": raw.get("sync_reporting_trace", []),
            },
            {
                "agent": "meeting_coordinator",
                "triggered": raw.get("meeting_coordinator_triggered", False),
                "trace": raw.get("meeting_coordinator_trace", []),
                "scheduled_meeting": raw.get("scheduled_meeting"),
            },
        ],
    }


@app.get("/projects")
def get_projects():
    """Every project_id with a rollup (open/blocked/done counts, last update) - powers
    the dashboard's Projects view."""
    return {"projects": fetch_projects()}


@app.get("/projects/{project_id}/tasks")
def get_project_tasks(project_id: str):
    """Task board for one project, each row including owner/status/priority/deadline
    and dependency info (what it depends on, what it blocks) joined in."""
    return {"tasks": fetch_project_tasks_with_dependencies(project_id)}


@app.delete("/projects/{project_id}")
def remove_project(project_id: str):
    """Permanently deletes a project and most things scoped to it (tasks, dependencies,
    Jira key, job/activity history) - but not project_people, which deliberately
    survives (see delete_project's docstring). No undo on what is deleted - the
    frontend requires typing the project id to confirm before calling this."""
    deleted_tasks = delete_project(project_id)
    if deleted_tasks == 0:
        raise HTTPException(status_code=404, detail=f"No project found with id '{project_id}'")
    return {"project_id": project_id, "deleted_tasks": deleted_tasks, "status": "deleted"}


@app.get("/projects/{project_id}/people")
def get_project_people(project_id: str):
    """Known people for this project (name/email/role) - powers the attendee picker
    on the manual Schedule Meeting form."""
    return {"people": list_project_people(project_id)}


@app.post("/projects/{project_id}/people")
def add_project_person(project_id: str, body: ProjectPersonRequest):
    """Add or edit a person this project can resolve attendees against - the only
    path to populating project_people used to be a manual DB insert; this is what
    Meeting Coordinator's resolve_attendees and the manual Schedule Meeting form's
    attendee picker both read from."""
    if not body.name.strip() or not body.email.strip():
        raise HTTPException(status_code=400, detail="Both name and email are required")
    upsert_project_person(project_id, body.name.strip(), body.email.strip(), body.role.strip() if body.role else None)
    return {"people": list_project_people(project_id)}


@app.delete("/projects/{project_id}/people/{name}")
def remove_project_person(project_id: str, name: str):
    """Remove a person from this project's known attendees."""
    deleted = delete_project_person(project_id, name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No person named '{name}' found in project '{project_id}'")
    return {"people": list_project_people(project_id)}


@app.post("/projects/{project_id}/schedule-meeting")
def schedule_project_meeting(project_id: str, body: ScheduleMeetingRequest):
    """Manually schedule a meeting from the Workspace, bypassing the Meeting Coordinator
    agent entirely - the user filled in every field by hand (title, attendees, time),
    which is already the deliberate decision an agent would otherwise have to infer, so
    there's nothing left for an LLM to reason about here. Calls the same calendar tool
    the agent uses, just invoked directly."""
    if not body.attendee_emails:
        raise HTTPException(status_code=400, detail="At least one attendee is required")

    raw = schedule_meeting(
        title=body.title,
        attendee_emails=body.attendee_emails,
        start_iso=body.start_iso,
        end_iso=body.end_iso,
        description=body.description,
    )
    data = json.loads(raw)

    if data.get("status") != "scheduled":
        raise HTTPException(status_code=502, detail=data.get("message") or data.get("error") or "Failed to schedule meeting")

    return data


@app.get("/projects/{project_id}/meetings")
def get_project_meetings(project_id: str):
    """Every meeting the Meeting Coordinator has proposed for this project - pending,
    approved (scheduled), or rejected - so a person can see what the agent has done
    without digging through the Activity Feed's trace log."""
    return {"meetings": list_meetings(project_id=project_id)}


@app.get("/meetings/pending")
def get_pending_meetings():
    """Every pending-approval meeting across all projects - powers a portfolio-wide
    'meetings waiting on you' count on the Dashboard."""
    return {"meetings": list_meetings(status="pending_approval")}


@app.post("/meetings/{meeting_id}/approve")
def approve_meeting(meeting_id: str, body: MeetingApprovalRequest):
    """A person approving a Meeting Coordinator proposal - this is the only path that
    actually creates the Google Calendar event and sends invites. Any field the
    reviewer edited in the approval modal overrides what the agent proposed."""
    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail=f"No meeting found with id '{meeting_id}'")
    if meeting["status"] != "pending_approval":
        raise HTTPException(status_code=409, detail=f"Meeting is already '{meeting['status']}', not pending approval")

    title = body.title if body.title is not None else meeting["title"]
    attendee_emails = body.attendee_emails if body.attendee_emails is not None else meeting["attendee_emails"]
    start_iso = body.start_iso if body.start_iso is not None else meeting["start_iso"]
    end_iso = body.end_iso if body.end_iso is not None else meeting["end_iso"]
    description = body.description if body.description is not None else meeting["description"]

    if not attendee_emails:
        raise HTTPException(status_code=400, detail="At least one attendee is required")

    raw = schedule_meeting(
        title=title,
        attendee_emails=attendee_emails,
        start_iso=start_iso,
        end_iso=end_iso,
        description=description,
    )
    data = json.loads(raw)

    if data.get("status") != "scheduled":
        raise HTTPException(status_code=502, detail=data.get("message") or data.get("error") or "Failed to schedule meeting")

    return approve_meeting_record(
        meeting_id=meeting_id,
        google_event_id=data.get("event_id"),
        meeting_link=data.get("meeting_link"),
        title=title,
        attendee_emails=attendee_emails,
        start_iso=start_iso,
        end_iso=end_iso,
    )


@app.post("/meetings/{meeting_id}/reject")
def reject_meeting(meeting_id: str):
    """A person declining a Meeting Coordinator proposal - no calendar event is ever
    created for it."""
    meeting = get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail=f"No meeting found with id '{meeting_id}'")
    if meeting["status"] != "pending_approval":
        raise HTTPException(status_code=409, detail=f"Meeting is already '{meeting['status']}', not pending approval")
    return reject_meeting_record(meeting_id)


@app.get("/projects/{project_id}/integrations")
def get_project_integrations(project_id: str):
    """This project's per-project external-system settings - currently just which
    Jira project its tasks sync into (Jira auth itself is a global .env setting)."""
    return {"jira_project_key": get_project_jira_key(project_id)}


@app.put("/projects/{project_id}/integrations")
def put_project_integrations(project_id: str, body: ProjectIntegrationsRequest):
    """Set which Jira project this task-manager project's tasks should sync into."""
    set_project_jira_key(project_id, body.jira_project_key)
    return {"project_id": project_id, "jira_project_key": body.jira_project_key}


@app.get("/projects/{project_id}/export")
def export_project_excel(project_id: str):
    """Excel export of the project's plan: every task with phase, weeks, estimate, baseline, logged and remaining hours, a summary and the
    change history. One-way (the app is the master); the Sync/Reporting agent keeps using its own generate_report."""
    if not fetch_project_tasks_with_dependencies(project_id):
        raise HTTPException(status_code=404, detail=f"No tasks found for project '{project_id}'")
    return Response(
        content=build_plan_workbook(project_id),
        headers={"Content-Disposition": f'attachment; filename="{project_id}-plan.xlsx"'},
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/projects/{project_id}/registers/export")
def export_registers_excel(project_id: str):
    """Excel snapshot of the project's registers (RAID log, RACI by deliverable and by task, deliverables). Read-only: edits
    made in the file never flow back. Items still waiting for review are left out and counted on the Summary sheet."""
    data = build_registers_workbook(project_id)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", project_id)  # the id is user text going into a header
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}-registers.xlsx"'},
    )


@app.get("/activity")
def get_activity(
    project_id: Optional[str] = Query(None, description="Scope to one project; omit for all"),
    limit: int = Query(20, le=50),
):
    """Recent agent actions (which agent, what tool, what happened), flattened from
    recent completed runs - powers the dashboard's activity feed."""
    return {"events": fetch_recent_agent_activity(project_id, limit)}


@app.get("/tasks")
def list_tasks(
    query: Optional[str] = Query(None, description="Text search across task_text"),
    owner: Optional[str] = Query(None, description="Filter by owner name"),
    status: Optional[str] = Query(None, description="Filter by status"),
    project_id: Optional[str] = Query(None, description="Filter by project ID"),
):
    """List active tasks with optional filters. Returns up to 20 results ordered by updated_at."""
    raw = search_tasks(query=query, owner=owner, status=status, project_id=project_id)
    data = json.loads(raw)

    if isinstance(data, dict) and "error" in data:
        raise HTTPException(status_code=500, detail=data["error"])

    return {"tasks": data, "count": len(data)}


@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    """Fetch a single task by its UUID."""
    raw = get_task_by_id(task_id)
    data = json.loads(raw)

    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])

    return data


@app.patch("/tasks/{task_id}")
def patch_task(task_id: str, body: TaskPatchRequest):
    """Update a single field on an existing task."""
    raw = update_task_status(task_id=task_id, field=body.field, value=body.value)
    data = json.loads(raw)

    if "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])

    return data


class MergeTaskRequest(BaseModel):
    into_task_id: str


@app.post("/tasks/{task_id}/merge")
def merge_task(task_id: str, body: MergeTaskRequest):
    """Merge a flagged possible-duplicate into the existing task it was flagged against.
    Soft-deletes task_id (history and dependencies move to into_task_id); the decision is
    recorded in duplicate_decisions."""
    try:
        return merge_tasks(task_id, body.into_task_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/tasks/{task_id}/not-duplicate")
def keep_task_separate(task_id: str):
    """Mark a flagged possible-duplicate as genuinely different; clears the flag and records
    the decision in duplicate_decisions."""
    try:
        return dismiss_possible_duplicate(task_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/tasks/{task_id}/dependencies")
def add_dependency(task_id: str, body: DependencyRequest):
    """Record that task_id depends on body.depends_on_task_id."""
    if task_id == body.depends_on_task_id:
        raise HTTPException(status_code=400, detail="A task cannot depend on itself")
    add_task_dependency(task_id, body.depends_on_task_id)
    return {"task_id": task_id, "depends_on_task_id": body.depends_on_task_id, "status": "added"}


@app.delete("/tasks/{task_id}/dependencies/{depends_on_task_id}")
def remove_dependency(task_id: str, depends_on_task_id: str):
    """Remove a dependency edge between two tasks."""
    remove_task_dependency(task_id, depends_on_task_id)
    return {"task_id": task_id, "depends_on_task_id": depends_on_task_id, "status": "removed"}


# ---------------------------------------------------------------------------
# Registers: RAID log and RACI matrix
# ---------------------------------------------------------------------------

RaidType = Literal["risk", "assumption", "issue", "dependency"]
RaidStatus = Literal["open", "mitigating", "closed"]
RaidLevel = Literal["low", "medium", "high"]
RaidReviewState = Literal["suggested", "confirmed", "rejected"]


class RaidItemCreate(BaseModel):
    item_type: RaidType
    title: str
    description: Optional[str] = None
    owner: Optional[str] = None
    status: RaidStatus = "open"
    probability: Optional[RaidLevel] = None
    impact: Optional[RaidLevel] = None
    review_date: Optional[date] = None


class RaidItemPatch(BaseModel):
    item_type: Optional[RaidType] = None
    title: Optional[str] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[RaidStatus] = None
    probability: Optional[RaidLevel] = None
    impact: Optional[RaidLevel] = None
    review_date: Optional[date] = None
    review_state: Optional[RaidReviewState] = None


class TaskRaciRequest(BaseModel):
    person: str
    roles: List[Literal["R", "A", "C", "I"]]


@app.get("/projects/{project_id}/raid")
def get_raid_items(
    project_id: str,
    item_type: Optional[RaidType] = Query(None),
    status: Optional[RaidStatus] = Query(None),
    review_state: Optional[RaidReviewState] = Query(None),
):
    """The project's RAID log. Suggested (AI-proposed, not yet reviewed) items sort first."""
    return {"items": list_raid_items(project_id, item_type, status, review_state)}


@app.post("/projects/{project_id}/raid")
def add_raid_item(project_id: str, body: RaidItemCreate):
    try:
        return create_raid_item(project_id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class SuggestRaidRequest(BaseModel):
    force: bool = False  # re-read documents that were already scanned


@app.post("/projects/{project_id}/raid/suggest")
def suggest_raid_items(project_id: str, body: SuggestRaidRequest = SuggestRaidRequest()):
    """Ask the assistant to read the project's stored documents (only ones not scanned yet, unless force) and propose RAID
    items. They are saved as 'suggested' - not part of the log - until a person accepts them; items that restate one already
    in the log (any state, so rejections are remembered) are skipped. A document the assistant could not read is reported and
    left unscanned so the next click retries it."""
    docs = fetch_documents_to_scan(project_id, body.force)
    if not docs and not fetch_documents_to_scan(project_id, True):
        raise HTTPException(status_code=400, detail="No documents have been submitted for this project yet.")
    suggestions, scanned, failed = [], [], 0
    for doc in docs:
        kind = "Statement of Work" if doc["document_type"] == "sow" else "set of meeting minutes"
        try:
            suggestions.extend(suggest_raid_items_from_text(doc["content"], kind))
            scanned.append(doc["doc_id"])
        except RuntimeError:
            failed += 1
    if docs and not scanned:
        raise HTTPException(status_code=502, detail="The assistant could not read the documents right now - try again.")
    result = store_suggested_raid_items(project_id, suggestions)
    mark_documents_scanned(scanned)
    return {**result, "documents_scanned": len(scanned), "documents_failed": failed}


@app.patch("/raid/{item_id}")
def patch_raid_item(item_id: str, body: RaidItemPatch):
    fields = body.model_dump(exclude_unset=True)
    try:
        item = update_raid_item(item_id, fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if item is None:
        raise HTTPException(status_code=404, detail=f"No RAID item with id {item_id}")
    return item


@app.post("/raid/{item_id}/promote-to-issue")
def promote_raid_risk(item_id: str):
    """The risk happened: creates an Issue from it and closes the risk, linked to the new issue."""
    try:
        return promote_risk_to_issue(item_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/raid/{item_id}")
def remove_raid_item(item_id: str):
    if not delete_raid_item(item_id):
        raise HTTPException(status_code=404, detail=f"No RAID item with id {item_id}")
    return {"item_id": item_id, "status": "deleted"}


@app.get("/projects/{project_id}/raci")
def get_raci(project_id: str, subject: Literal["task", "deliverable"] = Query("task")):
    """People columns + one row per task (or per deliverable) with its R/A/C/I assignments and warnings."""
    return fetch_raci_matrix(project_id, subject)


@app.put("/tasks/{task_id}/raci")
def put_task_raci(task_id: str, body: TaskRaciRequest):
    """Replace one person's roles on one task (empty list clears them)."""
    try:
        roles = set_task_raci(task_id, body.person, body.roles)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"task_id": task_id, "person": body.person.strip(), "roles": roles}


# ---------------------------------------------------------------------------
# Deliverables register (manual-first; see DECISIONS.md "Deliverable-level RACI")
# ---------------------------------------------------------------------------

TaskStatus = Literal["not_started", "in_progress", "completed", "blocked", "on_hold"]


class DeliverableCreate(BaseModel):
    title: str
    number: Optional[str] = None
    owner: Optional[str] = None
    deadline: Optional[str] = None
    status: TaskStatus = "not_started"


class DeliverablePatch(BaseModel):
    title: Optional[str] = None
    number: Optional[str] = None
    owner: Optional[str] = None
    deadline: Optional[str] = None
    status: Optional[TaskStatus] = None
    review_state: Optional[Literal["suggested", "confirmed", "rejected"]] = None  # accept / reject an AI suggestion


class SuggestDeliverablesRequest(BaseModel):
    definition: Optional[str] = None  # overrides the default definition of "deliverable" for this call only


class TaskAssignment(BaseModel):
    task_id: str
    deliverable_id: Optional[str] = None


class AssignTasksRequest(BaseModel):
    assignments: List[TaskAssignment]


class TaskDeliverableRequest(BaseModel):
    deliverable_id: Optional[str] = None


class DeliverablesFromTasks(BaseModel):
    task_ids: List[str]


@app.get("/projects/{project_id}/deliverables")
def get_deliverables(project_id: str):
    return {"deliverables": list_deliverables(project_id)}


@app.post("/projects/{project_id}/deliverables")
def add_deliverable(project_id: str, body: DeliverableCreate):
    try:
        return create_deliverable(project_id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/projects/{project_id}/deliverables/from-tasks")
def promote_tasks_to_deliverables(project_id: str, body: DeliverablesFromTasks):
    """Promote extracted tasks to deliverables (the task stays a task). Already-promoted tasks are skipped."""
    try:
        return create_deliverables_from_tasks(project_id, body.task_ids)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/projects/{project_id}/deliverables/similar-tasks")
def get_tasks_similar_to_deliverables(project_id: str):
    """For the 'Add from tasks' picker: tasks that look like an existing deliverable. A similarity hint a person judges
    (related sub-tasks score as high as true restatements) - never a duplicate verdict."""
    return {"similar": find_tasks_similar_to_deliverables(project_id)}


@app.post("/projects/{project_id}/deliverables/suggest")
def suggest_deliverables(project_id: str, body: SuggestDeliverablesRequest = SuggestDeliverablesRequest()):
    """Ask the assistant to suggest deliverables from the project's latest SOW. They are saved as 'suggested' (not real
    deliverables, not RACI rows) until a person accepts them; ones that restate an existing deliverable, including a
    rejected suggestion, are skipped."""
    sow = fetch_latest_sow_text(project_id)
    if not sow:
        raise HTTPException(status_code=400, detail="No SOW has been submitted for this project yet.")
    try:
        suggestions = suggest_deliverables_from_text(sow, body.definition)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return store_suggested_deliverables(project_id, suggestions)


@app.post("/projects/{project_id}/deliverables/suggest-assignments")
def suggest_task_assignments(project_id: str):
    """Propose, for each unassigned task, which deliverable it contributes to. Nothing is saved - the proposals are
    reviewed and then applied with PUT /projects/{id}/deliverables/assign."""
    deliverables = list_deliverables(project_id, states=("confirmed",))
    if not deliverables:
        raise HTTPException(status_code=400, detail="Add or accept some deliverables first.")
    return {"proposals": propose_assignments(deliverables, fetch_unassigned_tasks(project_id))}


@app.put("/projects/{project_id}/deliverables/assign")
def assign_tasks(project_id: str, body: AssignTasksRequest):
    try:
        changed = assign_tasks_to_deliverables(project_id, [a.model_dump() for a in body.assignments])
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"changed": changed}


@app.put("/tasks/{task_id}/deliverable")
def put_task_deliverable(task_id: str, body: TaskDeliverableRequest):
    """Assign one task to a deliverable, or clear it with deliverable_id = null."""
    try:
        set_task_deliverable(task_id, body.deliverable_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"task_id": task_id, "deliverable_id": body.deliverable_id}


@app.patch("/deliverables/{deliverable_id}")
def patch_deliverable(deliverable_id: str, body: DeliverablePatch):
    try:
        row = update_deliverable(deliverable_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if row is None:
        raise HTTPException(status_code=404, detail=f"No deliverable with id {deliverable_id}")
    return row


@app.delete("/deliverables/{deliverable_id}")
def remove_deliverable(deliverable_id: str):
    if not delete_deliverable(deliverable_id):
        raise HTTPException(status_code=404, detail=f"No deliverable with id {deliverable_id}")
    return {"deliverable_id": deliverable_id, "status": "deleted"}


@app.put("/deliverables/{deliverable_id}/raci")
def put_deliverable_raci(deliverable_id: str, body: TaskRaciRequest):
    """Replace one person's roles on one deliverable (empty list clears them)."""
    try:
        roles = set_deliverable_raci(deliverable_id, body.person, body.roles)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"deliverable_id": deliverable_id, "person": body.person.strip(), "roles": roles}


# ---------------------------------------------------------------------------
# Hours: budget, estimates, weekly timesheet, availability, plan / timesheet import.
# The rules behind every number are in apps/utils/hours.py; the design is in DECISIONS.md ("Hours").
# ---------------------------------------------------------------------------

MAX_IMPORT_ROWS = 5000


class HoursSettingsRequest(BaseModel):
    week_start_day: Optional[int] = None  # 0 = Monday ... 6 = Sunday
    budget_hours: Optional[float] = None
    kickoff_date: Optional[date] = None
    role_budgets: Optional[dict] = None


class PlanTaskRequest(BaseModel):
    task_text: str
    owner: Optional[str] = None
    phase: Optional[str] = None
    estimate_hours: Optional[float] = None
    plan_start_week: Optional[int] = None
    plan_finish_week: Optional[int] = None


class TaskHoursRequest(BaseModel):
    phase: Optional[str] = None
    estimate_hours: Optional[float] = None
    plan_start_week: Optional[int] = None
    plan_finish_week: Optional[int] = None
    hours_still_needed: Optional[float] = None


class TimesheetEntry(BaseModel):
    task_id: Optional[str] = None  # None = "Other": meetings, admin, time on no listed task
    hours: float
    note: Optional[str] = None


class TimesheetSaveRequest(BaseModel):
    person: str
    week_start: date
    entries: List[TimesheetEntry]


class PersonCapacityRequest(BaseModel):
    weekly_capacity_hours: Optional[float] = None
    skills: Optional[List[str]] = None


class ImportDecision(BaseModel):
    idx: int
    task_id: Optional[str] = None  # timesheet: the task (None = Other); plan: the task to update
    person: Optional[str] = None
    action: Optional[Literal["update", "create", "skip"]] = None  # plan rows
    skip: bool = False  # timesheet rows


class ReassignRequest(BaseModel):
    label: Optional[str] = None
    person: str
    task_id: Optional[str] = None  # None = Other


class ConfirmImportRequest(BaseModel):
    decisions: List[ImportDecision] = []
    apply_budget: bool = True
    apply_kickoff: bool = True


@app.get("/projects/{project_id}/plan/summary")
def get_plan_summary(project_id: str):
    """What the Plan view's strip shows: whether a plan exists, the baseline and current totals, and tasks added or removed since."""
    return plan_summary(project_id)


@app.get("/projects/{project_id}/plan/changes")
def get_plan_changes(project_id: str, limit: int = Query(100, ge=1, le=1000)):
    return {"changes": list_plan_changes(project_id, limit)}


@app.post("/projects/{project_id}/plan/baseline")
def post_plan_baseline(project_id: str):
    """Freeze the plan as it stands as the baseline (once). A plan import does this by itself."""
    try:
        return set_plan_baseline(project_id)
    except ValueError as e:
        raise HTTPException(status_code=409 if "already" in str(e) else 400, detail=str(e))


@app.post("/projects/{project_id}/plan/tasks")
def post_plan_task(project_id: str, body: PlanTaskRequest):
    """Add a task to the plan by hand."""
    try:
        return create_plan_task(project_id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=409 if "already exists" in str(e) else 400, detail=str(e))


@app.delete("/tasks/{task_id}")
def delete_plan_task(task_id: str):
    """Take a task out of the plan. Its logged hours stay in the project total as hours not on a task."""
    try:
        return remove_plan_task(task_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/projects/{project_id}/hours")
def get_project_hours(project_id: str, as_of: Optional[date] = None):
    """Budget, hours logged, forecast, burn check and per-task hours for one project."""
    return project_hours_report(project_id, as_of)


@app.get("/hours/summary")
def get_hours_summary():
    """One row per project that has any hours data - powers the Dashboard's hours card."""
    rows = []
    for pid in project_ids_with_hours():
        r = project_hours_report(pid)
        rows.append({"project_id": pid, "budget_hours": r["budget_hours"], "logged_hours": r["logged_hours"],
                     "hours_left_in_budget": r["hours_left_in_budget"], "forecast_at_completion": r["forecast_at_completion"],
                     "status": r["burn_check"]["status"]})
    return {"projects": rows}


@app.get("/projects/{project_id}/hours/settings")
def get_project_hours_settings(project_id: str):
    return get_hours_settings(project_id)


@app.put("/projects/{project_id}/hours/settings")
def put_project_hours_settings(project_id: str, body: HoursSettingsRequest):
    """Set the hours budget, kickoff date and per-role budgets. Only the fields sent are changed (null clears one)."""
    try:
        return set_hours_settings(project_id, **{k: getattr(body, k) for k in body.model_fields_set})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/projects/{project_id}/hours/budget/suggest")
def suggest_project_hours_budget(project_id: str):
    """Look for a total hours budget in the project's latest SOW. Nothing is saved: the quote and number are shown and a
    person sets the budget with PUT .../hours/settings."""
    sow = fetch_latest_sow_text(project_id)
    if not sow:
        raise HTTPException(status_code=400, detail="No SOW has been submitted for this project yet.")
    try:
        return suggest_budget_hours(sow)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.put("/tasks/{task_id}/hours")
def put_task_hours(task_id: str, body: TaskHoursRequest):
    """Set a task's estimate, planned start/finish weeks and 'hours still needed'. Only the fields sent are changed."""
    try:
        return set_task_hours(task_id, **{k: getattr(body, k) for k in body.model_fields_set})
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/projects/{project_id}/timesheet")
def get_timesheet(project_id: str, person: str = Query(...), week: date = Query(...)):
    """One person's week: every active task with its estimate, hours logged so far by everyone, and this person's hours this week."""
    wk = _week_start(week, get_hours_settings(project_id)["week_start_day"])
    logged = {}
    for e in fetch_time_entries(project_id):
        if e["task_id"]:
            logged[e["task_id"]] = logged.get(e["task_id"], 0.0) + e["hours"]
    mine = {e["task_id"]: e for e in get_timesheet_week(project_id, person, wk)}
    tasks = []
    for t in fetch_active_tasks_for_hours(project_id):
        est = None if t["estimate_hours"] is None else float(t["estimate_hours"])
        total = round(logged.get(t["task_id"], 0.0), 2)
        tasks.append({
            "task_id": t["task_id"], "task_text": t["task_text"], "owner": t["owner"], "status": t["status"],
            "estimate_hours": est, "logged_hours": total,
            "hours_still_needed": None if t["hours_still_needed"] is None else float(t["hours_still_needed"]),
            "hours_this_week": mine[t["task_id"]]["hours"] if t["task_id"] in mine else 0,
            "note": mine[t["task_id"]]["note"] if t["task_id"] in mine else None,
        })
    other = mine.get(None)
    return {"person": person, "week_start": wk.isoformat(), "tasks": tasks,
            "other_hours": other["hours"] if other else 0, "other_note": other["note"] if other else None}


@app.put("/projects/{project_id}/timesheet")
def put_timesheet(project_id: str, body: TimesheetSaveRequest):
    """Replace everything one person logged for one week."""
    try:
        saved = save_timesheet_week(project_id, body.person, body.week_start, [e.model_dump() for e in body.entries])
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"week_start": _week_start(body.week_start, get_hours_settings(project_id)["week_start_day"]).isoformat(), "saved": saved}


@app.put("/projects/{project_id}/people/{name}/capacity")
def put_person_capacity(project_id: str, name: str, body: PersonCapacityRequest):
    """Set a person's weekly capacity (hours) and skills for this project. Only the fields sent are changed."""
    try:
        set_person_capacity(project_id, name, **{k: getattr(body, k) for k in body.model_fields_set})
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"people": list_project_people(project_id)}


@app.get("/projects/{project_id}/availability")
def get_availability(project_id: str, from_date: Optional[date] = Query(None, alias="from"),
                     to_date: Optional[date] = Query(None, alias="to"), skill: Optional[str] = None):
    """Free hours per person in a window (default: the next four weeks), optionally for people whose role or skills match."""
    start = from_date or date.today()
    end = to_date or start + timedelta(days=27)
    if end < start or (end - start).days > 366:
        raise HTTPException(status_code=400, detail="The window must end after it starts and be at most a year long.")
    return availability_report(project_id, start, end, skill)


def _preview_timesheet(project_id: str, filename: str, content: bytes, mapping_json: Optional[str]) -> dict:
    return timesheet_service.preview_timesheet(project_id, filename, content, mapping_json)


def _preview_plan(project_id: str, filename: str, content: bytes) -> dict:
    parsed = hours_import.parse_plan(filename, content)
    tasks = fetch_active_tasks_for_hours(project_id)
    rows = parsed["rows"]
    matches = hours_import.propose_task_matches([{"label": r["label"], "person": r["owner"]} for r in rows], tasks, mode="plan")
    for idx, (r, m) in enumerate(zip(rows, matches)):
        matched = m["match"] in ("exact", "suggested", "llm")
        r.update(idx=idx, task_id=m["task_id"], task_text=m["task_text"], match=m["match"], score=m["score"],
                 action="skip" if r["estimate"] is None else ("update" if matched else "create"))
    current = get_hours_settings(project_id)
    new = {"budget_hours": parsed["budget_hours"], "role_budgets": parsed["role_budgets"],
           "kickoff_date": parsed["kickoff_date"].isoformat() if parsed["kickoff_date"] else None}
    payload = {"rows": rows, "settings": new}
    import_id = create_import_record(project_id, "plan", filename, "plan", payload)
    return {"import_id": import_id, "kind": "plan", "layout": "plan", "warnings": parsed["warnings"], "rows": rows,
            "settings_new": new,
            "settings_current": {"budget_hours": current["budget_hours"], "role_budgets": current["role_budgets"],
                                 "kickoff_date": current["kickoff_date"].isoformat() if current["kickoff_date"] else None},
            "summary": {"rows": len(rows), "estimated_hours": round(sum(r["estimate"] or 0 for r in rows), 2),
                        "to_update": sum(r["action"] == "update" for r in rows), "to_create": sum(r["action"] == "create" for r in rows)},
            "tasks": [{"task_id": t["task_id"], "task_text": t["task_text"]} for t in tasks]}


@app.post("/projects/{project_id}/hours/import/preview")
def preview_hours_import(project_id: str, kind: Literal["timesheet", "plan"] = Form(...), file: UploadFile = File(...),
                         mapping: Optional[str] = Form(None)):
    """Read a plan or timesheet file and return what WOULD be imported, matched to the project's people and tasks. Nothing is
    changed until POST /hours/imports/{id}/confirm. `mapping` ({"person": "Employee", ...}) overrides column detection."""
    content = file.file.read()
    if kind == "plan" and plan_has_content(project_id):
        raise HTTPException(status_code=409, detail="This project already has a plan. Edit it in Workspace, Plan; a plan is only uploaded the first time.")
    try:
        if kind == "timesheet":
            return _preview_timesheet(project_id, file.filename or "upload", content, mapping)
        return _preview_plan(project_id, file.filename or "upload", content)
    except hours_import.HoursImportError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="The column mapping was not valid JSON.")


@app.post("/hours/imports/{import_id}/confirm")
def confirm_hours_import(import_id: str, body: ConfirmImportRequest):
    """Apply a previewed import with the user's changes. Re-importing the same entries REPLACES them, never adds."""
    imp = get_import(import_id)
    if not imp:
        raise HTTPException(status_code=404, detail="No such import.")
    project_id, payload = imp["project_id"], imp["payload"]
    decisions = {d.idx: d for d in body.decisions}
    valid_tasks = {t["task_id"] for t in fetch_active_tasks_for_hours(project_id)}
    try:
        if imp["kind"] == "timesheet":
            result = timesheet_service.confirm_timesheet(import_id, {
                d.idx: {"skip": d.skip, "person": d.person, "has_task": "task_id" in d.model_fields_set, "task_id": d.task_id}
                for d in body.decisions})
            timesheet_watcher.finish_review(import_id)
            return result
        items = []
        for r in payload["rows"]:
            d = decisions.get(r["idx"])
            action = d.action if d and d.action else r["action"]
            if action == "skip" or r["estimate"] is None:
                continue
            if action == "update":
                task_id = d.task_id if d and d.task_id else r["task_id"]
                if not task_id or task_id not in valid_tasks:
                    continue
                items.append({"action": "update", "task_id": task_id, "estimate_hours": r["estimate"],
                              "plan_start_week": r["start_wk"], "plan_finish_week": r["finish_wk"], "phase": r.get("phase")})
            else:
                items.append({"action": "create", "task_text": r["label"], "owner": r["owner"], "estimate_hours": r["estimate"],
                              "plan_start_week": r["start_wk"], "plan_finish_week": r["finish_wk"], "phase": r.get("phase")})
        new, settings_update = payload["settings"], {}
        if body.apply_budget and new.get("budget_hours") is not None:
            settings_update["budget_hours"] = new["budget_hours"]
        if body.apply_budget and new.get("role_budgets"):
            settings_update["role_budgets"] = new["role_budgets"]
        if body.apply_kickoff and new.get("kickoff_date"):
            settings_update["kickoff_date"] = date.fromisoformat(new["kickoff_date"])
        return apply_plan_import(import_id, items, settings_update)
    except timesheet_service.BadChoice as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/hours/imports/{import_id}/undo")
def undo_hours_import(import_id: str):
    try:
        return undo_import(import_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/projects/{project_id}/timesheet-inbox")
def get_timesheet_inbox(project_id: str):
    """The Incoming timesheets card: the folder, when it was last checked, the files picked up from it, and the lines whose task was only
    guessed ("check these matches")."""
    items, to_check = [], []
    for r in list_folder_imports(project_id):
        s = r["summary"] or {}
        items.append({"import_id": r["import_id"], "filename": r["filename"], "status": r["status"], "reason": r["reason"],
                      "hours": s.get("hours_total"), "entries": s.get("entries_written"), "replaced": s.get("replaced"),
                      "to_check_count": len(s.get("to_check", [])) if r["status"] == "applied" else 0,
                      "can_review": r["status"] == "held" and bool(r["has_rows"]),
                      "created_at": r["created_at"], "applied_at": r["applied_at"]})
        if r["status"] == "applied":
            to_check += [{"import_id": r["import_id"], "filename": r["filename"], **c} for c in s.get("to_check", [])]
    applied = last_applied_at(project_id)
    return {"enabled": timesheet_watcher.STATUS["enabled"], "folder": f"timesheets/{project_id}",
            "poll_seconds": timesheet_watcher.STATUS["poll_seconds"], "last_checked_at": timesheet_watcher.STATUS["last_checked_at"],
            "last_applied_at": applied.isoformat() if applied else None, "items": items, "to_check": to_check,
            "tasks": [{"task_id": t["task_id"], "task_text": t["task_text"]} for t in fetch_active_tasks_for_hours(project_id)]}


@app.get("/hours/imports/{import_id}/review")
def get_import_review(import_id: str):
    """A held file's stored preview, in the shape the review screen takes."""
    try:
        return timesheet_service.review_payload(import_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/hours/imports/{import_id}/reassign")
def reassign_hours(import_id: str, body: ReassignRequest):
    """Move one group of an imported file's hours to another task (or Other). Totals do not change; the import can still be undone."""
    try:
        return reassign_import_group(import_id, body.label, body.person, body.task_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/projects/{project_id}/hours/imports")
def get_hours_imports(project_id: str):
    return {"imports": list_imports(project_id)}


@app.get("/chat/history")
def get_chat_history(session_id: str = Query(...)):
    """Prior turns for one chat session, oldest first - lets the frontend restore the
    visible transcript on page load instead of always starting blank."""
    return {"messages": fetch_chat_history(session_id)}


@app.post("/chat")
def chat(request: ChatRequest):
    """Read-only Q&A over the caller's own project/task data. No auth or user identity
    yet - session_id (client-generated) is the only thing scoping conversation history."""
    history = [{"role": m["role"], "content": m["content"]} for m in fetch_chat_history(request.session_id)]
    reply_text, trace = run_chat_agent(history, request.message)

    insert_chat_message(request.session_id, "user", request.message)
    insert_chat_message(request.session_id, "assistant", reply_text)

    return {"text": reply_text, "trace": trace}
