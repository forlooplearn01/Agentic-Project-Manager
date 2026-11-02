"""The plan maintained in the app: baseline, change history, adding and removing tasks, first-upload-only, the plan export and the chat tool."""
import io
import itertools
import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from apps.agent.tool_registry import CHAT_TOOL_IMPLS
from apps.api import app
from apps.db.db import add_task_dependency, fetch_project_tasks_with_dependencies, insert_task, list_project_people, merge_tasks, upsert_project_person
from apps.db.hours_db import project_hours_report, save_timesheet_week, set_hours_settings, set_task_hours, undo_import
from apps.db.plan_db import create_plan_task, list_plan_changes, plan_summary, remove_plan_task, set_plan_baseline

client = TestClient(app)
DATA = Path(__file__).resolve().parents[2] / "datasets" / "hours" / "forloop"
MON = date(2026, 9, 28)


def _task(project_id, text, owner=None, status="not_started"):
    return str(insert_task({"task_text": text, "normalized_task_text": text.lower(), "owner": owner, "status": status}, project_id))


@pytest.fixture
def plan(test_project):
    """A project with three tasks: A and B have estimates, C has none."""
    upsert_project_person(test_project, "Ana Diaz", "ana@x.com", "Engineer")
    a, b, c = _task(test_project, "Task A"), _task(test_project, "Task B"), _task(test_project, "Task C")
    set_task_hours(a, _log=False, estimate_hours=20, plan_start_week=1, plan_finish_week=2, phase="Phase 1")
    set_task_hours(b, _log=False, estimate_hours=10, plan_start_week=2, plan_finish_week=3, phase="Phase 2")
    return {"project": test_project, "a": a, "b": b, "c": c}


def _rows(project):
    return {t["task_text"]: t for t in fetch_project_tasks_with_dependencies(project)}


# ---------------------------------------------------------------- baseline

def test_the_baseline_is_the_plan_as_it_stood_and_can_be_set_once(plan):
    p = plan["project"]
    assert plan_summary(p)["has_plan"] and plan_summary(p)["baseline_set_at"] is None
    s = set_plan_baseline(p)
    assert (s["baseline_hours"], s["current_hours"], s["change_hours"]) == (30, 30, 0)
    rows = _rows(p)
    assert rows["Task A"]["baseline_estimate_hours"] == 20 and rows["Task A"]["baseline_start_week"] == 1
    assert rows["Task C"]["baseline_estimate_hours"] is None and not rows["Task A"]["added_after_baseline"]
    with pytest.raises(ValueError, match="already has a baseline"):
        set_plan_baseline(p)


def test_a_baseline_needs_something_to_baseline(test_project):
    _task(test_project, "Nothing planned")
    assert plan_summary(test_project)["has_plan"] is False
    with pytest.raises(ValueError, match="estimates or planned weeks"):
        set_plan_baseline(test_project)


# ---------------------------------------------------------------- change history

def test_edits_after_the_baseline_show_the_variance_and_are_logged(plan):
    p = plan["project"]
    set_plan_baseline(p)
    set_task_hours(plan["a"], estimate_hours=28, plan_finish_week=3, phase="Discovery")
    set_task_hours(plan["a"], estimate_hours=28)  # same value again: nothing to record
    row = _rows(p)["Task A"]
    assert (row["estimate_hours"], row["baseline_estimate_hours"], row["plan_finish_week"], row["baseline_finish_week"]) == (28, 20, 3, 2)
    s = plan_summary(p)
    assert (s["baseline_hours"], s["current_hours"], s["change_hours"]) == (30, 38, 8)
    changes = [(c["field"], c["old_value"], c["new_value"]) for c in list_plan_changes(p) if c["task_text"] == "Task A"]
    assert sorted(changes) == sorted([("estimate_hours", "20", "28"), ("plan_finish_week", "2", "3"), ("phase", "Phase 1", "Discovery")])


