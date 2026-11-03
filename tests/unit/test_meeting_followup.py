"""A transcript that asks for another meeting (apps/utils/meeting_followup.py) and the calendar's time zone default. No model, no calendar."""
import pytest

from apps.utils import meeting_followup as mf
from mcp_server.tools.calendar import MEETING_TIMEZONE, _event_time


@pytest.mark.parametrize("text", [
    "We agreed to reconvene on Thursday at 2pm to close the VPN issue.",
    "Let's meet again next Tuesday and decide.",
    "Rucheek will set up a call with Calderwick's IT team.",
    "Can we schedule a follow-up meeting for after the UAT?",
    "Let's catch up tomorrow morning on the migration.",
    "Another session is needed with the data team.",
])
def test_wording_that_arranges_a_meeting_is_a_hint(text):
    assert mf.has_hint(text)


@pytest.mark.parametrize("text", [
    "Cy will take over the search work and wants it finished by next week.",
    "Follow up with the IT contact about the certificate.",
    "The dashboard is done. Bo owns the migration plan, due Friday.",
    "",
])
def test_ordinary_task_talk_is_not_a_hint(text):
    assert not mf.has_hint(text)


def test_no_hint_means_no_agent_run(monkeypatch):
    monkeypatch.setattr(mf, "run_agent", lambda *a: pytest.fail("the agent must not run without a hint"))
    assert mf.propose_followup("p", "Bo owns the migration plan.", "a.txt") == {"followup": "none"}


def test_a_warranted_follow_up_is_reported_with_its_meeting(monkeypatch):
    seen = {}

    def agent(text, project_id):
        seen.update(text=text, project=project_id)
        return {"followup_warranted": True, "reason": "VPN still blocked",
                "scheduled_meeting": {"meeting_id": "m1", "title": "VPN follow-up", "start_iso": "2026-11-05T14:00:00"}}
    monkeypatch.setattr(mf, "run_agent", agent)
    r = mf.propose_followup("forloop", "Let's reconvene Thursday.", "call.txt")
    assert r == {"followup": "proposed", "meeting_id": "m1", "followup_title": "VPN follow-up", "followup_start": "2026-11-05T14:00:00", "followup_reason": "VPN still blocked"}
    assert seen == {"text": "Let's reconvene Thursday.", "project": "forloop"}


def test_the_agent_may_decide_no_meeting_is_needed(monkeypatch):
    monkeypatch.setattr(mf, "run_agent", lambda *a: {"followup_warranted": False, "reason": "only a deadline was mentioned", "scheduled_meeting": None})
    assert mf.propose_followup("p", "Let's meet again?", "a.txt") == {"followup": "not_needed", "followup_reason": "only a deadline was mentioned"}


def test_an_agent_failure_never_fails_the_file(monkeypatch):
    def boom(*a):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(mf, "run_agent", boom)
    r = mf.propose_followup("p", "Let's reconvene Thursday.", "a.txt")
    assert r["followup"] == "error" and "model unavailable" in r["followup_reason"]


def test_a_long_transcript_keeps_its_start_and_more_of_its_end():
    text = "A" * 50000 + "B" * 50000 + "let's reconvene Friday"
    short = mf._shorten(text)
    assert len(short) <= mf.MAX_CHARS + 20 and short.startswith("A") and short.endswith("let's reconvene Friday")
    assert mf._shorten("small") == "small"


def test_a_time_without_an_offset_gets_the_meeting_time_zone():
    assert _event_time("2026-11-03T10:00:00") == {"dateTime": "2026-11-03T10:00:00", "timeZone": MEETING_TIMEZONE}
    for with_offset in ("2026-11-02T21:00:00-05:00", "2026-11-02T21:00:00+0530", "2026-11-02T21:00:00Z"):
        assert _event_time(with_offset) == {"dateTime": with_offset}


# ---------------------------------------------------------------- the coordinator itself: a looping model must still end with a proposal

