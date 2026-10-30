"""Hours storage and API: settings, estimates, the weekly timesheet, imports (replace, don't add; undo), chat tools and cleanup."""
import io
import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.agent.tool_registry import CHAT_TOOL_IMPLS
from apps.api import app
from apps.db.db import (
    delete_project, fetch_project_tasks_with_dependencies, insert_task, list_project_people, upsert_project_person,
)
from apps.db.hours_db import (
    apply_plan_import, apply_timesheet_import, create_import_record, fetch_time_entries, get_hours_settings, get_timesheet_week,
    project_hours_report, save_timesheet_week, set_hours_settings, set_person_capacity, set_task_hours, undo_import,
)

client = TestClient(app)
DATA = Path(__file__).resolve().parents[2] / "datasets" / "hours" / "forloop"
MON = date(2026, 9, 28)


def _task(project_id, text, owner=None, status="not_started"):
    return str(insert_task({"task_text": text, "normalized_task_text": text.lower(), "owner": owner, "status": status}, project_id))


def _person(project_id, name, role=None):
    upsert_project_person(project_id, name, f"{name.split()[0].lower()}@x.com", role)


@pytest.fixture
def project(test_project):
    _person(test_project, "Ana Diaz", "Backend Engineer")
    _person(test_project, "Bo Park", "QA Engineer")
    return test_project


# ---------------------------------------------------------------- settings, estimates, capacity

def test_settings_partial_update_and_validation(project):
    assert get_hours_settings(project) == {"budget_hours": None, "kickoff_date": None, "role_budgets": {}, "week_start_day": 0}
    set_hours_settings(project, budget_hours=700, kickoff_date=MON)
    s = set_hours_settings(project, role_budgets={"QA Engineer": 70})  # does not disturb the other fields
    assert s["budget_hours"] == 700 and s["kickoff_date"] == MON and s["role_budgets"] == {"QA Engineer": 70}
    assert set_hours_settings(project, budget_hours=None)["budget_hours"] is None
    with pytest.raises(ValueError):
        set_hours_settings(project, budget_hours=-1)


def test_task_estimate_validation_and_partial_update(project):
    t = _task(project, "Build login")
    out = set_task_hours(t, estimate_hours=20, plan_start_week=2, plan_finish_week=3)
    assert out["estimate_hours"] == 20 and out["plan_start_week"] == 2
    assert set_task_hours(t, hours_still_needed=5)["estimate_hours"] == 20  # untouched
    with pytest.raises(ValueError):
        set_task_hours(t, plan_finish_week=1)  # before the start week (2)
    with pytest.raises(ValueError):
        set_task_hours(t, estimate_hours=-3)
    with pytest.raises(LookupError):
        set_task_hours("00000000-0000-0000-0000-0000000000aa", estimate_hours=1)


def test_capacity_and_skills(project):
    set_person_capacity(project, "ana diaz", weekly_capacity_hours=32, skills=["Python", " python ", "AWS", ""])
    ana = next(p for p in list_project_people(project) if p["name"] == "Ana Diaz")
    assert ana["weekly_capacity_hours"] == 32 and ana["skills"] == ["AWS", "Python"]
    with pytest.raises(ValueError):
        set_person_capacity(project, "Ana Diaz", weekly_capacity_hours=200)
    with pytest.raises(LookupError):
        set_person_capacity(project, "Nobody", weekly_capacity_hours=10)
    bo = next(p for p in list_project_people(project) if p["name"] == "Bo Park")
    assert bo["weekly_capacity_hours"] is None and bo["skills"] == []


# ---------------------------------------------------------------- weekly timesheet

