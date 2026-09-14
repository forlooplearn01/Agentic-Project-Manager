from apps.logger import get_logger
from apps.state import AgentState

logger = get_logger("FINALIZE")


def task_finalize_node(state: AgentState) -> AgentState:
    # Runs last, after sync_reporting and meeting_coordinator - merges their results into
    # final_output, which format() built earlier (before real task_ids existed for the
    # tasks those two agents needed to act on).
    logger.info("=" * 60)
    logger.info("Running Finalize Node")

    final_output = state.get("final_output", {})
    final_output.update({
        "sync_reporting_triggered": state.get("sync_reporting_triggered", False),
        "sync_reporting_trace": state.get("sync_reporting_trace", []),
        "sync_reporting_result": state.get("sync_reporting_result"),
        "meeting_coordinator_triggered": state.get("meeting_coordinator_triggered", False),
        "meeting_coordinator_trace": state.get("meeting_coordinator_trace", []),
        "scheduled_meeting": state.get("scheduled_meeting"),
    })
    state["final_output"] = final_output

    return state
