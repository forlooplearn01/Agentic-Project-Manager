"""Per-agent tool registries - schemas (OpenAI function-calling format) paired with the
Python callables they invoke. Deliberately NOT one shared registry: each agent's registry
only contains the tools its manifest declares, so an agent can never call a tool outside
its own scope (Planning never touches Jira/report tools, Sync/Reporting never touches the
calendar, etc. - see the manifests in apps/agent/manifests/)."""

import json

from mcp_server.tools.postgres import search_tasks, get_task_by_id, list_projects
from mcp_server.tools.calendar import check_owner_availability, resolve_attendees, propose_meeting
from mcp_server.tools.jira import sync_task_to_jira
from mcp_server.tools.report import generate_report
from apps.db.db import (
    add_task_dependency,
    fetch_projects,
    fetch_owner_workload,
    fetch_dependencies_for_project,
    fetch_recent_agent_activity,
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
            "description": "Rollup stats (total/open/blocked/completed/high-priority task counts, "
                            "unique owners, last updated) for one project, or every project in a "
                            "single call if project_id is omitted - prefer omitting it over calling "
                            "this once per project for cross-project questions.",
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
                            "to list a project's recent tasks.",
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
            "description": "Fetch full detail on one task found via search_tasks, including its blocker text.",
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
            "name": "get_project_dependencies",
            "description": "The full dependency graph for a project - which tasks depend on which.",
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
]

CHAT_TOOL_IMPLS = {
    "list_projects": list_projects,
    "get_project_overview": _get_project_overview,
    "search_tasks": search_tasks,
    "get_task_by_id": get_task_by_id,
    "get_owner_workload": fetch_owner_workload,
    "get_project_dependencies": fetch_dependencies_for_project,
    "get_recent_activity": _get_recent_activity,
}