def test_saving_a_week_replaces_the_whole_week(project):
    a, b = _task(project, "A"), _task(project, "B")
    save_timesheet_week(project, "Ana Diaz", date(2026, 10, 1), [{"task_id": a, "hours": 10}, {"task_id": None, "hours": 4, "note": "meetings"}])
    saved = save_timesheet_week(project, "ana diaz", date(2026, 9, 30), [{"task_id": b, "hours": 6}, {"task_id": b, "hours": 2}, {"task_id": a, "hours": 0}])
    assert [(e["task_id"], e["hours"]) for e in saved] == [(b, 8.0)]  # merged, zero dropped, the earlier rows replaced
    assert {e["week_start"] for e in fetch_time_entries(project)} == {MON}  # any day of the week lands on its Monday


def test_timesheet_rejects_unknown_people_and_foreign_tasks(project):
    with pytest.raises(LookupError):
        save_timesheet_week(project, "Stranger", MON, [{"task_id": None, "hours": 1}])
    with pytest.raises(LookupError):
        save_timesheet_week(project, "Ana Diaz", MON, [{"task_id": "00000000-0000-0000-0000-0000000000bb", "hours": 1}])
    with pytest.raises(ValueError):
        save_timesheet_week(project, "Ana Diaz", MON, [{"task_id": None, "hours": -1}])


# ---------------------------------------------------------------- imports replace, never add

def _import(project, rows):
    return create_import_record(project, "timesheet", "x.csv", "flat", {"rows": rows})


def test_reimporting_replaces_instead_of_adding_and_undo_restores(project):
    t = _task(project, "A")
    first = _import(project, [])
    r1 = apply_timesheet_import(first, [{"person": "Ana Diaz", "week_start": MON, "task_id": t, "hours": 10, "source_ref": "r2"},
                                        {"person": "Ana Diaz", "week_start": MON, "task_id": t, "hours": 2, "source_ref": "r3"},
                                        {"person": "Ana Diaz", "week_start": MON, "task_id": None, "hours": 3}])
    assert r1["entries_written"] == 2 and r1["hours_total"] == 15  # same key summed inside one file
    second = _import(project, [])
    r2 = apply_timesheet_import(second, [{"person": "Ana Diaz", "week_start": MON, "task_id": t, "hours": 12}])  # cumulative re-send
    assert r2["replaced"] == 1
    assert sum(e["hours"] for e in fetch_time_entries(project)) == 15  # 12 + 3, not 27
    with pytest.raises(ValueError):
        apply_timesheet_import(second, [])  # an import can only be applied once
    undo_import(second)
    assert sorted(e["hours"] for e in fetch_time_entries(project)) == [3.0, 12.0]  # previous 12 restored? see below


def test_undo_of_an_import_that_was_overwritten_later_keeps_the_later_values(project):
    t = _task(project, "A")
    first, second = _import(project, []), _import(project, [])
    apply_timesheet_import(first, [{"person": "Ana Diaz", "week_start": MON, "task_id": t, "hours": 10}])
    apply_timesheet_import(second, [{"person": "Ana Diaz", "week_start": MON, "task_id": t, "hours": 8}])
    undo_import(first)  # the second import owns the row now
    assert [e["hours"] for e in fetch_time_entries(project)] == [8.0]
    undo_import(second)  # restores what the first import had written
    assert [e["hours"] for e in fetch_time_entries(project)] == [10.0]


def test_plan_import_updates_creates_and_undoes_including_settings(project):
    existing = _task(project, "Existing task", owner="Ana Diaz")
    set_task_hours(existing, estimate_hours=5)
    imp = create_import_record(project, "plan", "plan.xlsx", "plan", {"rows": []})
    res = apply_plan_import(imp, [
        {"action": "update", "task_id": existing, "estimate_hours": 20, "plan_start_week": 1, "plan_finish_week": 2},
        {"action": "create", "task_text": "Brand new task", "owner": "Bo Park", "estimate_hours": 8, "plan_start_week": 3, "plan_finish_week": 3},
    ], {"budget_hours": 500, "kickoff_date": MON})
    assert (res["tasks_updated"], res["tasks_created"]) == (1, 1)
    tasks = {t["task_text"]: t for t in fetch_project_tasks_with_dependencies(project)}
    assert tasks["Existing task"]["estimate_hours"] == 20 and tasks["Brand new task"]["estimate_hours"] == 8
    assert get_hours_settings(project)["budget_hours"] == 500
    undo_import(imp)
    tasks = {t["task_text"]: t for t in fetch_project_tasks_with_dependencies(project)}
    assert tasks["Existing task"]["estimate_hours"] == 5 and "Brand new task" not in tasks
    assert get_hours_settings(project)["budget_hours"] is None


