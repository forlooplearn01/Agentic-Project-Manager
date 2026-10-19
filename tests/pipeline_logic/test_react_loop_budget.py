"""Regression test for the Meeting Coordinator tool-call-budget bug: resolve_attendees (1
call) + check_owner_availability per attendee (3 calls for 3 attendees) exhausted a budget
of 4, leaving zero calls left for schedule_meeting - the one call the agent exists to make.
Fixed by raising max_tool_calls to 8.

The LLM is scripted here, not real - this tests react_loop.py's own budget-enforcement
loop (the tool_calls_made counter, the cutoff check), never a model's judgment about
which tools to call. That's what the separate eval harness is for."""
import json

from apps.agent.react_loop import run_react_loop
from tests.conftest import make_tool_call_response, make_final_json_response


def _fake_tool_impls():
    return {
        "resolve_attendees": lambda **kw: json.dumps({"resolved": ["a@example.com", "b@example.com", "c@example.com"]}),
        "check_owner_availability": lambda **kw: json.dumps({"status": "unavailable_to_check"}),
        "schedule_meeting": lambda **kw: json.dumps({"status": "scheduled", "event_id": "evt_1"}),
    }


def test_budget_of_4_exhausts_before_reaching_schedule_meeting(scripted_llm):
    # Exactly the real bug scenario: 1 resolve + 3 availability checks = 4, then the
    # model (with tools now withheld since budget is spent) has to answer with no
    # schedule_meeting call ever having been possible.
    scripted_llm([
        make_tool_call_response("call_1", "resolve_attendees", {"project_id": "x", "names": ["A", "B", "C"]}),
        make_tool_call_response("call_2", "check_owner_availability", {"owner": "a@example.com"}),
        make_tool_call_response("call_3", "check_owner_availability", {"owner": "b@example.com"}),
        make_tool_call_response("call_4", "check_owner_availability", {"owner": "c@example.com"}),
        make_final_json_response({"followup_warranted": True, "scheduled_meeting": None}),
    ])

    result, trace = run_react_loop(
        agent_name="test_agent",
        system_prompt="test",
        user_message="test",
        tool_schemas=[],
        tool_impls=_fake_tool_impls(),
        max_tool_calls=4,
    )

    assert len(trace) == 4
    assert [t["tool"] for t in trace] == [
        "resolve_attendees", "check_owner_availability", "check_owner_availability", "check_owner_availability",
    ]
    assert result["scheduled_meeting"] is None


def test_budget_of_8_allows_schedule_meeting_through(scripted_llm):
    scripted_llm([
        make_tool_call_response("call_1", "resolve_attendees", {"project_id": "x", "names": ["A", "B", "C"]}),
        make_tool_call_response("call_2", "check_owner_availability", {"owner": "a@example.com"}),
        make_tool_call_response("call_3", "check_owner_availability", {"owner": "b@example.com"}),
        make_tool_call_response("call_4", "check_owner_availability", {"owner": "c@example.com"}),
        make_tool_call_response("call_5", "schedule_meeting", {"title": "Sync", "attendee_emails": ["a@example.com"]}),
        make_final_json_response({"followup_warranted": True, "scheduled_meeting": {"status": "scheduled"}}),
    ])

    result, trace = run_react_loop(
        agent_name="test_agent",
        system_prompt="test",
        user_message="test",
        tool_schemas=[],
        tool_impls=_fake_tool_impls(),
        max_tool_calls=8,
    )

    assert len(trace) == 5
    assert trace[-1]["tool"] == "schedule_meeting"
    assert result["scheduled_meeting"]["status"] == "scheduled"
