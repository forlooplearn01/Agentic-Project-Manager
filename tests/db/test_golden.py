"""Export a project's golden state, damage it, restore it: it comes back identical (apps/utils/golden.py)."""
import json
from datetime import date

import pytest
from openpyxl import Workbook
from psycopg2.extras import Json

from apps.db.db import create_deliverable, get_connection, insert_task, upsert_project_person, upsert_source_document
from apps.db.hours_db import save_timesheet_week, set_hours_settings, set_person_capacity, set_task_hours
from apps.db.plan_sync import record_folder_file
from apps.db.suggestions_db import create_suggestion
from apps.utils import golden, timesheet_watcher as tw


@pytest.fixture
def world(test_project, tmp_path, monkeypatch):
    monkeypatch.setattr(tw, "ROOT", tmp_path)
    monkeypatch.setattr(golden, "GOLDEN_DIR", tmp_path / "golden")
    return {"p": test_project, "root": tmp_path, "dir": tmp_path / "golden" / test_project}


def build(p, root):
    upsert_project_person(p, "Ana Lopez", "ana@example.com", "Engineer")
    set_person_capacity(p, "Ana Lopez", weekly_capacity_hours=30, skills=["python", "qa"])
    set_hours_settings(p, budget_hours=100, kickoff_date=date(2026, 10, 12), role_budgets={"Engineer": 60})
    upsert_source_document("The SOW says the go-live is on 20 November and the budget is 100 hours for the build.", "sow", p)
    a = str(insert_task({"task_text": "Build search", "normalized_task_text": "build search", "owner": "Ana Lopez", "status": "in_progress"}, p))
    b = str(insert_task({"task_text": "Write the guide", "normalized_task_text": "write the guide", "owner": "Ana Lopez", "status": "not_started"}, p))
    set_task_hours(a, estimate_hours=20, plan_start_week=1, plan_finish_week=3)
    d = create_deliverable(p, "Search module", number="4.1")
    conn = get_connection()
    with conn.cursor() as cur:
        did = d["deliverable_id"] if isinstance(d, dict) else str(d)
        cur.execute("UPDATE tasks SET deliverable_id = %s WHERE task_id = %s", (did, a))
        cur.execute("INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES (%s, %s)", (b, a))
        cur.execute("INSERT INTO meetings (project_id, title, attendee_emails, start_iso, end_iso, status) VALUES (%s, 'Review', %s, '2026-11-05T10:00:00', '2026-11-05T10:30:00', 'pending_approval')", (p, Json(["ana@example.com"])))
        cur.execute("INSERT INTO raid_items (project_id, item_type, title) VALUES (%s, 'risk', 'Credentials may arrive late')", (p,))
    conn.commit()
    conn.close()
    save_timesheet_week(p, "Ana Lopez", date(2026, 10, 19), [{"task_id": a, "hours": 12}])
    create_suggestion(p, "new_task", "Prepare the runbook", {"owner": "Ana Lopez"}, source_name="sync.txt")
    (root / p / "plan").mkdir(parents=True)
    wb = Workbook()
    wb.active.append(["Task", "Est hours"])
    wb.active.append(["Build search", 20])
    wb.save(root / p / "plan" / "plan.xlsx")
    record_folder_file(p, "plan", "plan.xlsx", "abc", "applied", summary={"tasks": 1})


def snapshot_rows(p):
    conn = get_connection()
    out = {}
    with conn.cursor() as cur:
        for t in golden.PROJECT_TABLES:
            cols = [c for c in golden._columns(cur, t) if c != golden.VECTOR_COLUMNS.get(t)]
            cur.execute(f"SELECT {', '.join(cols)} FROM {t} WHERE project_id = %s ORDER BY 1", (p,))
            out[t] = json.loads(json.dumps(cur.fetchall(), default=str))
    conn.close()
    return out


def test_a_damaged_project_is_restored_exactly(world):
    w = world
    build(w["p"], w["root"])
    path = golden.export_project(w["p"], w["root"])
    before = snapshot_rows(w["p"])
    assert path.exists() and (w["dir"] / "files" / "plan" / "plan.xlsx").exists()
    assert before["tasks"] and before["document_chunks"] and before["time_entries"] and before["meetings"] and before["raid_items"] and before["plan_suggestions"]
    # damage: change, delete and add things in the database and the folders
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE tasks SET status = 'completed', owner = 'Someone Else' WHERE project_id = %s", (w["p"],))
        cur.execute("DELETE FROM time_entries WHERE project_id = %s", (w["p"],))
        cur.execute("DELETE FROM raid_items WHERE project_id = %s", (w["p"],))
        cur.execute("UPDATE meetings SET status = 'scheduled' WHERE project_id = %s", (w["p"],))
        cur.execute("DELETE FROM project_people WHERE project_id = %s", (w["p"],))
    conn.commit()
    conn.close()
    insert_task({"task_text": "Junk from a rehearsal", "normalized_task_text": "junk", "owner": None, "status": "not_started"}, w["p"])
    (w["root"] / w["p"] / "plan" / "plan.xlsx").write_bytes(b"edited by a rehearsal")
    (w["root"] / w["p"] / "meetings").mkdir(exist_ok=True)
    (w["root"] / w["p"] / "meetings" / "extra.txt").write_text("dropped during a rehearsal")
    assert not golden.verify_project(w["p"], w["dir"], w["root"])["ok"]
    result = golden.restore_project(w["p"], w["dir"], w["root"])
    assert result == {"ok": True, "differences": []}, result
    after = snapshot_rows(w["p"])
    # a restore moves a meeting that is still waiting for approval to the next business day (an invite must be in the future): everything else is as it was
    pending_before, pending_after = before.pop("meetings"), after.pop("meetings")
    assert after == before  # every row, every column, every id as it was
    assert [m[:6] + m[8:] for m in pending_before] == [m[:6] + m[8:] for m in pending_after] and pending_before[0][6:8] != pending_after[0][6:8]  # start and end moved
    assert not (w["root"] / w["p"] / "meetings" / "extra.txt").exists() and (w["root"] / w["p"] / "plan" / "plan.xlsx").read_bytes() != b"edited by a rehearsal"
    assert not (w["root"] / tw.PAUSED).exists()  # the watcher is running again
    # search vectors were rebuilt, so searching the restored project works
    from apps.db.db import search_document_chunks
    assert "go-live" in search_document_chunks(w["p"], "when is go-live", limit=1)[0]["chunk_text"]


def test_verify_lists_every_difference(world):
    w = world
    build(w["p"], w["root"])
    golden.export_project(w["p"], w["root"])
    assert golden.verify_project(w["p"], w["dir"], w["root"])["ok"]
    insert_task({"task_text": "Extra task", "normalized_task_text": "extra task", "owner": None, "status": "not_started"}, w["p"])
    (w["root"] / w["p"] / "timesheets" / "late.csv").parent.mkdir(exist_ok=True)
    (w["root"] / w["p"] / "timesheets" / "late.csv").write_text("x")
    diffs = golden.verify_project(w["p"], w["dir"], w["root"])["differences"]
    assert "tasks: 3 rows, expected 2" in diffs and "extra file: timesheets/late.csv" in diffs
