"""Deliverables register: manual creation, promotion from tasks, the pipeline node, and RACI by deliverable."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api import app
from apps.db.db import (
    create_deliverable,
    create_deliverables_from_tasks,
    delete_deliverable,
    delete_project,
    fetch_raci_matrix,
    insert_task,
    list_deliverables,
    set_deliverable_raci,
    update_deliverable,
    upsert_document_deliverables,
)
from apps.db.db import import_document_raid_items, list_raid_items
from apps.nodes.sow_registers_node import task_sow_registers_node
from apps.utils.sow_sections import parse_raid_sections
from apps.utils.deliverables import parse_deliverables_section

client = TestClient(app)
SYNTHETIC_SOW = (Path(__file__).resolve().parents[2] / "datasets" / "simulation-ai-x-vmware" / "sow.txt").read_text()


def _task(project_id, text, owner=None, deadline=None, status=None):
    return insert_task({"task_text": text, "normalized_task_text": text.lower(), "owner": owner, "deadline": deadline,
                        "status": status}, project_id)


# ------------------------------------------------------------------ CRUD

def test_create_update_delete_and_natural_number_order(test_project):
    create_deliverable(test_project, "Tenth", number="4.10")
    second = create_deliverable(test_project, "Second", number="4.2", owner="Jordan Mills", deadline="Week 4")
    create_deliverable(test_project, "Unnumbered")
    assert [d["title"] for d in list_deliverables(test_project)] == ["Second", "Tenth", "Unnumbered"]

    updated = update_deliverable(str(second["deliverable_id"]), {"status": "in_progress", "number": "4.3"})
    assert updated["status"] == "in_progress" and updated["number"] == "4.3"
    assert update_deliverable("00000000-0000-0000-0000-000000000000", {"status": "completed"}) is None
    assert delete_deliverable(str(second["deliverable_id"])) is True
    assert delete_deliverable(str(second["deliverable_id"])) is False


def test_validation(test_project):
    create_deliverable(test_project, "First", number="1.1")
    with pytest.raises(ValueError):
        create_deliverable(test_project, "Duplicate number", number="1.1")
    with pytest.raises(ValueError):
        create_deliverable(test_project, "   ")
    with pytest.raises(ValueError):
        create_deliverable(test_project, "Bad status", status="winning")
    other = create_deliverable(test_project, "Other", number="1.2")
    with pytest.raises(ValueError):
        update_deliverable(str(other["deliverable_id"]), {"number": "1.1"})
    with pytest.raises(ValueError):
        update_deliverable(str(other["deliverable_id"]), {"origin": "document"})  # not editable


def test_deleting_a_project_removes_its_deliverables(test_project):
    _task(test_project, "Anything")
    create_deliverable(test_project, "Goes away")
    delete_project(test_project)
    assert list_deliverables(test_project) == []


# ------------------------------------------------------------------ promote tasks

def test_promoting_tasks_copies_fields_keeps_the_task_and_never_duplicates(test_project):
    t1 = _task(test_project, "Data mapping document", owner="Sofia Martins", deadline="end of Week 2", status="in_progress")
    t2 = _task(test_project, "Training session", owner=None, status="weird-status")
    result = create_deliverables_from_tasks(test_project, [t1, t2])
    assert len(result["created"]) == 2 and result["skipped"] == []

    by_title = {d["title"]: d for d in list_deliverables(test_project)}
    d1 = by_title["Data mapping document"]
    assert (d1["owner"], d1["deadline"], d1["status"], d1["origin"], str(d1["source_task_id"])) == (
        "Sofia Martins", "end of Week 2", "in_progress", "task", str(t1))
    assert by_title["Training session"]["status"] == "not_started"  # unknown task status falls back

    again = create_deliverables_from_tasks(test_project, [t1])
    assert again["created"] == [] and again["skipped"] == [t1]
    assert len(list_deliverables(test_project)) == 2


def test_promoting_an_unknown_task_creates_nothing(test_project):
    t = _task(test_project, "Real task")
    with pytest.raises(LookupError):
        create_deliverables_from_tasks(test_project, [t, "00000000-0000-0000-0000-000000000000"])
    assert list_deliverables(test_project) == []  # all-or-nothing


# ------------------------------------------------------------------ document parse + pipeline node

def test_the_pipeline_node_fills_the_register_from_a_sow_and_is_idempotent(test_project):
    state = {"document_type": "sow", "raw_input": SYNTHETIC_SOW, "project_id": test_project}
    task_sow_registers_node(state)
    rows = list_deliverables(test_project)
    assert [d["number"] for d in rows] == [f"4.{n}" for n in range(1, 12)] and {d["origin"] for d in rows} == {"document"}

    update_deliverable(str(rows[0]["deliverable_id"]), {"owner": "Someone Else"})  # a person's edit...
    task_sow_registers_node(state)  # ...survives a re-run, and nothing is duplicated
    again = list_deliverables(test_project)
    assert len(again) == 11 and again[0]["owner"] == "Someone Else"


def test_the_node_ignores_meeting_minutes_and_sows_without_a_section(test_project):
    task_sow_registers_node({"document_type": "mom", "raw_input": SYNTHETIC_SOW, "project_id": test_project})
    task_sow_registers_node({"document_type": "sow", "raw_input": "Phase 1\n- Marcus will build it", "project_id": test_project})
    assert list_deliverables(test_project) == []


def test_the_node_never_fails_a_run(test_project, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("apps.nodes.sow_registers_node.upsert_document_deliverables", boom)
    state = {"document_type": "sow", "raw_input": SYNTHETIC_SOW, "project_id": test_project}
    assert task_sow_registers_node(state) is state


# ------------------------------------------------------------------ RACI by deliverable

def test_deliverable_raci_uses_the_owner_as_implied_responsible_and_flags_started_rows(test_project):
    items = parse_deliverables_section(SYNTHETIC_SOW)
    upsert_document_deliverables(test_project, items)
    matrix = fetch_raci_matrix(test_project, "deliverable")
    assert [r["number"] for r in matrix["rows"]] == [f"4.{n}" for n in range(1, 12)]  # natural order
    multi = next(r for r in matrix["rows"] if r["number"] == "4.4")
    assert [(a["person"], a["implied"]) for a in multi["assignments"]] == [("Jordan Mills", True), ("Priya Nair", True)]
    assert all(r["warnings"] == [] for r in matrix["rows"])  # untouched rows are not flagged

    set_deliverable_raci(multi["subject_id"], "Sarah Okonkwo", ["A"])
    started = next(r for r in fetch_raci_matrix(test_project, "deliverable")["rows"] if r["number"] == "4.4")
    assert started["warnings"] == []  # owners are implied R, Sarah is the single A
    set_deliverable_raci(multi["subject_id"], "Sarah Okonkwo", [])
    assert next(r for r in fetch_raci_matrix(test_project, "deliverable")["rows"] if r["number"] == "4.4")["assignments"][0]["implied"]


def test_task_and_deliverable_raci_are_independent(test_project):
    t = _task(test_project, "Some task", owner="Jordan Mills")
    d = create_deliverable(test_project, "Some deliverable", owner="Priya Nair")
    assert [r["title"] for r in fetch_raci_matrix(test_project, "task")["rows"]] == ["Some task"]
    assert [r["title"] for r in fetch_raci_matrix(test_project, "deliverable")["rows"]] == ["Some deliverable"]
    with pytest.raises(LookupError):
        set_deliverable_raci(str(t), "Jordan Mills", ["R"])  # a task id is not a deliverable id
    assert set_deliverable_raci(str(d["deliverable_id"]), "Jordan Mills", ["R", "A"]) == ["R", "A"]
    with pytest.raises(ValueError):
        fetch_raci_matrix(test_project, "bogus")


# ------------------------------------------------------------------ HTTP

def test_deliverable_endpoints(test_project):
    created = client.post(f"/projects/{test_project}/deliverables", json={"title": "Final report", "number": "9.1", "owner": "Marcus Webb"})
    assert created.status_code == 200
    did = created.json()["deliverable_id"]
    assert client.get(f"/projects/{test_project}/deliverables").json()["deliverables"][0]["title"] == "Final report"

    assert client.patch(f"/deliverables/{did}", json={"status": "completed", "deadline": "Week 8"}).json()["status"] == "completed"
    assert client.post(f"/projects/{test_project}/deliverables", json={"title": "Dup", "number": "9.1"}).status_code == 400
    assert client.post(f"/projects/{test_project}/deliverables", json={"title": "x", "status": "winning"}).status_code == 422
    assert client.patch("/deliverables/00000000-0000-0000-0000-000000000000", json={"title": "x"}).status_code == 404

    assert client.put(f"/deliverables/{did}/raci", json={"person": "Marcus Webb", "roles": ["A", "R"]}).json()["roles"] == ["R", "A"]
    row = client.get(f"/projects/{test_project}/raci?subject=deliverable").json()["rows"][0]
    assert row["title"] == "Final report" and row["assignments"][0]["roles"] == ["R", "A"]
    assert client.get(f"/projects/{test_project}/raci?subject=bogus").status_code == 422
    assert client.put("/deliverables/00000000-0000-0000-0000-000000000000/raci", json={"person": "x", "roles": ["R"]}).status_code == 404

    assert client.delete(f"/deliverables/{did}").status_code == 200
    assert client.delete(f"/deliverables/{did}").status_code == 404


def test_promote_endpoint(test_project):
    t = str(_task(test_project, "Data mapping document", owner="Sofia Martins"))
    resp = client.post(f"/projects/{test_project}/deliverables/from-tasks", json={"task_ids": [t]})
    assert resp.status_code == 200 and resp.json()["created"][0]["title"] == "Data mapping document"
    assert client.post(f"/projects/{test_project}/deliverables/from-tasks", json={"task_ids": [t]}).json()["skipped"] == [t]
    assert client.post(f"/projects/{test_project}/deliverables/from-tasks",
                       json={"task_ids": ["00000000-0000-0000-0000-000000000000"]}).status_code == 404


# ------------------------------------------------------------------ RAID imported from a SOW's numbered lists

def test_the_node_imports_assumptions_and_dependencies_idempotently(test_project):
    state = {"document_type": "sow", "raw_input": SYNTHETIC_SOW, "project_id": test_project}
    task_sow_registers_node(state)
    items = list_raid_items(test_project)
    assert sorted((i["item_type"] for i in items)) == ["assumption"] * 9 + ["dependency"] * 6
    assert {(i["origin"], i["review_state"], i["status"]) for i in items} == {("document", "confirmed", "open")}
    long_one = next(i for i in items if i["description"] and "Delays in UAT sign-off" in i["description"])
    assert long_one["title"].startswith("NovaTech will complete UAT") and len(long_one["title"]) <= 110

    task_sow_registers_node(state)  # same document again: nothing new
    assert len(list_raid_items(test_project)) == 15


def test_a_different_document_with_the_same_numbering_is_not_skipped(test_project):
    items = parse_raid_sections("Assumptions\n1. Alpha\n2. Beta\n")
    first = import_document_raid_items(test_project, "doc-one", items)
    second = import_document_raid_items(test_project, "doc-two", items)
    again = import_document_raid_items(test_project, "doc-one", items)
    assert (len(first), len(second), len(again)) == (2, 2, 0)


def test_one_failing_import_does_not_block_the_other(test_project, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("apps.nodes.sow_registers_node.import_document_raid_items", boom)
    state = {"document_type": "sow", "raw_input": SYNTHETIC_SOW, "project_id": test_project}
    assert task_sow_registers_node(state) is state
    assert len(list_deliverables(test_project)) == 11  # the deliverables part still ran
    assert list_raid_items(test_project) == []


def test_imported_raid_items_keep_the_documents_order(test_project):
    items = parse_raid_sections("ASSUMPTIONS\n1. First\n2. Second\n3. Third\n4. Fourth\n")
    import_document_raid_items(test_project, "doc-order", items)
    assert [i["title"] for i in list_raid_items(test_project)] == ["First", "Second", "Third", "Fourth"]  # not a random tie
