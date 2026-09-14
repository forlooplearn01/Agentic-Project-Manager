from langgraph.graph import StateGraph, END

from apps.state import AgentState
from apps.supervisor import supervisor_node
from apps.nodes.extract import task_extract_node
from apps.nodes.normalize import task_normalize_node
from apps.nodes.match import task_match_node
from apps.nodes.planning_node import task_planning_node
from apps.nodes.sync_reporting_node import task_sync_reporting_node
from apps.nodes.meeting_coordinator_node import task_meeting_coordinator_node
from apps.nodes.format import task_format_node
from apps.nodes.finalize_node import task_finalize_node


def build_graph():
    graph = StateGraph(AgentState)

    # Nodes
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("extract", task_extract_node)
    graph.add_node("normalize", task_normalize_node)
    graph.add_node("match", task_match_node)
    graph.add_node("planning", task_planning_node)
    graph.add_node("format", task_format_node)
    graph.add_node("sync_reporting", task_sync_reporting_node)
    graph.add_node("meeting_coordinator", task_meeting_coordinator_node)
    graph.add_node("finalize", task_finalize_node)

    # Entry point - Supervisor decides sow vs mom (code, with a conditional LLM
    # tie-break only when the task-count heuristic conflicts with document content).
    graph.set_entry_point("supervisor")

    # `format` must run before sync_reporting/meeting_coordinator: it's what persists
    # new tasks and assigns their real task_ids via store_tasks() - Sync/Reporting needs
    # those ids to sync to Jira. Sync/Reporting and Meeting Coordinator are graph-linear
    # but internally conditional - each node decides whether its agent actually runs
    # (should_trigger_sync_reporting / followup_signal), so a no-op run costs zero extra
    # LLM calls even though the node is always visited.
    graph.add_edge("supervisor", "extract")
    graph.add_edge("extract", "normalize")
    graph.add_edge("normalize", "match")
    graph.add_edge("match", "planning")
    graph.add_edge("planning", "format")
    graph.add_edge("format", "sync_reporting")
    graph.add_edge("sync_reporting", "meeting_coordinator")
    graph.add_edge("meeting_coordinator", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()