def test_budget_and_kickoff_changes_are_in_the_history(plan):
    p = plan["project"]
    set_hours_settings(p, budget_hours=700, kickoff_date=MON)
    set_hours_settings(p, budget_hours=700)  # unchanged
    seen = {(c["field"], c["old_value"], c["new_value"]) for c in list_plan_changes(p)}
    assert ("budget_hours", None, "700") in seen and ("kickoff_date", None, "2026-09-28") in seen
    assert sum(1 for c in list_plan_changes(p) if c["field"] == "budget_hours") == 1


def test_phase_is_cleaned_and_limited(plan):
    set_task_hours(plan["c"], phase="  Phase   3  ")
    assert _rows(plan["project"])["Task C"]["phase"] == "Phase 3"
    set_task_hours(plan["c"], phase="  ")
    assert _rows(plan["project"])["Task C"]["phase"] is None
    with pytest.raises(ValueError):
        set_task_hours(plan["c"], phase="x" * 81)


# ---------------------------------------------------------------- add and remove

def test_a_task_added_after_the_baseline_is_scope_growth(plan):
    p = plan["project"]
    set_plan_baseline(p)
    new = create_plan_task(p, "  New   thing ", owner="Zed Quinn", phase="Phase 2", estimate_hours=12, plan_start_week=4, plan_finish_week=5)
    row = _rows(p)["New thing"]
    assert row["added_after_baseline"] and row["estimate_hours"] == 12 and row["baseline_estimate_hours"] is None and row["status"] == "not_started"
    assert any(x["name"] == "Zed Quinn" for x in list_project_people(p))  # the roster builds itself from owners, as in the pipeline
    s = plan_summary(p)
    assert (s["tasks_added"], s["change_hours"]) == (1, 12)
    added = [c for c in list_plan_changes(p) if c["field"] == "task_added"]
    assert [(c["task_text"], c["new_value"]) for c in added] == [("New thing", "12")]
    with pytest.raises(ValueError, match="already exists"):
        create_plan_task(p, "new THING")
    with pytest.raises(ValueError):
        create_plan_task(p, "   ")
    assert new["task_text"] == "New thing"


def test_an_invalid_new_task_leaves_nothing_behind(plan):
    p = plan["project"]
    before = len(_rows(p))
    with pytest.raises(ValueError):
        create_plan_task(p, "Bad numbers", estimate_hours=-5)
    with pytest.raises(ValueError):
        create_plan_task(p, "Bad weeks", plan_start_week=5, plan_finish_week=2)
    assert len(_rows(p)) == before


def test_a_task_created_by_a_document_run_after_the_baseline_is_listed_too(plan):
    p = plan["project"]
    set_plan_baseline(p)
    _task(p, "From the minutes")  # created by something other than the plan
    assert plan_summary(p)["tasks_added"] == 1
    assert [(c["field"], c["task_text"], c["source"]) for c in list_plan_changes(p) if c["field"] == "task_added"] == [("task_added", "From the minutes", "document")]


def test_removing_a_task_keeps_its_hours_in_the_total_and_its_baseline_in_the_comparison(plan):
    p = plan["project"]
    set_hours_settings(p, budget_hours=100, kickoff_date=MON)
    add_task_dependency(plan["a"], plan["b"])
    save_timesheet_week(p, "Ana Diaz", MON, [{"task_id": plan["b"], "hours": 6}, {"task_id": None, "hours": 2}])
    set_plan_baseline(p)
    gone = remove_plan_task(plan["b"])
    assert gone["task_text"] == "Task B" and gone["logged_hours"] == 6
    assert "Task B" not in _rows(p) and _rows(p)["Task A"]["depends_on"] == []  # no "unknown task" link left
    s = plan_summary(p)
    assert (s["baseline_hours"], s["current_hours"], s["change_hours"], s["tasks_removed"]) == (30, 20, -10, 1)
    r = project_hours_report(p)
    assert r["logged_hours"] == 8 and r["hours_not_tied_to_a_task"] == 8  # the 6 h moved to "not on a task"; none lost
    assert [(c["field"], c["old_value"]) for c in list_plan_changes(p) if c["field"] == "task_removed"] == [("task_removed", "10")]
    with pytest.raises(LookupError):
        remove_plan_task(plan["b"])  # already gone


