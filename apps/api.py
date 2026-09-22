import concurrent.futures
import hashlib
import json
import threading
import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from apps.agent.chat import run_chat_agent
from apps.graph import build_graph
from apps.db.db import (
    add_task_dependency,
    approve_meeting_record,
    cache_pipeline_result,
    cleanup_old_jobs,
    complete_job,
    create_job,
    delete_project,
    ensure_jobs_table,
    ensure_meetings_table,
    ensure_source_documents_project_column,
    ensure_tasks_jira_column,
    fail_job,
    fetch_chat_history,
    fetch_project_tasks_with_dependencies,
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
    """Manually schedule a meeting from Task Manager, bypassing the Meeting Coordinator
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
    """On-demand Excel export of a project's current tasks - calls the same
    generate_report the Sync/Reporting agent uses, but directly (no LLM reasoning
    needed for a deterministic "export what's there right now" action), and streams
    the file back so a plain <a href download> link in the browser just works."""
    raw = generate_report(format="excel", project_id=project_id)
    data = json.loads(raw)

    if "error" in data:
        raise HTTPException(status_code=500, detail=data["error"])
    if data.get("tasks_count", 0) == 0:
        raise HTTPException(status_code=404, detail=f"No tasks found for project '{project_id}'")

    return FileResponse(
        data["file"],
        filename=f"{project_id}-tasks.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
