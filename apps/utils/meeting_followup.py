"""A transcript in a project's meetings folder can also ask for another meeting ("let's reconvene Thursday at 2"). This hands such a transcript to the
Meeting Coordinator agent (apps/agent/meeting_coordinator.py), which decides whether a follow-up is warranted, resolves the attendees and stores a
PROPOSED meeting. Nothing is sent to the calendar: a person approves it in Scheduled meetings first (POST /meetings/{id}/approve).

Same order as the plan suggestions: the folder reads the file, proposes, and waits for the PMO. A failure here never fails the file."""
import re

from apps.logger import get_logger

logger = get_logger("MEETING_FOLLOWUP")

MAX_CHARS = 60000  # the agent gets the whole transcript; a longer one keeps its start and, more of, its end (that is where "let's meet again" is said)

# The cheap pre-filter (the Extractor had one too): without a hint of a meeting being arranged, no agent run is paid for. Deliberately wide; the
# agent makes the real decision and may say a follow-up is not warranted.
HINT = re.compile(
    r"re-?convene|re-?group|follow[- ]?up (meeting|call|session|sync)|(meet|sync|talk|speak|connect|call|catch up|touch base|check in)\b[^.\n]{0,40}\b"
    r"(again|tomorrow|today|next|monday|tuesday|wednesday|thursday|friday|this week|later this)|"
    r"(schedule|set up|book|arrange|organi[sz]e|plan)\b[^.\n]{0,30}\b(call|meeting|session|sync|workshop|review|huddle|catch-?up)|"
    r"next (meeting|call|sync|session)|another (call|meeting|session|sync)|huddle|working session|stand-?up tomorrow",
    re.IGNORECASE,
)


def run_agent(text: str, project_id: str) -> dict:
    """The agent call, as one place to replace in tests."""
    from apps.agent.meeting_coordinator import run_meeting_coordinator_agent
    result, _trace = run_meeting_coordinator_agent(text, project_id)
    return result


def has_hint(text: str) -> bool:
    return bool(HINT.search(text or ""))


def _shorten(text: str) -> str:
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS // 3] + "\n[...]\n" + text[-(MAX_CHARS - MAX_CHARS // 3):]


def propose_followup(project_id: str, text: str, source_name: str = None) -> dict:
    """-> {"followup": "none" | "not_needed" | "proposed" | "error", ...}. Stored on the file's summary, so the app can say what happened."""
    if not has_hint(text):
        return {"followup": "none"}
    try:
        result = run_agent(_shorten(text), project_id)
    except Exception as e:  # reading a transcript must never fail because the follow-up step did
        logger.exception(f"{project_id}/{source_name}: the Meeting Coordinator failed")
        return {"followup": "error", "followup_reason": str(e)[:200]}
    meeting = result.get("scheduled_meeting")
    if result.get("followup_warranted") and meeting:
        logger.info(f"{project_id}/{source_name}: proposed '{meeting.get('title')}' at {meeting.get('start_iso')}, waiting for approval")
        return {"followup": "proposed", "meeting_id": meeting.get("meeting_id"), "followup_title": meeting.get("title"),
                "followup_start": meeting.get("start_iso"), "followup_reason": result.get("reason")}
    return {"followup": "not_needed", "followup_reason": result.get("reason")}
