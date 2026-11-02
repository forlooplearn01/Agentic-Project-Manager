"""The starter plan file made from the SOW's tasks (apps/utils/plan_starter.py)."""
import shutil

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from psycopg2.extras import RealDictCursor

from apps.api import app
from apps.db.db import get_connection, insert_task
from apps.db.hours_db import set_hours_settings
from apps.db.suggestions_db import create_suggestion, list_suggestions
from apps.utils import plan_merge, plan_starter, project_folders as pfd, timesheet_watcher as tw
from apps.utils.hours_import import parse_plan

client = TestClient(app)


@pytest.fixture
def world(test_project, tmp_path, monkeypatch):
    monkeypatch.setattr("apps.utils.hours_import._llm_pick", lambda *a, **k: {})
    monkeypatch.setattr(tw, "ROOT", tmp_path)
    monkeypatch.setattr(plan_merge, "jira_sync", lambda *a, **k: {"status": "disabled"})
    return {"p": test_project, "root": tmp_path, "plan_dir": tmp_path / test_project / "plan"}


def add(project, text, owner=None, status="not_started", deadline=None):
    return str(insert_task({"task_text": text, "normalized_task_text": text.lower(), "owner": owner, "status": status, "deadline": deadline}, project))


def test_the_sows_tasks_become_a_plan_file_that_is_read_back_without_adding_anything(world):
    w = world
    set_hours_settings(w["p"], kickoff_date=__import__("datetime").date(2026, 9, 28))
    add(w["p"], "Build the login page", "Ana Lopez", "in_progress", "2026-10-30")
    add(w["p"], "Write the user guide", None)
    path = plan_starter.write_starter_plan(w["p"], w["root"])
    assert path == w["plan_dir"] / f"{w['p']}-plan.xlsx"
    parsed = parse_plan(path.name, path.read_bytes())
    assert [(r["task_ref"], r["label"], r["owner"], r["status"], r["finish_wk"], r["estimate"]) for r in parsed["rows"]] == [
        ("T-001", "Build the login page", "Ana Lopez", "in_progress", 5, None), ("T-002", "Write the user guide", None, "not_started", None, None)]
    assert parsed["rows"][0]["notes"] == "Deadline: 2026-10-30" and parsed["layout"]["headers"]["task_ref"] == "Task ID"
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT task_text, plan_ref FROM tasks WHERE project_id = %s AND is_active ORDER BY plan_ref", (w["p"],))
        assert [(r["task_text"], r["plan_ref"]) for r in cur.fetchall()] == [("Build the login page", "T-001"), ("Write the user guide", "T-002")]
    conn.close()
    assert pfd.process_plan_file(w["p"], path, root=w["root"])["status"] == "unchanged"  # already read back; the watcher will not read it twice


def test_it_never_writes_over_or_beside_an_existing_plan_and_needs_tasks(world):
    w = world
    with pytest.raises(ValueError, match="no tasks"):
        plan_starter.write_starter_plan(w["p"], w["root"])
    add(w["p"], "Build search")
    w["plan_dir"].mkdir(parents=True)
    (w["plan_dir"] / "their-plan.xlsx").write_bytes(b"theirs")
    with pytest.raises(plan_starter.PlanExists, match="their-plan.xlsx"):
        plan_starter.write_starter_plan(w["p"], w["root"])
    assert (w["plan_dir"] / "their-plan.xlsx").read_bytes() == b"theirs" and len(list(w["plan_dir"].glob("*.xlsx"))) == 1


def test_the_pm_edits_the_starter_and_an_approved_meeting_change_is_written_into_it(world):
    w = world
    add(w["p"], "Build search", "Bo Li")
    path = plan_starter.write_starter_plan(w["p"], w["root"])
    wb = load_workbook(path)
    wb["Plan"]["G5"] = 20  # the PM adds an estimate and a start / finish week in Excel
    wb["Plan"]["E5"], wb["Plan"]["F5"] = 1, 3
    wb.save(path)
    pfd.scan_all(w["root"], [w["p"]], settle_seconds=0, recheck_seconds=0)
    from apps.db.plan_db import plan_summary
    assert plan_summary(w["p"])["baseline_hours"] == 20  # the baseline is set when the first estimates arrive
    s = create_suggestion(w["p"], "new_task", "Prepare the runbook", {"owner": "Bo Li", "status": "not_started"}, source_name="sync.txt")
    out = plan_merge.approve(w["p"], [s["suggestion_id"]], w["root"])
    assert out["merged"] == 1 and list_suggestions(w["p"])[0]["state"] == "confirmed"
    ws = load_workbook(path)["Plan"]
    assert [c.value for c in ws[6]][:4] == ["T-002", None, "Prepare the runbook", "Bo Li"] and ws["G8"].value == "=SUM(G5:G6)" and ws["C8"].value == "Total estimated hours"


def test_the_api_creates_a_starter_plan_once(world, monkeypatch):
    w = world
    assert client.post(f"/projects/{w['p']}/starter-plan").status_code == 400  # no tasks
    add(w["p"], "Build search")
    got = client.post(f"/projects/{w['p']}/starter-plan")
    assert got.status_code == 200 and got.json() == {"file": f"{w['p']}-plan.xlsx", "folder": f"projects/{w['p']}/plan"}
    assert client.post(f"/projects/{w['p']}/starter-plan").status_code == 409
