"""Similarity hint, AI deliverable suggestions, and task -> deliverable assignment. The LLM is scripted: what is under
test is our own logic (evidence verification, de-duplication, deterministic number matching, batching, review gating),
never a real model's judgment - that is what eval/nodes/ measures."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from apps.api import app
from apps.db.db import (
    assign_tasks_to_deliverables,
    create_deliverable,
    create_deliverables_from_tasks,
    fetch_raci_matrix,
    find_tasks_similar_to_deliverables,
    insert_task,
    list_deliverables,
    upsert_source_document,
)
from apps.utils.deliverable_ai import propose_assignments, suggest_deliverables_from_text
from tests.conftest import make_final_json_response

client = TestClient(app)
SOW = ("Statement of Work. Phase 1: Marcus Chen will build the ETL pipeline by end of Week 3. "
       "Sofia Martins will deliver a data mapping document to the client for sign-off by end of Week 2. "
       "The team will deploy the dashboard to production by end of Week 8.")


def _task(project_id, text, owner=None):
    return insert_task({"task_text": text, "normalized_task_text": text.lower(), "owner": owner}, project_id)


def _fake_llm(monkeypatch, payload):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs["messages"][0]["content"])
        return make_final_json_response(payload(kwargs["messages"][0]["content"]) if callable(payload) else payload)

    monkeypatch.setattr("apps.utils.deliverable_ai.complete", fake)
    return calls


# ------------------------------------------------------------------ task 1: similarity hint

def test_similar_tasks_are_hinted_and_unrelated_or_promoted_ones_are_not(test_project):
    create_deliverable(test_project, "AI Agent and Graph Generation Module (codebase + unit tests)", number="4.2")
    similar = _task(test_project, "Build AI Agent and Graph Generation Module")
    _task(test_project, "Send meeting notes to all attendees")
    hints = find_tasks_similar_to_deliverables(test_project)
    assert set(hints) == {str(similar)}
    assert hints[str(similar)]["number"] == "4.2" and hints[str(similar)]["score"] >= 0.70

    create_deliverables_from_tasks(test_project, [str(similar)])  # once promoted it is no longer hinted
    assert find_tasks_similar_to_deliverables(test_project) == {}


def test_suggested_or_rejected_deliverables_are_not_used_for_hints(test_project):
    create_deliverable(test_project, "Data Mapping Document", review_state="suggested", origin="agent")
    _task(test_project, "Deliver data mapping document to the client")
    assert find_tasks_similar_to_deliverables(test_project) == {}


def test_similar_tasks_endpoint(test_project):
    create_deliverable(test_project, "Data Mapping Document", number="1.1")
    t = _task(test_project, "Deliver data mapping document")
    assert str(t) in client.get(f"/projects/{test_project}/deliverables/similar-tasks").json()["similar"]


# ------------------------------------------------------------------ task 2: suggestions

def test_suggestions_keep_only_verbatim_evidence_and_never_invent_missing_fields(monkeypatch):
    _fake_llm(monkeypatch, {"deliverables": [
        {"title": "ETL pipeline", "owner": "Marcus Chen", "deadline": "end of Week 3", "evidence": "Marcus Chen will build the ETL pipeline"},
        {"title": "Data mapping document", "owner": None, "deadline": None, "evidence": "a quote that is NOT in the SOW"},
        {"title": "  ", "owner": None, "deadline": None, "evidence": "ignored: blank title"},
        "not even a dict",
    ]})
    items = suggest_deliverables_from_text(SOW)
    assert [i["title"] for i in items] == ["ETL pipeline", "Data mapping document"]
    assert items[0]["evidence"] == "Marcus Chen will build the ETL pipeline" and items[0]["owner"] == "Marcus Chen"
    assert items[1]["evidence"] is None  # the made-up quote is dropped, not shown


def test_a_failing_model_call_raises_a_clear_error(monkeypatch):
    def boom(**kwargs):
        raise TimeoutError("provider down")
    monkeypatch.setattr("apps.utils.deliverable_ai.complete", boom)
    with pytest.raises(RuntimeError):
        suggest_deliverables_from_text(SOW)


def test_the_definition_override_reaches_the_prompt(monkeypatch):
    calls = _fake_llm(monkeypatch, {"deliverables": []})
    suggest_deliverables_from_text(SOW, definition="ONLY documents count")
    assert "ONLY documents count" in calls[0] and "tangible output" not in calls[0]


def test_suggest_endpoint_stores_suggestions_for_review_and_dedupes(test_project, monkeypatch):
    assert client.post(f"/projects/{test_project}/deliverables/suggest").status_code == 400  # no SOW yet
    upsert_source_document(SOW, "sow", test_project)
    create_deliverable(test_project, "Data Mapping Document", number="1.1")  # already known
    _fake_llm(monkeypatch, {"deliverables": [
        {"title": "ETL pipeline", "owner": "Marcus Chen", "deadline": "end of Week 3", "evidence": "Marcus Chen will build the ETL pipeline"},
        {"title": "Data mapping document", "owner": None, "deadline": None, "evidence": None},   # same as the existing one
        {"title": "Production deployment", "owner": None, "deadline": "end of Week 8", "evidence": None},
    ]})
    result = client.post(f"/projects/{test_project}/deliverables/suggest").json()
    assert [d["title"] for d in result["created"]] == ["ETL pipeline", "Production deployment"] and result["skipped_existing"] == 1
    assert {d["review_state"] for d in result["created"]} == {"suggested"} and {d["origin"] for d in result["created"]} == {"agent"}

    # suggestions are visible in the register but are NOT RACI rows until accepted
    assert len(client.get(f"/projects/{test_project}/deliverables").json()["deliverables"]) == 3
    assert [r["title"] for r in fetch_raci_matrix(test_project, "deliverable")["rows"]] == ["Data Mapping Document"]

    etl = next(d for d in result["created"] if d["title"] == "ETL pipeline")
    assert client.patch(f"/deliverables/{etl['deliverable_id']}", json={"review_state": "confirmed"}).status_code == 200
    assert {r["title"] for r in fetch_raci_matrix(test_project, "deliverable")["rows"]} == {"Data Mapping Document", "ETL pipeline"}

    deploy = next(d for d in result["created"] if d["title"] == "Production deployment")
    client.patch(f"/deliverables/{deploy['deliverable_id']}", json={"review_state": "rejected"})
    again = client.post(f"/projects/{test_project}/deliverables/suggest").json()  # the model offers it again...
    assert again["created"] == [] and again["skipped_existing"] == 3  # ...but accepted + rejected ones are never re-proposed
    assert "Production deployment" not in [d["title"] for d in client.get(f"/projects/{test_project}/deliverables").json()["deliverables"]]


def test_suggest_endpoint_reports_a_model_failure_as_502(test_project, monkeypatch):
    upsert_source_document(SOW, "sow", test_project)

    def boom(**kwargs):
        raise TimeoutError("down")
    monkeypatch.setattr("apps.utils.deliverable_ai.complete", boom)
    assert client.post(f"/projects/{test_project}/deliverables/suggest").status_code == 502


# ------------------------------------------------------------------ task 3: assignment

def _deliverables():
    return [
        {"deliverable_id": "d-41", "number": "4.1", "title": "Data Source Integration Module", "owner": "Jordan Mills"},
        {"deliverable_id": "d-46", "number": "4.6", "title": "Integration Testing Report", "owner": None},
    ]


def test_a_task_citing_exactly_one_known_deliverable_number_is_assigned_without_the_model(monkeypatch):
    calls = _fake_llm(monkeypatch, {"assignments": []})
    tasks = [{"task_id": "t1", "task_text": "Deliver Integration Testing Report (Deliverable 4.6)", "owner": None}]
    [p] = propose_assignments(_deliverables(), tasks)
    assert (p["deliverable_id"], p["method"], p["confidence"]) == ("d-46", "number", "high") and calls == []


def test_several_or_unknown_numbers_fall_through_to_the_model(monkeypatch):
    _fake_llm(monkeypatch, {"assignments": [{"task": "T1", "deliverable": "D1", "confidence": "medium"},
                                            {"task": "T2", "deliverable": None, "confidence": "high"}]})
    tasks = [{"task_id": "t1", "task_text": "Test modules 4.1 and 4.6 together", "owner": None},
             {"task_id": "t2", "task_text": "Cites 9.9 which does not exist", "owner": None}]
    p1, p2 = propose_assignments(_deliverables(), tasks)
    assert (p1["deliverable_id"], p1["method"], p1["confidence"]) == ("d-41", "llm", "medium")
    assert (p2["deliverable_id"], p2["confidence"]) == (None, None)  # 'none' is a valid answer


def test_model_answers_that_make_no_sense_are_treated_as_unassigned(monkeypatch):
    _fake_llm(monkeypatch, {"assignments": [{"task": "T1", "deliverable": "D99", "confidence": "high"},
                                            {"task": "T2", "deliverable": "banana"},
                                            {"task": "T7", "deliverable": "D1"}]})
    tasks = [{"task_id": f"t{i}", "task_text": f"Task {i}", "owner": None} for i in (1, 2, 3)]
    assert [p["deliverable_id"] for p in propose_assignments(_deliverables(), tasks)] == [None, None, None]


def test_large_task_lists_are_batched_and_keep_their_order(monkeypatch):
    def answer(prompt):
        labels = re.findall(r"^T(\d+):", prompt, re.M)
        return {"assignments": [{"task": f"T{n}", "deliverable": "D2", "confidence": "high"} for n in labels]}
    calls = _fake_llm(monkeypatch, answer)
    tasks = [{"task_id": f"t{i:03d}", "task_text": f"Task number {i}", "owner": None} for i in range(120)]
    proposals = propose_assignments(_deliverables(), tasks)
    assert len(calls) == 3  # 50 + 50 + 20
    assert [p["task_id"] for p in proposals] == [t["task_id"] for t in tasks] and {p["deliverable_id"] for p in proposals} == {"d-46"}


def test_a_failed_batch_leaves_those_tasks_unassigned_instead_of_failing(monkeypatch):
    def boom(**kwargs):
        raise TimeoutError("down")
    monkeypatch.setattr("apps.utils.deliverable_ai.complete", boom)
    [p] = propose_assignments(_deliverables(), [{"task_id": "t1", "task_text": "Anything", "owner": None}])
    assert p["deliverable_id"] is None


def test_assignment_endpoints_end_to_end(test_project, monkeypatch):
    d = create_deliverable(test_project, "Integration Testing Report", number="4.6")
    suggested = create_deliverable(test_project, "Not accepted yet", review_state="suggested", origin="agent")
    t1 = str(_task(test_project, "Run integration tests (Deliverable 4.6)"))
    t2 = str(_task(test_project, "Something cross-cutting"))
    assert client.post(f"/projects/{test_project}/deliverables/suggest-assignments").status_code == 200

    proposals = client.post(f"/projects/{test_project}/deliverables/suggest-assignments").json()["proposals"]
    assert {p["task_id"]: p["method"] for p in proposals}[t1] == "number"
    ids = {p["deliverable_id"] for p in proposals if p["deliverable_id"]}
    assert str(suggested["deliverable_id"]) not in ids  # only confirmed deliverables are candidates

    did = str(d["deliverable_id"])
    assert client.put(f"/projects/{test_project}/deliverables/assign", json={"assignments": [{"task_id": t1, "deliverable_id": did}]}).json() == {"changed": 1}
    assert next(x for x in list_deliverables(test_project) if str(x["deliverable_id"]) == did)["task_count"] == 1
    assert len(client.post(f"/projects/{test_project}/deliverables/suggest-assignments").json()["proposals"]) == 1  # only t2 is left

    # all-or-nothing: one bad row means nothing changes
    bad = client.put(f"/projects/{test_project}/deliverables/assign", json={"assignments": [
        {"task_id": t2, "deliverable_id": did}, {"task_id": "00000000-0000-0000-0000-000000000000", "deliverable_id": did}]})
    assert bad.status_code == 404
    assert next(x for x in list_deliverables(test_project) if str(x["deliverable_id"]) == did)["task_count"] == 1
    # a suggested (unconfirmed) deliverable cannot be assigned to
    assert client.put(f"/tasks/{t2}/deliverable", json={"deliverable_id": str(suggested["deliverable_id"])}).status_code == 404
    # single assign + clear
    assert client.put(f"/tasks/{t2}/deliverable", json={"deliverable_id": did}).status_code == 200
    assert client.put(f"/tasks/{t2}/deliverable", json={"deliverable_id": None}).status_code == 200
    assert client.put("/tasks/00000000-0000-0000-0000-000000000000/deliverable", json={"deliverable_id": None}).status_code == 404


def test_no_deliverables_means_no_assignment_proposals(test_project):
    _task(test_project, "A task")
    assert client.post(f"/projects/{test_project}/deliverables/suggest-assignments").status_code == 400


def test_promoting_a_task_assigns_it_to_the_deliverable_it_created_and_the_board_shows_it(test_project):
    t = str(_task(test_project, "Data mapping document", owner="Sofia Martins"))
    created = create_deliverables_from_tasks(test_project, [t])["created"][0]
    board = client.get(f"/projects/{test_project}/tasks").json()["tasks"]
    assert board[0]["deliverable"]["deliverable_id"] == str(created["deliverable_id"])
    assert client.put(f"/tasks/{t}/deliverable", json={"deliverable_id": None}).status_code == 200
    assert client.get(f"/projects/{test_project}/tasks").json()["tasks"][0]["deliverable"] is None


def test_a_suggestion_that_is_an_existing_deliverable_without_its_suffix_is_recognised_but_a_distinct_one_is_not(test_project):
    """Measured gap: same deliverable with/without '(...)' suffix scores 0.74-1.00, distinct deliverables <= 0.56."""
    from apps.db.db import store_suggested_deliverables
    create_deliverable(test_project, "Data Source Integration Module (codebase + unit tests)", number="4.1")
    create_deliverable(test_project, "Multi-cluster Data Aggregation Support (codebase + unit tests)", number="4.4")
    result = store_suggested_deliverables(test_project, [
        {"title": "Data Source Integration Module"},               # 4.1 reworded -> skipped
        {"title": "Multi-cluster Data Aggregation Support"},       # 4.4 reworded -> skipped
        {"title": "Report Generation Module"},                     # genuinely different -> kept
    ])
    assert [d["title"] for d in result["created"]] == ["Report Generation Module"] and result["skipped_existing"] == 2
