import json
from datetime import datetime
from pathlib import Path

from apps.agent.react_loop import run_react_loop
from apps.agent.tool_registry import MEETING_COORDINATOR_TOOL_SCHEMAS, MEETING_COORDINATOR_TOOL_IMPLS
from apps.db.db import create_pending_meeting
from apps.logger import get_logger

logger = get_logger("MEETING_COORDINATOR")

_MANIFEST_PATH = Path(__file__).parent / "manifests" / "meeting_coordinator.md"
_MANIFEST = _MANIFEST_PATH.read_text()

_SYSTEM_PROMPT_TAIL = """
## What counts as actually warranted
- An unresolved decision the group needs to make together
- An explicit "let's reconvene" / "let's sync again" / "schedule a follow-up" statement
- A blocker that needs higher-touch discussion than an async status update
Do NOT schedule a meeting just because a date or deadline is mentioned in passing -
that's a task deadline, not a meeting request.

## Output format
Respond with ONLY a JSON object, no other text:
{"followup_warranted": true|false, "reason": "<short reason>",
"unresolved_attendees": ["<name>", ...], "scheduled_meeting": {...}|null}
"""


def run_meeting_coordinator_agent(raw_input: str, project_id: str) -> tuple:
    """Called only when normalize's followup_signal fires on the MoM path.
    Returns (result_dict, trace)."""
    user_message = json.dumps({"project_id": project_id, "meeting_minutes": raw_input})
    # Plain concatenation, not str.format() - the manifest and the tail both contain
    # literal JSON-example braces (e.g. {"followup_warranted": ...}) that .format()
    # would misread as its own placeholders and crash on. Computed per call rather than
    # a module-level constant, so a long-running server doesn't keep telling the agent
    # it's the day the process started - the exact class of bug this fixes.
    today_line = datetime.now().strftime("Today is %A, %Y-%m-%d")
    today_section = f"""
## Today's date
{today_line}. The meeting minutes' own language ("next week", "early next week", "in a
few days") is always relative to this date, never to your own sense of "now" - resolve
it from here. A meeting more than a few weeks out or in the past is almost certainly a
miscalculation; double-check the arithmetic before calling schedule_meeting.
"""
    system_prompt = _MANIFEST + today_section + _SYSTEM_PROMPT_TAIL

    try:
        result, trace = run_react_loop(
            agent_name="meeting_coordinator",
            system_prompt=system_prompt,
            user_message=user_message,
            tool_schemas=MEETING_COORDINATOR_TOOL_SCHEMAS,
            tool_impls=MEETING_COORDINATOR_TOOL_IMPLS,
            # resolve_attendees (1) + one check_owner_availability per attendee (1 each)
            # already exhausts a budget of 4 with just 3 attendees, leaving nothing for
            # the schedule_meeting call itself - the one tool call this agent exists to
            # make. 8 comfortably covers resolve + a availability check per attendee for
            # a normal-sized meeting + the actual scheduling call.
            max_tool_calls=8,
        )
    except Exception as e:
        logger.error(f"Meeting Coordinator agent failed: {e}")
        return {
            "followup_warranted": False,
            "reason": f"agent error: {e}",
            "unresolved_attendees": [],
            "scheduled_meeting": None,
        }, []

    # Grounding check: scheduled_meeting must reflect an actual propose_meeting call that
    # succeeded, never the model's unverified claim - a hallucinated "proposed" result here
    # would mean the dashboard shows a meeting that doesn't actually exist for anyone to
    # approve.
    proposed_meeting = _find_proposed_meeting_in_trace(trace)
    if result.get("scheduled_meeting") and not proposed_meeting:
        logger.warning("Meeting Coordinator claimed a scheduled_meeting with no successful propose_meeting call in trace - dropping the claim")
    result["scheduled_meeting"] = proposed_meeting

    # Persist the proposal so a person can review/approve it from the UI - without this,
    # it would only ever exist transiently inside this one job's trace.
    if proposed_meeting:
        meeting_id = create_pending_meeting(
            project_id=project_id,
            title=proposed_meeting["title"],
            description=proposed_meeting.get("description") or "",
            attendee_emails=proposed_meeting["attendee_emails"],
            unresolved_attendees=result.get("unresolved_attendees", []),
            start_iso=proposed_meeting["start_iso"],
            end_iso=proposed_meeting["end_iso"],
            reason=result.get("reason", ""),
        )
        proposed_meeting["meeting_id"] = meeting_id
        result["scheduled_meeting"] = proposed_meeting

    logger.info(f"Meeting Coordinator done via {len(trace)} tool call(s): {result}")
    return result, trace


def _find_proposed_meeting_in_trace(trace: list):
    for entry in trace:
        if entry.get("tool") != "propose_meeting":
            continue
        try:
            result_data = json.loads(entry.get("result", "{}"))
        except json.JSONDecodeError:
            continue
        if result_data.get("status") == "proposed":
            return result_data
    return None
