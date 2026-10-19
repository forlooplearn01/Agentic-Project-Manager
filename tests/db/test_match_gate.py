"""match.py's LLM gate (top-3 candidates, same/different/unsure verdicts) and format.py's
respect for the gate's verdict. The LLM is scripted - what's under test is our control flow
(candidate mapping, fallbacks, the llm_unsure flag, not re-merging a rejected candidate), never
a real model's judgment; that's eval/nodes/eval_match_seeded.py's job."""
import json
import re

from apps.db.db import get_connection, insert_task
from apps.nodes.format import store_tasks
from apps.nodes.match import task_match_node
from tests.conftest import make_final_json_response


def _seed(project_id, text, owner=None):
    return insert_task(
        {"task_text": text, "normalized_task_text": text.lower(), "owner": owner}, project_id
    )


def _task_count(project_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM tasks WHERE project_id = %s", (project_id,))
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


def _match(project_id, text, owner=None):
    state = {"normalized_events": [{"task_text": text, "owner": owner}], "project_id": project_id}
    return task_match_node(state)["matched_events"][0]


def test_unsure_creates_new_task_and_flags_it(test_project, scripted_llm):
    _seed(test_project, "Write unit tests for CSV module")
    scripted_llm(
        [make_final_json_response({"verdicts": [{"id": 0, "decision": "unsure"}]})],
        target="apps.nodes.match.complete",
    )
    event = _match(test_project, "Complete CSV module coding and unit tests")
    assert event["is_matched"] is False
    assert event["event_type"] == "new_task"
    assert event["match_method"] == "llm_unsure"
    assert event["llm_adjudicated"] is True


def test_same_applies_the_candidate_the_model_picked_not_just_the_closest(test_project, monkeypatch):
    first = _seed(test_project, "Send CSV module test case document to Marcus")
    second = _seed(test_project, "Send CSV module test report to Marcus by Friday")
    captured = {}

    def fake_complete(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        captured["prompt"] = prompt
        # Pick whichever lettered candidate is the *second* task, wherever cosine ranked it.
        letter = next(m.group(1) for m in re.finditer(r"^\s+([a-c])\) \"(.*?)\"", prompt, re.M)
                      if "test report" in m.group(2))
        return make_final_json_response({"verdicts": [{"id": 0, "decision": "same", "candidate": letter}]})

    monkeypatch.setattr("apps.nodes.match.complete", fake_complete)
    event = _match(test_project, "Send the CSV module test document to Marcus")
    assert "a)" in captured["prompt"] and "b)" in captured["prompt"]  # saw more than one candidate
    assert event["is_matched"] is True
    assert event["matched_task_id"] == second
    assert event["matched_task_id"] != first


def test_prompt_carries_owner_hint(test_project, monkeypatch):
    _seed(test_project, "Write unit tests for CSV module", owner="Jordan Mills")
    seen = {}

    def fake_complete(**kwargs):
        seen["prompt"] = kwargs["messages"][0]["content"]
        return make_final_json_response({"verdicts": [{"id": 0, "decision": "different"}]})

    monkeypatch.setattr("apps.nodes.match.complete", fake_complete)
    _match(test_project, "Complete CSV module coding and unit tests", owner="Marcus Webb")
    assert "owner: Marcus Webb" in seen["prompt"] and "owner: Jordan Mills" in seen["prompt"]


def test_same_with_invalid_candidate_letter_is_treated_as_unsure(test_project, scripted_llm):
    _seed(test_project, "Write unit tests for CSV module")
    scripted_llm(
        [make_final_json_response({"verdicts": [{"id": 0, "decision": "same", "candidate": "z"}]})],
        target="apps.nodes.match.complete",
    )
    event = _match(test_project, "Complete CSV module coding and unit tests")
    assert event["is_matched"] is False
    assert event["match_method"] == "llm_unsure"


def test_llm_failure_means_new_task_and_not_adjudicated(test_project, monkeypatch):
    _seed(test_project, "Write unit tests for CSV module")

    def boom(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr("apps.nodes.match.complete", boom)
    event = _match(test_project, "Complete CSV module coding and unit tests")
    assert event["is_matched"] is False
    assert event["event_type"] == "new_task"
    assert not event.get("llm_adjudicated")


def _store(project_id, text, **flags):
    event = {"task_text": text, "event_type": "new_task", "is_matched": False, **flags}
    state = {"events_to_create": [event], "project_id": project_id, "planned_tasks": []}
    store_tasks(state)
    return event


def test_format_dedup_still_merges_a_near_identical_task_when_the_gate_did_not_rule(test_project):
    _seed(test_project, "Create Slack channel for API questions")
    _store(test_project, "Create Slack channel for API questions today")
    assert _task_count(test_project) == 1  # the 0.85 safety-net guard fired, as before


def test_format_dedup_does_not_override_a_gate_rejection(test_project):
    _seed(test_project, "Create Slack channel for API questions")
    event = _store(test_project, "Create Slack channel for API questions today", llm_adjudicated=True)
    assert _task_count(test_project) == 2
    assert event["match_method"] == "created"


def test_unsure_flag_survives_store_tasks_for_the_audit_log(test_project):
    event = _store(
        test_project, "Draft deployment runbook",
        llm_adjudicated=True, match_method="llm_unsure", match_confidence="low",
    )
    assert _task_count(test_project) == 1
    assert event["match_method"] == "llm_unsure"
    assert event["match_confidence"] == "low"


LONG = ("Fix all four integration test defects (scatter plot y-axis padding, pie chart label "
        "overflow with Other-slice merge, missing stat key user-facing warning)")


def _capture_gate(monkeypatch, decision):
    seen = {"calls": 0}

    def fake_complete(**kwargs):
        seen["calls"] += 1
        seen["prompt"] = kwargs["messages"][0]["content"]
        verdict = {"id": 0, "decision": decision}
        if decision == "same":
            verdict["candidate"] = "a"
        return make_final_json_response({"verdicts": [verdict]})

    monkeypatch.setattr("apps.nodes.match.complete", fake_complete)
    return seen


def test_containment_hit_below_the_cosine_floor_is_offered_to_the_gate(test_project, monkeypatch):
    from apps.nodes.match import CANDIDATE_MIN_SCORE, cosine_sim
    from apps.utils.embeddings import get_embedding

    task_id = _seed(test_project, LONG)
    mention = "Fix all four integration test defects"
    assert cosine_sim(get_embedding(mention), get_embedding(LONG)) < CANDIDATE_MIN_SCORE  # precondition
    seen = _capture_gate(monkeypatch, "same")
    event = _match(test_project, mention)
    assert LONG in seen["prompt"]
    assert event["is_matched"] is True and event["matched_task_id"] == task_id


def test_containment_is_never_an_automatic_match(test_project, monkeypatch):
    _seed(test_project, LONG)
    seen = _capture_gate(monkeypatch, "different")
    event = _match(test_project, "Fix all four integration test defects")
    assert seen["calls"] == 1
    assert event["is_matched"] is False and event["llm_adjudicated"] is True


def test_containment_needs_at_least_three_words(test_project, monkeypatch):
    _seed(test_project, "Review integration test plan with the whole team on Friday")
    seen = _capture_gate(monkeypatch, "same")
    event = _match(test_project, "Review plan")  # 2 words, and nothing close by cosine
    assert seen["calls"] == 0  # nothing offered, so the gate was never asked
    assert event["is_matched"] is False


def test_containment_off_means_no_such_candidate(test_project, monkeypatch):
    monkeypatch.setattr("apps.nodes.match.CONTAINMENT_CANDIDATES", False)
    _seed(test_project, LONG)
    seen = _capture_gate(monkeypatch, "same")
    event = _match(test_project, "Fix all four integration test defects")
    assert seen["calls"] == 0 and event["is_matched"] is False
