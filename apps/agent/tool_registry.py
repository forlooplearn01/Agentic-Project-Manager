"""Per-agent tool registries - schemas (OpenAI function-calling format) paired with the
Python callables they invoke. Deliberately NOT one shared registry: each agent's registry
only contains the tools its manifest declares, so an agent can never call a tool outside
its own scope (Planning never touches Jira/report tools, Sync/Reporting never touches the
calendar, etc. - see the manifests in apps/agent/manifests/)."""

import re
import json
from datetime import date, timedelta

from mcp_server.tools.postgres import search_tasks, get_task_by_id, list_projects
from mcp_server.tools.calendar import check_owner_availability, resolve_attendees, propose_meeting
from mcp_server.tools.jira import sync_task_to_jira
from mcp_server.tools.report import generate_report
from apps.db.hours_db import availability_report, project_hours_report
from apps.utils import clock
from apps.db.plan_db import list_plan_changes, plan_summary
from apps.db.db import (
    list_deliverables,
    list_raid_items,
    fetch_project_tasks_with_dependencies,
    add_task_dependency,
    fetch_projects,
    fetch_owner_workload,
    fetch_dependencies_for_project,
    fetch_recent_agent_activity,
    list_project_people,
    list_meetings,
    search_document_chunks,
)


def _record_dependency(task_id: str, depends_on_task_id: str) -> str:
    add_task_dependency(task_id, depends_on_task_id)
    return json.dumps({"status": "recorded", "task_id": task_id, "depends_on_task_id": depends_on_task_id})


# ---------------------------------------------------------------------------
# Planning agent
# ---------------------------------------------------------------------------

PLANNING_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "check_owner_availability",
            "description": "Check Google Calendar availability for a task owner around a deadline. "
                            "Use this to ground a deadline recommendation instead of guessing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "The task owner's name or email"},
                    "deadline": {"type": "string", "description": "Proposed deadline, YYYY-MM-DD if known"},
                },
                "required": ["owner"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_tasks",
            "description": "Search active tasks in this project to check dependency order and "
                            "avoid ranking a prerequisite lower than what it unblocks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "owner": {"type": "string"},
                    "status": {"type": "string"},
                    "project_id": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task_by_id",
            "description": "Fetch full detail on a specific task found via search_tasks.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_dependency",
            "description": "Record that task_id depends on depends_on_task_id, once genuinely "
                            "identified - not guessed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "depends_on_task_id": {"type": "string"},
                },
                "required": ["task_id", "depends_on_task_id"],
            },
        },
    },
]

PLANNING_TOOL_IMPLS = {
    "check_owner_availability": check_owner_availability,
    "search_tasks": search_tasks,
    "get_task_by_id": get_task_by_id,
    "record_dependency": _record_dependency,
}


# ---------------------------------------------------------------------------
# Sync/Reporting agent
# ---------------------------------------------------------------------------

SYNC_REPORTING_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "sync_task_to_jira",
            "description": "Push a task to Jira as a new issue. Only for tasks new this run or "
                            "whose status/owner/deadline changed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "task_text": {"type": "string"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "assignee": {"type": "string"},
                },
                "required": ["task_id", "task_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": "Regenerate the task export for this project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": ["markdown", "json", "excel"]},
                    "project_id": {"type": "string"},
                },
                "required": ["format", "project_id"],
            },
        },
    },
]

def build_sync_reporting_impls(project_id: str) -> dict:
    """project_id is bound here (closure), not exposed as an LLM-supplied parameter on
    sync_task_to_jira's schema above - the model deciding to sync a task shouldn't also
    be trusted to correctly echo back which project it's syncing for real writes to an
    external system; the run already knows this deterministically."""
    return {
        "sync_task_to_jira": lambda **kwargs: sync_task_to_jira(project_id=project_id, **kwargs),
        "generate_report": generate_report,
    }


# ---------------------------------------------------------------------------
# Meeting Coordinator agent
# ---------------------------------------------------------------------------

