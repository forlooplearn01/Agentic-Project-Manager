from apps.agent.meeting_coordinator import run_meeting_coordinator_agent
from apps.logger import get_logger
from apps.state import AgentState

logger = get_logger("MEETING_COORDINATOR_NODE")


def task_meeting_coordinator_node(state: AgentState) -> AgentState:
    logger.info("=" * 60)
    logger.info("Running Meeting Coordinator Node")

    # MoM path only, and only when normalize's cheap pre-filter fired.
    if state.get("document_type") != "mom" or not state.get("followup_signal"):
        state["meeting_coordinator_triggered"] = False
        state["meeting_coordinator_trace"] = []
        state["scheduled_meeting"] = None
        return state

    result, trace = run_meeting_coordinator_agent(
        state.get("raw_input", ""), state.get("project_id", "default")
    )

    state["meeting_coordinator_triggered"] = True
    state["meeting_coordinator_trace"] = trace
    # The agent can override the pre-filter and decide no meeting is actually warranted -
    # scheduled_meeting stays null in that case even though the node ran.
    state["scheduled_meeting"] = result.get("scheduled_meeting") if result.get("followup_warranted") else None
    return state