def test_a_repeated_tool_call_is_answered_from_memory():
    import json
    from apps.agent.meeting_coordinator import _remembering
    calls = []
    tools = _remembering({"check_owner_availability": lambda owner: calls.append(owner) or json.dumps({"status": "unavailable_to_check"})})
    first = tools["check_owner_availability"](owner="ram@x.com")
    again = json.loads(tools["check_owner_availability"](owner="ram@x.com"))
    other = tools["check_owner_availability"](owner="bo@x.com")
    assert calls == ["ram@x.com", "bo@x.com"]  # the repeat never reached the calendar
    assert again["repeat"] is True and json.loads(again["earlier_result"]) == json.loads(first) and json.loads(other)["status"] == "unavailable_to_check"


def test_a_warranted_follow_up_with_no_proposal_is_rescued(monkeypatch):
    """The agent said yes and spent its tool calls on availability checks (what gpt-4o-mini did): one more short run proposes the meeting."""
    import json
    from apps.agent import meeting_coordinator as mc
    resolved = json.dumps({"resolved": [{"name": "Nirmal", "email": "n@x.com"}, {"name": "Ram", "email": "r@x.com"}], "unresolved": ["Kofi"]})
    runs = []

    def fake_loop(**kw):
        runs.append(kw["agent_name"])
        if kw["agent_name"] == "meeting_coordinator":
            return ({"followup_warranted": True, "reason": "blocker", "unresolved_attendees": ["Kofi"], "scheduled_meeting": None},
                    [{"tool": "resolve_attendees", "args": {}, "result": resolved}])
        assert [t["function"]["name"] for t in kw["tool_schemas"]] == ["propose_meeting"] and kw["max_tool_calls"] == 1
        assert "n@x.com" in kw["system_prompt"] and "r@x.com" in kw["system_prompt"]
        assert "let's reconvene Wednesday" in kw["user_message"]  # the rescue sees the minutes, or it could not know the day and time they name
        proposal = {"status": "proposed", "title": "Blocker follow-up", "attendee_emails": ["n@x.com", "r@x.com"], "start_iso": "2026-11-04T10:00:00", "end_iso": "2026-11-04T10:30:00"}
        return {"done": True}, [{"tool": "propose_meeting", "args": {}, "result": json.dumps(proposal)}]
    stored = {}
    monkeypatch.setattr(mc, "run_react_loop", fake_loop)
    monkeypatch.setattr(mc, "create_pending_meeting", lambda **kw: stored.update(kw) or "m-9")
    result, trace = mc.run_meeting_coordinator_agent("minutes: let's reconvene Wednesday", "p")
    assert runs == ["meeting_coordinator", "meeting_coordinator_rescue"]
    assert result["scheduled_meeting"]["meeting_id"] == "m-9" and stored["attendee_emails"] == ["n@x.com", "r@x.com"] and stored["unresolved_attendees"] == ["Kofi"]
    assert [e["tool"] for e in trace] == ["resolve_attendees", "propose_meeting"]


def test_no_rescue_when_no_follow_up_is_warranted_or_nobody_resolved(monkeypatch):
    from apps.agent import meeting_coordinator as mc
    monkeypatch.setattr(mc, "create_pending_meeting", lambda **kw: pytest.fail("nothing should be stored"))
    monkeypatch.setattr(mc, "run_react_loop", lambda **kw: ({"followup_warranted": False, "reason": "no", "scheduled_meeting": None}, []))
    assert mc.run_meeting_coordinator_agent("minutes", "p")[0]["scheduled_meeting"] is None
    calls = []
    monkeypatch.setattr(mc, "run_react_loop", lambda **kw: calls.append(kw["agent_name"]) or ({"followup_warranted": True, "reason": "x", "scheduled_meeting": None}, []))
    assert mc.run_meeting_coordinator_agent("minutes", "p")[0]["scheduled_meeting"] is None and calls == ["meeting_coordinator"]  # no emails: no rescue