# ---------------------------------------------------------------- the task board carries the same numbers as the Hours page

def test_task_board_rows_show_estimate_logged_and_left(project):
    t = _task(project, "Build login", status="in_progress")
    set_task_hours(t, estimate_hours=20)
    save_timesheet_week(project, "Ana Diaz", MON, [{"task_id": t, "hours": 6}])
    row = fetch_project_tasks_with_dependencies(project)[0]
    assert (row["estimate_hours"], row["logged_hours"], row["remaining_hours"]) == (20, 6, 14)
    save_timesheet_week(project, "Ana Diaz", MON, [{"task_id": t, "hours": 26}])
    assert fetch_project_tasks_with_dependencies(project)[0]["remaining_hours"] == 0
    set_task_hours(t, hours_still_needed=15)
    assert fetch_project_tasks_with_dependencies(project)[0]["remaining_hours"] == 15


# ---------------------------------------------------------------- API: preview, confirm, replace, undo

def _upload(project, name, kind):
    data = (DATA / name).read_bytes()
    return client.post(f"/projects/{project}/hours/import/preview", data={"kind": kind}, files={"file": (name, io.BytesIO(data))})


def _seed_forloop(project):
    exp = json.loads((DATA / "forloop-hours-expected.json").read_text())
    for p in exp["people"]:
        _person(project, p["name"], p["role"])
    ids = {}
    for t in exp["tasks"]:
        if t["db_task_text"]:
            ids[t["key"]] = _task(project, t["db_task_text"], owner=t["owner"], status=t["status_assumed"])
    return exp, ids


def test_timesheet_upload_preview_confirm_replace_and_undo(project, monkeypatch):
    monkeypatch.setattr("apps.utils.hours_import._llm_pick", lambda *a, **k: {})  # no model in tests
    _seed_forloop(project)
    set_hours_settings(project, budget_hours=700, kickoff_date=MON)

    pv = _upload(project, "forloop-timesheet-flat.csv", "timesheet")
    assert pv.status_code == 200, pv.text
    body = pv.json()
    assert body["layout"] == "flat" and body["summary"]["hours"] == 258 and body["summary"]["unknown_people"] == []
    assert {r["person"] for r in body["rows"] if r["person_raw"] in ("Ruiz, Elena", "M. Chen")} == {"Elena Ruiz", "Marcus Chen"}
    assert fetch_time_entries(project) == []  # a preview changes nothing

    ok = client.post(f"/hours/imports/{body['import_id']}/confirm", json={})
    assert ok.status_code == 200 and ok.json()["hours_total"] == 258
    assert client.post(f"/hours/imports/{body['import_id']}/confirm", json={}).status_code == 409  # not twice

    again = _upload(project, "forloop-timesheet-flat.csv", "timesheet").json()
    assert again["summary"]["will_replace"] > 0
    client.post(f"/hours/imports/{again['import_id']}/confirm", json={})
    assert client.get(f"/projects/{project}/hours").json()["logged_hours"] == 258  # same file twice: still 258

    client.post(f"/hours/imports/{again['import_id']}/undo")
    assert client.get(f"/projects/{project}/hours").json()["logged_hours"] == 258  # the first import's rows are back


