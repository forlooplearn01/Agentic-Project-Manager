from apps.logger import get_logger
from apps.state import AgentState

logger = get_logger("REDIRECT")


def route_after_supervisor(state: AgentState) -> str:
    """The Extractor page takes an SOW. Meeting minutes are not run through it any more (they would create tasks and Jira issues without
    the PMO's approval): when the run came from there (sow_only) and the Supervisor says it is minutes, the run stops here."""
    return "redirect" if state.get("sow_only") and state.get("document_type") == "mom" else "extract"


def task_redirect_node(state: AgentState) -> AgentState:
    project_id = state.get("project_id", "default")
    logger.info(f"{project_id}: document looks like meeting minutes, not an SOW; nothing was created")
    state["final_output"] = {
        "tasks_count": 0, "tasks": [], "event_signals": [], "unresolved_events": [], "planning_trace": [], "status": "redirected",
        "message": ("This looks like meeting minutes, not an SOW (the project already has tasks and the text does not read like a statement of work), "
                    f"so nothing was created. Put meeting transcripts in projects/{project_id}/meetings/ : the tasks in them come back under "
                    "Workspace > Suggestions for the PMO to approve."),
    }
    return state