MEETING_COORDINATOR_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "resolve_attendees",
            "description": "Resolve a list of person names to email addresses for this project. "
                            "Names that don't resolve come back separately - never guess an address.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "names": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["project_id", "names"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_owner_availability",
            "description": "Check Google Calendar availability for an attendee around a proposed "
                            "meeting time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "deadline": {"type": "string"},
                },
                "required": ["owner"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_meeting",
            "description": "Stage the meeting once attendees and a time are settled - this does NOT "
                            "create the calendar event or notify anyone. A person reviews and approves "
                            "the proposal in the UI; only that approval actually creates the event and "
                            "sends invites. This is the only way this agent can act on a decision to "
                            "schedule a meeting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "attendee_emails": {"type": "array", "items": {"type": "string"}},
                    "start_iso": {"type": "string", "description": "ISO 8601 datetime"},
                    "end_iso": {"type": "string", "description": "ISO 8601 datetime"},
                    "description": {"type": "string"},
                },
                "required": ["title", "attendee_emails", "start_iso", "end_iso"],
            },
        },
    },
]

MEETING_COORDINATOR_TOOL_IMPLS = {
    "resolve_attendees": resolve_attendees,
    "check_owner_availability": check_owner_availability,
    "propose_meeting": propose_meeting,
}


# ---------------------------------------------------------------------------
# Chat agent - read-only. Every tool here only queries Postgres; none of them
# create, update, or sync anything, matching the chat agent's read-only scope.
# ---------------------------------------------------------------------------

def _get_project_overview(project_id: str = None) -> list:
    projects = fetch_projects()
    if project_id:
        return [p for p in projects if p["project_id"] == project_id]
    return projects


def _get_recent_activity(project_id: str = None, limit: int = 10) -> list:
    return fetch_recent_agent_activity(project_id=project_id, limit=limit)


def _get_project_hours(project_id: str, task_query: str = None) -> dict:
    """The Hours page's numbers for one project. Per-task rows are limited to tasks matching task_query, or else to the tasks
    that need attention (open and over their estimate, or open with no estimate), so the reply stays short."""
    report = project_hours_report(project_id)
    tasks = report.pop("tasks")
    if task_query:
        words = [w for w in task_query.lower().split() if len(w) > 2]
        tasks = [t for t in tasks if any(w in t["task_text"].lower() or w in (t["owner"] or "").lower() for w in words)]
    else:
        tasks = [t for t in tasks if t["status"] != "completed" and (t["remaining_hours"] is None or t["over_estimate_by"])]
    report["tasks"] = tasks[:25]
    return report


def _get_project_dependencies(project_id: str) -> list:
    """Dependency edges with task names, owners and statuses, so the answer never has to show an id: 'task X depends on task Y (Y's status)'."""
    tasks = fetch_project_tasks_with_dependencies(project_id)
    by_id = {t["task_id"]: t for t in tasks}
    edges = []
    for t in tasks:
        for d in t["depends_on"]:
            other = by_id.get(d["task_id"], {})
            edges.append({"task": t["task_text"], "task_owner": t["owner"], "task_status": t["status"],
                          "depends_on": d["task_text"], "depends_on_owner": other.get("owner"), "depends_on_status": other.get("status")})
    return edges


def _get_project_risks(project_id: str, item_type: str = None, status: str = None) -> dict:
    """The project's RAID log (risks, assumptions, issues, dependencies) as the PMO has confirmed it, plus how many more are only suggested."""
    rows = list_raid_items(project_id, item_type=item_type, status=status)
    keep = ("item_type", "title", "description", "owner", "status", "probability", "impact", "evidence")
    confirmed = [{k: r[k] for k in keep} for r in rows if r["review_state"] == "confirmed"]
    return {"items": confirmed, "count": len(confirmed), "suggested_not_yet_reviewed": sum(1 for r in rows if r["review_state"] == "suggested"),
            "note": "Only confirmed items are listed; suggested ones are waiting for the PMO's review in Registers."}