def test_user_decisions_override_the_proposed_match_and_can_skip_rows(project, monkeypatch):
    monkeypatch.setattr("apps.utils.hours_import._llm_pick", lambda *a, **k: {})
    exp, ids = _seed_forloop(project)
    body = _upload(project, "forloop-timesheet-flat.csv", "timesheet").json()
    row = next(r for r in body["rows"] if r["label"] == "ETL pipeline - POS ingest")
    skip = next(r for r in body["rows"] if r["label"] == "Admin")
    res = client.post(f"/hours/imports/{body['import_id']}/confirm", json={"decisions": [
        {"idx": row["idx"], "task_id": ids["etl"]}, {"idx": skip["idx"], "skip": True}]})
    assert res.status_code == 200 and res.json()["skipped_rows"] == 1  # only the row that was skipped
    etl = next(t for t in client.get(f"/projects/{project}/hours").json()["tasks"] if t["task_id"] == ids["etl"])
    assert etl["logged_hours"] >= 14
    bad = client.post(f"/hours/imports/{_upload(project, 'forloop-timesheet-flat.csv', 'timesheet').json()['import_id']}/confirm",
                      json={"decisions": [{"idx": 0, "task_id": "00000000-0000-0000-0000-0000000000cc"}]})
    assert bad.status_code == 400


def test_plan_upload_sets_estimates_budget_and_kickoff(project, monkeypatch):
    monkeypatch.setattr("apps.utils.hours_import._llm_pick", lambda *a, **k: {})
    exp, ids = _seed_forloop(project)
    pv = _upload(project, "forloop-plan.xlsx", "plan").json()
    assert pv["summary"]["rows"] == 22 and pv["settings_new"]["budget_hours"] == 700
    exact = [r for r in pv["rows"] if r["match"] == "exact"]
    assert all(r["action"] == "update" for r in exact)
    res = client.post(f"/hours/imports/{pv['import_id']}/confirm", json={})
    assert res.status_code == 200
    s = client.get(f"/projects/{project}/hours/settings").json()
    assert s["budget_hours"] == 700 and s["kickoff_date"] == "2026-09-28" and len(s["role_budgets"]) == 8
    assert client.get(f"/projects/{project}/hours").json()["sum_of_task_estimates"] == 612


def test_a_file_that_is_not_a_timesheet_gets_a_clear_422(project):
    r = client.post(f"/projects/{project}/hours/import/preview", data={"kind": "timesheet"},
                    files={"file": ("x.csv", io.BytesIO(b"Colour,Shape\nred,round\n"))})
    assert r.status_code == 422 and "No hours" in r.json()["detail"]


# ---------------------------------------------------------------- API: manual entry, availability, budget finder

def test_timesheet_endpoints_round_trip(project):
    t = _task(project, "Build login", owner="Ana Diaz", status="in_progress")
    client.put(f"/tasks/{t}/hours", json={"estimate_hours": 10})
    put = client.put(f"/projects/{project}/timesheet", json={"person": "Ana Diaz", "week_start": "2026-10-01",
                                                              "entries": [{"task_id": t, "hours": 12}, {"task_id": None, "hours": 3}]})
    assert put.status_code == 200 and put.json()["week_start"] == "2026-09-28"
    got = client.get(f"/projects/{project}/timesheet", params={"person": "Ana Diaz", "week": "2026-09-29"}).json()
    row = next(x for x in got["tasks"] if x["task_id"] == t)
    assert (row["hours_this_week"], row["logged_hours"], row["estimate_hours"]) == (12, 12, 10) and got["other_hours"] == 3
    assert client.put(f"/projects/{project}/timesheet", json={"person": "Stranger", "week_start": "2026-10-01", "entries": []}).status_code == 404


def test_availability_endpoint(project):
    client.put(f"/projects/{project}/people/Ana Diaz/capacity", json={"weekly_capacity_hours": 40, "skills": ["python"]})
    r = client.get(f"/projects/{project}/availability", params={"from": "2026-10-26", "to": "2026-10-30", "skill": "backend"}).json()
    assert [p["name"] for p in r["people"]] == ["Ana Diaz"] and r["people"][0]["available_hours"] == 40
    assert client.get(f"/projects/{project}/availability", params={"from": "2026-10-30", "to": "2026-10-26"}).status_code == 400


