from apps.agent.sync_reporting import should_trigger_sync_reporting, run_sync_reporting_agent
from apps.logger import get_logger
from apps.state import AgentState

logger = get_logger("SYNC_REPORTING_NODE")


def task_sync_reporting_node(state: AgentState) -> AgentState:
    logger.info("=" * 60)
    logger.info("Running Sync/Reporting Node")

    if not should_trigger_sync_reporting(state):
        logger.info("No meaningful change this run - Sync/Reporting agent skipped.")
        state["sync_reporting_triggered"] = False
        state["sync_reporting_trace"] = []
        return state

    result, trace = run_sync_reporting_agent(state)
    state["sync_reporting_triggered"] = True
    state["sync_reporting_trace"] = trace
    state["sync_reporting_result"] = result
    return state