def _get_project_deliverables(project_id: str) -> dict:
    """Deliverables with number, owner, deadline, status, the number of tasks behind each, and who is Responsible / Accountable / Consulted / Informed."""
    from apps.db.db import get_connection
    rows = [d for d in list_deliverables(project_id, states=("confirmed",))]
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT deliverable_id, person, role FROM deliverable_raci WHERE deliverable_id = ANY(%s::uuid[])", ([str(d["deliverable_id"]) for d in rows],))
        raci = {}
        for did, person, role in cur.fetchall():
            raci.setdefault(str(did), {}).setdefault(role, []).append(person)
    conn.close()
    names = {"R": "responsible", "A": "accountable", "C": "consulted", "I": "informed"}
    items = [{"number": d["number"], "title": d["title"], "owner": d["owner"], "deadline": d["deadline"], "status": d["status"], "tasks_behind_it": d["task_count"],
              "raci": {names[r]: ", ".join(p) for r, p in raci.get(str(d["deliverable_id"]), {}).items()}} for d in rows]
    return {"deliverables": items, "count": len(items)}


def _plan_value(field: str, value):
    """A history value with its unit, so the answer cannot call a week number 'hours'."""
    if value in (None, ""):
        return None
    if field in ("estimate_hours", "hours_still_needed", "budget_hours"):
        return f"{value} h"
    if field in ("plan_start_week", "plan_finish_week"):
        return f"week {value}"
    return value


def _get_plan_changes(project_id: str, limit: int = 15) -> dict:
    """The plan against its baseline plus what changed AFTER the baseline was set (the import that created the plan is not a change)."""
    from datetime import datetime
    summary = plan_summary(project_id)
    baseline_at = datetime.fromisoformat(summary["baseline_set_at"]) if summary["baseline_set_at"] else None
    changes = []
    for c in list_plan_changes(project_id, 200):
        if baseline_at and (c["changed_at"] <= baseline_at or c["field"] in ("baseline_set", "plan_imported")):
            continue
        changes.append({"when": c["changed_at"].date().isoformat(), "change": c["label"], "task": c["task_text"],
                        "from": _plan_value(c["field"], c["old_value"]), "to": _plan_value(c["field"], c["new_value"])})
    return {"summary": summary, "changes_since_baseline": changes[:min(max(limit, 1), 50)] if baseline_at else [],
            "all_changes": [] if baseline_at else changes[:min(max(limit, 1), 50)]}


def _find_available_people(project_id: str, skill: str = None, from_date: str = None, to_date: str = None) -> dict:
    try:
        start = date.fromisoformat(from_date) if from_date else clock.today()
        end = date.fromisoformat(to_date) if to_date else start + timedelta(days=27)
    except ValueError:
        return {"error": "Dates must be YYYY-MM-DD."}
    if end < start:
        return {"error": "to_date is before from_date."}
    return availability_report(project_id, start, end, skill)