def test_budget_suggestion_keeps_only_a_verbatim_quote_with_the_number(project, monkeypatch):
    from apps.db.db import upsert_source_document
    sow = "Scope: build it. Total effort is estimated at 1,200 hours over 8 weeks. Priya is available 20 hours per week."
    upsert_source_document(sow, "sow", project)

    def fake(answer):
        def complete(messages, **kw):
            class R:
                choices = [type("C", (), {"message": type("M", (), {"content": json.dumps(answer)})()})()]
            return R()
        return complete

    monkeypatch.setattr("apps.utils.hours_budget_ai.complete", fake({"budget_hours": 1200, "evidence": "Total effort is estimated at 1,200 hours"}))
    assert client.post(f"/projects/{project}/hours/budget/suggest").json()["budget_hours"] == 1200
    monkeypatch.setattr("apps.utils.hours_budget_ai.complete", fake({"budget_hours": 1500, "evidence": "Total effort is estimated at 1,500 hours"}))
    assert client.post(f"/projects/{project}/hours/budget/suggest").json() == {"budget_hours": None, "evidence": None}  # invented quote
    monkeypatch.setattr("apps.utils.hours_budget_ai.complete", fake({"budget_hours": 20, "evidence": "Priya is available 20 hours per week"}))
    assert client.post(f"/projects/{project}/hours/budget/suggest").json()["budget_hours"] == 20  # verbatim, so kept: the person reviews it


# ---------------------------------------------------------------- chat tools and cleanup

def test_chat_tools_return_compact_json_with_caveats(project):
    t = _task(project, "Build login", owner="Ana Diaz", status="in_progress")
    _task(project, "Unestimated thing", owner="Ana Diaz", status="in_progress")
    set_task_hours(t, estimate_hours=10)
    save_timesheet_week(project, "Ana Diaz", MON, [{"task_id": t, "hours": 12}])
    report = CHAT_TOOL_IMPLS["get_project_hours"](project)
    json.dumps(report, default=str)
    assert report["logged_hours"] == 12 and report["hours_left_in_budget"] is None
    assert any("No hours budget" in n for n in report["notes"])
    assert {x["task_text"] for x in report["tasks"]} == {"Build login", "Unestimated thing"}  # only the ones needing attention
    only = CHAT_TOOL_IMPLS["get_project_hours"](project, task_query="login")
    assert [x["task_text"] for x in only["tasks"]] == ["Build login"]
    free = CHAT_TOOL_IMPLS["find_available_people"](project, skill="QA", from_date="2026-10-26", to_date="2026-10-30")
    assert [p["name"] for p in free["people"]] == ["Bo Park"]
    assert "error" in CHAT_TOOL_IMPLS["find_available_people"](project, from_date="tomorrow")


def test_deleting_a_project_removes_its_hours_data(test_project):
    _person(test_project, "Ana Diaz")
    t = _task(test_project, "A")
    save_timesheet_week(test_project, "Ana Diaz", MON, [{"task_id": t, "hours": 5}])
    set_hours_settings(test_project, budget_hours=10)
    create_import_record(test_project, "timesheet", "x.csv", "flat", {})
    delete_project(test_project)
    assert fetch_time_entries(test_project) == [] and get_hours_settings(test_project)["budget_hours"] is None
    assert client.get(f"/projects/{test_project}/hours/imports").json() == {"imports": []}


# ---------------------------------------------------------------- tasks that change under logged hours

def test_deleting_a_project_with_task_hours_and_other_hours_for_the_same_week(test_project):
    """Deleting a task turns its entries into 'Other' entries; with 'Other' hours already there for the same person and week
    that used to violate the uniqueness key and make the project impossible to delete."""
    _person(test_project, "Ana Diaz")
    t = _task(test_project, "A")
    save_timesheet_week(test_project, "Ana Diaz", MON, [{"task_id": t, "hours": 5}, {"task_id": None, "hours": 2}])
    assert delete_project(test_project) == 1
    assert fetch_time_entries(test_project) == []


