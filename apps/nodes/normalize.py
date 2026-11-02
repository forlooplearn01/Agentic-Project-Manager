import datetime

from apps.llm import call_llm_json
from apps.logger import get_logger
from apps.state import AgentState
from prompts.normalize.prompt import get_normalize_prompt
from apps.utils.abbreviations import expand_abbreviations
from apps.utils.deadline import resolve_deadline
from apps.utils import clock

# Initialize logger for this node
logger = get_logger("NORMALIZE")

def task_normalize_node(state: AgentState) -> AgentState:
    logger.info("=" * 60)  # separator for each new node run
    # Log entry into normalize node and raw input for debugging
    logger.info("Running Normalize Task Node")
    # logger.info(f"Normalize raw_input: {state.get('raw_input')}")

    # Retrieve extracted tasks from state
    tasks = state.get("tasks", [])

    # Handle case where no tasks were extracted
    if not tasks:
        logger.info("No extracted tasks found. Setting normalized_events to empty list.")
        state["normalized_events"] = []
        # Conservative default - a document with zero extracted tasks that still needs a
        # follow-up meeting is a rare edge case not worth a dedicated LLM call to catch.
        state["followup_signal"] = False
        return dict(state)
    
    # Static abbreviation expansion: replace known abbreviations before LLM sees the tasks
    expanded_tasks = [
        {**task, "task": expand_abbreviations(task.get("task", ""))}
        for task in tasks
    ]

    # Prompt: Instruct LLM to extract additional information for task event. Normalize and classify tasks
    prompt = get_normalize_prompt(
        raw_input = state.get("raw_input", ""),
        tasks = expanded_tasks
    )

    # Call LLM to generate normalized structured events
    try:
        result = call_llm_json(prompt)
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        state["normalized_events"] = []
        state["followup_signal"] = False
        return dict(state)
    
    # Extract normalized events from LLM response
    normalized_events = result.get("normalized_events", [])

    # Validate that normalized events is a list
    if not isinstance(normalized_events, list):
        logger.warning("Invalid normalized_events format from LLM, defaulting to empty list")
        normalized_events = []

    # Define allowed values for event type and confidence
    allowed_confidence = {"high", "medium", "low"}

    # Clean and normalize each event object
    cleaned_events = []

    for item in normalized_events:
        if not isinstance(item, dict):
            continue

        task_text = item.get("task_text")
        owner = item.get("owner")
        status = item.get("status")
        deadline = item.get("deadline")
        blocker = item.get("blocker")
        dependency = item.get("dependency")
        confidence = item.get("confidence")

        # Skip invalid or empty task_text entries
        if not isinstance(task_text, str) or not task_text.strip():
            logger.warning("Skipping normalized event because task_text is missing or invalid")
            continue

        # Validate and correct confidence value
        if confidence not in allowed_confidence:
            logger.warning(f"Invalid confidence '{confidence}' found. Defaulting to 'medium'")
            confidence = "medium"

        # Append cleaned and normalized event to final list
        # event_type is not set here - the match node infers it after DB lookup
        cleaned_events.append({
            "task_text": task_text.strip(),
            "owner": owner if isinstance(owner, str) and owner.strip() else None,
            "status": status if isinstance(status, str) and status.strip() else None,
            "deadline": deadline if isinstance(deadline, str) and deadline.strip() else None,
            "blocker": blocker if isinstance(blocker, str) and blocker.strip() else None,
            "dependency": dependency if isinstance(dependency, str) and dependency.strip() else None,
            "confidence": confidence
        })

    # Post-process: resolve natural-language deadlines to ISO dates (YYYY-MM-DD).
    # Done here in Python after the LLM call - deterministic date math, not LLM work.
    today = clock.today()
    for evt in cleaned_events:
        if evt.get("deadline"):
            resolved = resolve_deadline(evt["deadline"], today)
            if resolved != evt["deadline"]:
                logger.info(f"Deadline resolved: '{evt['deadline']}' → '{resolved}'")
            evt["deadline"] = resolved

    # Store normalized events back into state
    state["normalized_events"] = cleaned_events

    # Cheap pre-filter for whether this document might need a follow-up meeting - the
    # Meeting Coordinator agent (MoM path only, gated on this flag) makes the real call.
    followup_signal = result.get("followup_signal", False)
    state["followup_signal"] = bool(followup_signal) if isinstance(followup_signal, bool) else False

    # Log number of successfully normalized events
    logger.info(f"Normalized {len(cleaned_events)} task events, followup_signal={state['followup_signal']}")

    return dict(state)