CHAT_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_projects",
            "description": "List every project id that has tasks.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_overview",
            "description": "Rollup stats (total/open/blocked/completed/in_progress/not_started/high-priority task counts, "
                            "jira_synced_tasks, jira_unsynced_tasks, unique owners, last updated, "
                            "jira_project_key) for one project, or every project in a single call if "
                            "project_id is omitted - prefer omitting it over calling this once per "
                            "project for cross-project questions. jira_project_key is null if Jira "
                            "sync was never configured for that project. For ANY counting question "
                            "(how many are X, how many aren't Y) use these counts, not search_tasks - "
                            "search_tasks caps at 20 results with no signal that more exist, so "
                            "counting its results undercounts silently on any project past 20 tasks.",
            "parameters": {
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_tasks",
            "description": "Search tasks by text, owner, status, and/or project. All filters "
                            "optional - status 'blocked' finds blocked tasks, omit query/owner/status "
                            "to list a project's recent tasks. Each result includes jira_issue_key "
                            "(null if that task hasn't synced to Jira). Capped at 20 results with no "
                            "indication more exist - never count these results to answer a 'how many' "
                            "question, use get_project_overview or get_owner_workload for counts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "owner": {"type": "string"},
                    "status": {"type": "string", "enum": ["not_started", "in_progress", "completed", "blocked", "on_hold"]},
                    "project_id": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task_by_id",
            "description": "Fetch full detail on one task found via search_tasks, including its "
                            "blocker text and jira_issue_key (null if not synced to Jira).",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_owner_workload",
            "description": "Per-owner task counts (total, open, blocked, high-priority) - use this "
                            "for 'who's overloaded' or 'what does X have on their plate' questions "
                            "instead of counting search_tasks results by hand.",
            "parameters": {
                "type": "object",
                "properties": {"project_id": {"type": "string", "description": "Omit to aggregate across every project"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_hours",
            "description": "Hours for one project, computed by the app: the hours budget, hours logged, hours left in the budget, "
                            "forecast at completion (logged + remaining on open tasks), budget minus forecast, a burn check "
                            "(budget used vs work done vs work due by now, status on_track / watch / at_risk), recent weekly burn, "
                            "hours logged by person and week, hours not tied to any task, open tasks with no estimate, open tasks over "
                            "their estimate, and a 'notes' list of caveats. Use it for 'how many hours are left', 'are we over "
                            "budget', 'how many hours has X logged', 'how many hours are left on <task>' (pass task_query). Report "
                            "the numbers as returned and always mention the relevant 'notes' (for example a missing budget or tasks "
                            "without estimates); never calculate your own totals.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "task_query": {"type": "string", "description": "Only return per-task rows whose task text or owner matches these words"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_plan_changes",
            "description": "How the project plan has changed since its baseline (the plan as first agreed): baseline hours vs current hours "
                            "(change_hours), tasks added and removed since, and the changes made after the baseline with dates (estimate or week changes "
                            "with from -> to and their units, tasks added or removed, budget or kickoff edits). Use it for 'has the plan changed', 'what changed "
                            "since the start', 'how much has scope grown'. baseline_set_at is null when no baseline exists. The history says "
                            "when and what, never who.",
            "parameters": {
                "type": "object",
                "properties": {"project_id": {"type": "string"}, "limit": {"type": "integer", "description": "How many recent changes, default 15"}},
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_available_people",
            "description": "Free hours per person for a date window (default: the next four weeks): weekly capacity for the working "
                            "days in the window minus the remaining hours of their open tasks spread to each task's due date. "
                            "Optionally filter by skill or role (for example 'backend', 'QA', 'infrastructure') - it matches each "
                            "person's role and skill tags, so try the closest role words. People with no capacity set have unknown "
                            "free hours, and tasks without an estimate or due date are listed per person as unknown; say so.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "skill": {"type": "string", "description": "Role or skill words to match, e.g. 'backend engineer'"},
                    "from_date": {"type": "string", "description": "YYYY-MM-DD, default today"},
                    "to_date": {"type": "string", "description": "YYYY-MM-DD, default four weeks after from_date"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_dependencies",
            "description": "The dependency graph for a project as a list of {task, task_status, depends_on, depends_on_status, owners} - which task waits on which, by NAME with both statuses. Use it for 'what does X depend on' and 'what is X blocking' questions and report the names and statuses, never task ids.",
            "parameters": {
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_risks",
            "description": "The project's RAID log: risks, issues, dependencies and assumptions the PMO has confirmed, each with title, owner, status (open / mitigating / closed), probability, impact and the evidence it came from. Use it for 'what are the risks', 'what is blocking us', 'which assumptions does the plan rest on'. item_type narrows it to risk, issue, dependency or assumption.",
            "parameters": {
                "type": "object",
                "properties": {"project_id": {"type": "string"}, "item_type": {"type": "string", "enum": ["risk", "issue", "dependency", "assumption"]},
                               "status": {"type": "string", "enum": ["open", "mitigating", "closed"]}},
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_deliverables",
            "description": "The project's deliverables: number, title, owner, deadline, status, how many tasks sit behind each, and the RACI (responsible, accountable, consulted, informed). Use it for 'what are the deliverables', 'what is due', 'who is accountable for deliverable X'.",
            "parameters": {"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_activity",
            "description": "Recent agent actions (Planning, Sync/Reporting, Meeting Coordinator) for "
                            "a project, or across all projects if project_id is omitted.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "limit": {"type": "integer", "description": "Default 10"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_people",
            "description": "Every known person for a project - name, email, role. A null email means "
                            "the person was auto-added as a task owner but hasn't been resolved to a "
                            "real address yet (say so plainly rather than guessing one). Use this for "
                            "any question about who's on a project, their role, contact info, or team "
                            "roster - never infer a role or email from a name alone.",
            "parameters": {
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_meetings",
            "description": "Meetings for a project - title, attendees, time, and status "
                            "(pending_approval, scheduled, or rejected). Optionally filter by status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending_approval", "scheduled", "rejected"]},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": "Semantic search over the actual SOW/MoM text submitted for a project - "
                            "scope narrative, commercial terms, meeting discussion, or any other "
                            "context that never became a structured task/field. Use this ONLY for "
                            "content the other tools can't answer (they cover structured facts - "
                            "status, owner, deadline, role, etc. - more reliably than this can). "
                            "Returns the closest-matching excerpts, not a guaranteed exact match - "
                            "this is retrieval, not a lookup, and can miss the right passage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "query": {"type": "string", "description": "What to search for, in natural language"},
                },
                "required": ["project_id", "query"],
            },
        },
    },
]

CHAT_TOOL_IMPLS = {
    "list_projects": list_projects,
    "get_project_overview": _get_project_overview,
    "search_tasks": search_tasks,
    "get_task_by_id": get_task_by_id,
    "get_owner_workload": fetch_owner_workload,
    "get_project_hours": _get_project_hours,
    "find_available_people": _find_available_people,
    "get_plan_changes": _get_plan_changes,
    "get_project_dependencies": _get_project_dependencies,
    "get_project_risks": _get_project_risks,
    "get_project_deliverables": _get_project_deliverables,
    "get_recent_activity": _get_recent_activity,
    "get_project_people": list_project_people,
    "get_project_meetings": list_meetings,
    "search_documents": search_document_chunks,
}


def _known_project_ids() -> list:
    from apps.db.db import get_connection
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT project_id FROM tasks WHERE is_active ORDER BY 1")
        ids = [r[0] for r in cur.fetchall()]
    conn.close()
    return ids


def resolve_project_id(given):
    """A project id as the user probably meant it. Exact (any case) first; then the one known id that is inside what was given or contains it
    ("the halvane portal", "halvane-portal" -> halvane). None when nothing, or more than one id, fits."""
    if not given:
        return None
    known, low = _known_project_ids(), str(given).strip().lower()
    if low in [k.lower() for k in known]:
        return next(k for k in known if k.lower() == low)
    words = {w for w in re.split(r"[^a-z0-9]+", low) if w}
    near = [k for k in known if k.lower() in low or low in k.lower() or k.lower() in words]
    return near[0] if len(near) == 1 else None


def _forgiving(fn):
    """Wraps a chat tool that takes a project_id: a loosely written one is corrected; an unknown one gets the list of real projects instead of
    an empty answer the model would read as 'nothing there'."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if "project_id" in kwargs and kwargs["project_id"] is not None:
            real = resolve_project_id(kwargs["project_id"])
            if real is None:
                return {"error": f"There is no project with the id '{kwargs['project_id']}'.", "known_projects": _known_project_ids()}
            kwargs["project_id"] = real
        return fn(*args, **kwargs)
    return wrapper


import inspect  # noqa: E402

for _name, _fn in list(CHAT_TOOL_IMPLS.items()):
    if "project_id" in inspect.signature(_fn).parameters:
        CHAT_TOOL_IMPLS[_name] = _forgiving(_fn)