def test_merging_duplicate_tasks_moves_hours_and_fills_the_estimate(project):
    from apps.db.db import merge_tasks
    keep, dup = _task(project, "Build login"), _task(project, "Build the login page")
    set_task_hours(dup, estimate_hours=12, plan_start_week=2, plan_finish_week=3)
    save_timesheet_week(project, "Ana Diaz", MON, [{"task_id": keep, "hours": 4}, {"task_id": dup, "hours": 6}])  # same week: summed
    save_timesheet_week(project, "Bo Park", MON, [{"task_id": dup, "hours": 3}])  # only on the duplicate: moves
    merge_tasks(dup, keep)
    by_person = {(e["person"], e["task_id"]): e["hours"] for e in fetch_time_entries(project)}
    assert by_person == {("Ana Diaz", keep): 10.0, ("Bo Park", keep): 3.0}
    row = project_hours_report(project)["tasks"]
    assert [(t["task_text"], t["estimate_hours"], t["logged_hours"], t["plan_start_week"]) for t in row] == [("Build login", 12.0, 13.0, 2)]


def test_report_is_anchored_to_the_latest_logged_week_not_to_today(project):
    set_hours_settings(project, budget_hours=100, kickoff_date=MON)
    t = _task(project, "Build login", status="in_progress")
    set_task_hours(t, estimate_hours=20, plan_start_week=1, plan_finish_week=8)
    save_timesheet_week(project, "Ana Diaz", date(2026, 10, 12), [{"task_id": t, "hours": 5}])  # week 3
    r = project_hours_report(project)
    assert r["as_of"] == "2026-10-16" and r["burn_check"]["weeks_elapsed"] == 3.0  # Friday of week 3, however late it is now
    assert any("No hours have been logged since the week of 2026-10-12" in n for n in r["notes"])  # today is well past that
    assert project_hours_report(project, date(2026, 10, 2))["burn_check"]["weeks_elapsed"] == 1.0  # an explicit date still wins
    assert not any("No hours have been logged since" in n for n in project_hours_report(project, date(2026, 10, 2))["notes"])


def test_report_without_any_hours_uses_today(project):
    r = project_hours_report(project)
    assert r["as_of"] == date.today().isoformat()


# ---------------------------------------------------------------- what the chat tools and document search rely on

def test_dependency_tool_returns_names_and_statuses_not_ids(project):
    from apps.agent.tool_registry import CHAT_TOOL_IMPLS
    from apps.db.db import add_task_dependency
    etl = _task(project, "Build ETL pipeline", owner="Ana Diaz", status="completed")
    view = _task(project, "Build Sales Trends view", owner="Bo Park", status="in_progress")
    add_task_dependency(view, etl)
    assert CHAT_TOOL_IMPLS["get_project_dependencies"](project) == [{
        "task": "Build Sales Trends view", "task_owner": "Bo Park", "task_status": "in_progress",
        "depends_on": "Build ETL pipeline", "depends_on_owner": "Ana Diaz", "depends_on_status": "completed"}]


def test_project_rollup_counts_not_started_tasks(project):
    from apps.db.db import fetch_projects
    _task(project, "A", status="completed")
    _task(project, "B", status="in_progress")
    _task(project, "C", status="not_started")
    _task(project, "D", status=None)
    row = next(p for p in fetch_projects() if p["project_id"] == project)
    assert (row["completed_tasks"], row["in_progress_tasks"], row["not_started_tasks"]) == (1, 1, 2)


def test_a_document_stored_without_chunks_is_chunked_when_resubmitted_and_by_the_backfill(project):
    from apps.db.db import (
        backfill_missing_document_chunks, get_connection, search_document_chunks, upsert_source_document,
    )
    text = "The go-live date is the end of Week 8. The system must handle 200 concurrent users. " * 3
    doc_id = upsert_source_document(text, "sow", project)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM document_chunks WHERE doc_id = %s", (doc_id,))  # a document stored before search existed
    conn.commit()
    assert search_document_chunks(project, "go-live date") == []
    assert upsert_source_document(text, "sow", project) == doc_id  # resubmitting heals it
    assert search_document_chunks(project, "concurrent users")
    cur.execute("DELETE FROM document_chunks WHERE doc_id = %s", (doc_id,))
    conn.commit()
    assert backfill_missing_document_chunks() >= 1
    assert search_document_chunks(project, "go-live date")
    cur.close()
    conn.close()


