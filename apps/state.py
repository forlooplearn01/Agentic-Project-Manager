from typing import TypedDict, List, Dict, Any, Optional


# Shared state passed across all LangGraph nodes and agents
class AgentState(TypedDict):
    raw_input: str  # original document text (SOW or MoM)
    project_id: str  # all DB reads/writes are scoped by this value
    sow_only: bool  # set by the Extractor page: only an SOW is run; a document the Supervisor takes for meeting minutes stops at the redirect node
    document_type: str  # "sow" | "mom" - set by the Supervisor; extract/normalize/match/planning ignore it. Read by sow_registers (sow), meeting_coordinator (mom) and stored on the source document by format

    # Core pipeline outputs (extract -> normalize -> match)
    tasks: List[Dict[str, Any]]  # extracted tasks
    normalized_events: List[Dict[str, Any]]  # structured task/event objects after normalization
    followup_signal: bool  # normalize's cheap pre-filter: does this MoM look like it needs a follow-up meeting?

    matched_events: List[Dict[str, Any]]  # normalized events enriched with task matching results
    events_to_update: List[Dict[str, Any]]  # matched events (existing tasks to update)
    events_to_create: List[Dict[str, Any]]  # new tasks (no match found)
    unresolved_events: List[Dict[str, Any]]  # update events with no match after all 4 match stages

    # Planning agent output - replaces prioritized_tasks/is_valid/validation_feedback/refine_target
    planned_tasks: List[Dict[str, Any]]  # task + priority + deadline + dependency notes, self-validated
    planning_trace: List[Dict[str, Any]]  # tool calls Planning made, for the dashboard trace panel

    # Sync/Reporting agent - conditional
    sync_reporting_triggered: bool
    sync_reporting_trace: List[Dict[str, Any]]
    sync_reporting_result: Optional[Dict[str, Any]]

    # Meeting Coordinator agent - conditional, MoM path only
    meeting_coordinator_triggered: bool
    meeting_coordinator_trace: List[Dict[str, Any]]
    scheduled_meeting: Optional[Dict[str, Any]]

    # Final output
    final_output: Dict[str, Any]