def test_merging_duplicates_keeps_the_baseline_and_phase(plan):
    p = plan["project"]
    set_plan_baseline(p)
    dup = _task(p, "Task A again")
    merge_tasks(plan["a"], dup)  # A (planned, baselined) is merged INTO the new duplicate
    survivor = _rows(p)["Task A again"]
    assert (survivor["estimate_hours"], survivor["baseline_estimate_hours"], survivor["phase"]) == (20, 20, "Phase 1")


# ---------------------------------------------------------------- first upload only, and import / undo

def _upload(project, name, kind):
    return client.post(f"/projects/{project}/hours/import/preview", data={"kind": kind}, files={"file": (name, io.BytesIO((DATA / name).read_bytes()))})


def test_the_first_plan_import_sets_phases_and_the_baseline_and_a_second_is_refused(test_project, monkeypatch):
    monkeypatch.setattr("apps.utils.hours_import._llm_pick", lambda *a, **k: {})
    exp = json.loads((DATA / "forloop-hours-expected.json").read_text())
    for t in exp["tasks"]:
        if t["db_task_text"]:
            _task(test_project, t["db_task_text"], owner=t["owner"])
    pv = _upload(test_project, "forloop-plan.xlsx", "plan")
    assert pv.status_code == 200
    done = client.post(f"/hours/imports/{pv.json()['import_id']}/confirm", json={})
    assert done.status_code == 200
    s = client.get(f"/projects/{test_project}/plan/summary").json()
    assert s["baseline_set_at"] and s["baseline_hours"] == 612 == s["current_hours"] and s["tasks_added"] == 0
    rows = _rows(test_project)
    assert rows["Lead technical discovery session with Calderwick's IT team"]["phase"] == "Phase 1: Discovery & Data Integration"
    assert {c["field"] for c in list_plan_changes(test_project)} >= {"plan_imported", "baseline_set", "budget_hours", "kickoff_date"}
    second = _upload(test_project, "forloop-plan.xlsx", "plan")
    assert second.status_code == 409 and "already has a plan" in second.json()["detail"]


def test_undoing_the_first_plan_import_removes_the_baseline_it_set(test_project, monkeypatch):
    monkeypatch.setattr("apps.utils.hours_import._llm_pick", lambda *a, **k: {})
    _task(test_project, "Lead technical discovery session with Calderwick's IT team", owner="Elena Ruiz")
    pv = _upload(test_project, "forloop-plan.xlsx", "plan").json()
    client.post(f"/hours/imports/{pv['import_id']}/confirm", json={})
    assert plan_summary(test_project)["baseline_set_at"]
    undo_import(pv["import_id"])
    s = plan_summary(test_project)
    assert s["baseline_set_at"] is None and not s["has_plan"]  # back to "no plan": the upload is offered again
    assert "import_undone" in {c["field"] for c in list_plan_changes(test_project)}


# ---------------------------------------------------------------- API

def test_plan_api_round_trip(plan):
    p = plan["project"]
    assert client.get(f"/projects/{p}/plan/summary").json()["has_plan"] is True
    assert client.post(f"/projects/{p}/plan/baseline").status_code == 200
    assert client.post(f"/projects/{p}/plan/baseline").status_code == 409
    made = client.post(f"/projects/{p}/plan/tasks", json={"task_text": "Added in the app", "phase": "Phase 2", "estimate_hours": 8})
    assert made.status_code == 200
    assert client.post(f"/projects/{p}/plan/tasks", json={"task_text": "added in the APP"}).status_code == 409
    assert client.post(f"/projects/{p}/plan/tasks", json={"task_text": "Bad", "estimate_hours": -1}).status_code == 400
    assert client.put(f"/tasks/{plan['a']}/hours", json={"estimate_hours": 25, "phase": "Build"}).status_code == 200
    fields = {c["field"] for c in client.get(f"/projects/{p}/plan/changes").json()["changes"]}
    assert {"task_added", "estimate_hours", "phase", "baseline_set"} <= fields
    assert client.delete(f"/tasks/{made.json()['task_id']}").status_code == 200
    assert client.delete(f"/tasks/{made.json()['task_id']}").status_code == 404