# ---------------------------------------------------------------- week start (per project: 0 Monday ... 6 Sunday)

def test_week_start_day_is_validated(project):
    for bad in (7, -1, None):
        with pytest.raises(ValueError):
            set_hours_settings(project, week_start_day=bad)
    assert set_hours_settings(project, week_start_day=6)["week_start_day"] == 6
    assert set_hours_settings(project, budget_hours=100)["week_start_day"] == 6  # other updates leave it alone


def test_a_sunday_week_files_hours_under_the_sunday_that_starts_it(project):
    set_hours_settings(project, week_start_day=6)
    save_timesheet_week(project, "Ana Diaz", date(2026, 10, 28), [{"task_id": None, "hours": 5}])  # a Wednesday
    assert [e["week_start"] for e in fetch_time_entries(project)] == [date(2026, 10, 25)]
    assert get_timesheet_week(project, "Ana Diaz", date(2026, 10, 31))  # the Saturday of the same week finds it
    assert not get_timesheet_week(project, "Ana Diaz", date(2026, 11, 1))  # the next Sunday starts a new week


def test_changing_the_week_start_refiles_logged_hours_and_can_be_undone(project):
    save_timesheet_week(project, "Ana Diaz", MON, [{"task_id": None, "hours": 5}])
    save_timesheet_week(project, "Bo Park", date(2026, 10, 5), [{"task_id": None, "hours": 7}])
    total = lambda: sum(e["hours"] for e in fetch_time_entries(project))
    set_hours_settings(project, week_start_day=6)
    assert sorted(e["week_start"] for e in fetch_time_entries(project)) == [date(2026, 9, 27), date(2026, 10, 4)]  # still "the week of 28 Sep" and "of 5 Oct"
    set_hours_settings(project, week_start_day=5)  # Saturday
    assert sorted(e["week_start"] for e in fetch_time_entries(project)) == [date(2026, 9, 26), date(2026, 10, 3)]
    set_hours_settings(project, week_start_day=0)
    assert sorted(e["week_start"] for e in fetch_time_entries(project)) == [date(2026, 9, 28), date(2026, 10, 5)]
    assert total() == 12


def test_report_uses_the_projects_week_for_its_default_date(project):
    set_hours_settings(project, budget_hours=100, kickoff_date=MON, week_start_day=6)
    t = _task(project, "Build login", status="in_progress")
    set_task_hours(t, estimate_hours=20, plan_start_week=1, plan_finish_week=8)
    save_timesheet_week(project, "Ana Diaz", date(2026, 10, 14), [{"task_id": t, "hours": 5}])  # Sunday week of 11 Oct
    r = project_hours_report(project)
    assert r["week_start_day"] == 6 and r["as_of"] == "2026-10-16" and r["burn_check"]["weeks_elapsed"] == 3.0  # its Friday, week 3
    assert any("the week of 2026-10-11" in n for n in r["notes"])


def test_week_start_api_round_trip(project):
    assert client.get(f"/projects/{project}/hours/settings").json()["week_start_day"] == 0
    assert client.put(f"/projects/{project}/hours/settings", json={"week_start_day": 6}).json()["week_start_day"] == 6
    assert client.put(f"/projects/{project}/hours/settings", json={"week_start_day": 9}).status_code == 400
    got = client.get(f"/projects/{project}/timesheet", params={"person": "Ana Diaz", "week": "2026-10-28"}).json()
    assert got["week_start"] == "2026-10-25"
    put = client.put(f"/projects/{project}/timesheet", json={"person": "Ana Diaz", "week_start": "2026-10-28", "entries": []})
    assert put.json()["week_start"] == "2026-10-25"
