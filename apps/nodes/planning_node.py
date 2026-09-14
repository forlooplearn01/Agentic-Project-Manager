from apps.agent.planning import run_planning_agent
from apps.logger import get_logger
from apps.state import AgentState

logger = get_logger("PLANNING_NODE")


def task_planning_node(state: AgentState) -> AgentState:
    logger.info("=" * 60)
    logger.info("Running Planning Node")

    events_to_create = state.get("events_to_create", [])
    events_to_update = state.get("events_to_update", [])

    tasks_to_plan = [
        {
            "task": event.get("task_text", ""),
            "owner": event.get("owner"),
            "deadline": event.get("deadline"),
            "blocker": event.get("blocker"),
            "dependency": event.get("dependency"),
            "confidence": event.get("confidence", "medium"),
        }
        for event in events_to_create + events_to_update
        if event.get("task_text")
    ]

    planned_tasks, trace = run_planning_agent(tasks_to_plan, state.get("project_id", "default"))

    state["planned_tasks"] = planned_tasks
    state["planning_trace"] = trace
    return state