def test_a_baseline_cannot_be_set_on_an_empty_plan(test_project):
    _task(test_project, "Unplanned")
    assert client.post(f"/projects/{test_project}/plan/baseline").status_code == 400


# ---------------------------------------------------------------- export and chat

def test_export_is_a_plan_workbook_with_summary_and_history(plan):
    p = plan["project"]
    set_plan_baseline(p)
    set_task_hours(plan["a"], estimate_hours=28)
    create_plan_task(p, "=HYPERLINK(\"http://evil\")", estimate_hours=5, phase="Phase 2")
    resp = client.get(f"/projects/{p}/export")
    assert resp.status_code == 200 and resp.headers["content-disposition"].endswith(f'{p}-plan.xlsx"')
    wb = load_workbook(io.BytesIO(resp.content))
    assert wb.sheetnames == ["Plan", "Summary", "Change history"]
    plan_rows = [[c.value for c in row] for row in wb["Plan"].iter_rows(min_row=2)]
    header = [c.value for c in wb["Plan"][1]]
    task_a = next(r for r in plan_rows if r[1] == "Task A")
    assert (task_a[header.index("Phase")], task_a[header.index("Estimate (h)")], task_a[header.index("Baseline (h)")], task_a[header.index("Change vs baseline (h)")]) == ("Phase 1", 28, 20, 8)
    phases = [r[0] or "" for r in plan_rows]
    runs = [k for k, _ in itertools.groupby(phases)]
    assert len(runs) == len(set(runs)) and runs[-1] == "" == phases[-1]  # each phase is one block, and the unphased task (C) comes last
    assert runs[:2] == ["Phase 1", "Phase 2"]  # blocks in the order the phases start
    evil = next(r for r in plan_rows if r[1].startswith("=HYPERLINK"))
    assert next(c for c in wb["Plan"]["B"] if c.value == evil[1]).data_type == "s"  # shown as text, never run as a formula
    summary = {r[0].value: r[1].value for r in wb["Summary"].iter_rows(min_row=2)}
    assert summary["Baseline estimate (h)"] == 30 and summary["Current estimate (h)"] == 43 and summary["Tasks added since baseline"] == 1
    history = [r[1].value for r in wb["Change history"].iter_rows(min_row=2)]
    assert "Estimate" in history and "Task added" in history and "Baseline set" in history


def test_export_of_an_unknown_project_is_a_404():
    assert client.get("/projects/no-such-project-xyz/export").status_code == 404


def test_the_chat_tool_reports_the_plan_against_its_baseline(plan):
    p = plan["project"]
    empty = CHAT_TOOL_IMPLS["get_plan_changes"](p)
    assert empty["summary"]["baseline_set_at"] is None and empty["changes_since_baseline"] == []
    set_task_hours(plan["a"], estimate_hours=21)  # before the baseline: part of drawing up the plan, not a change to it
    set_plan_baseline(p)
    assert CHAT_TOOL_IMPLS["get_plan_changes"](p)["changes_since_baseline"] == []  # nothing has moved yet
    set_task_hours(plan["a"], estimate_hours=28, plan_finish_week=3)
    report = CHAT_TOOL_IMPLS["get_plan_changes"](p)
    json.dumps(report, default=str)
    assert report["summary"]["change_hours"] == 7  # baseline was 21 + 10, now 28 + 10
    by_change = {c["change"]: (c["task"], c["from"], c["to"]) for c in report["changes_since_baseline"]}
    assert by_change == {"Estimate": ("Task A", "21 h", "28 h"), "Finish week": ("Task A", "week 2", "week 3")}  # units, never "6 hours" for a week
