"""The plan and meeting folders end to end: a plan dropped in plan/ is read, a transcript in meetings/ becomes suggestions, approving one writes it
into the plan file (safely), and the next read confirms it. No model and no Jira: both are replaced."""
import os
import shutil

import pytest
from openpyxl import Workbook, load_workbook
from psycopg2.extras import RealDictCursor

from apps.db.db import get_connection
from apps.db.plan_db import list_plan_changes
from apps.db.suggestions_db import list_suggestions
from apps.utils import plan_merge, project_folders as pfd, timesheet_watcher as tw

TRANSCRIPT = "Standup 29 Oct. Cy will take over the search work and wants it finished by next week. We also need a data migration plan from Bo.\n" * 2


def make_plan(path, rows, total=True):
    wb = Workbook()
    ws = wb.active
    ws.title = "Plan"
    ws.append(["Plan for the test project"])
    ws.append([])
    ws.append(["Task ID", "Task", "Owner", "Start wk", "Finish wk", "Est. hours", "Notes"])
    for r in rows:
        ws.append(list(r))
    if total:
        ws.append([])
        ws.append([None, "Total estimated hours", None, None, None, sum(r[5] for r in rows)])
    wb.save(path)


BASE = [("T-1", "Build the login page", "Ana", 1, 2, 12, None), ("T-2", "Build search", "Bo", 2, 5, 20, None), ("T-3", "Write the user guide", "Cy", 5, 8, 8, None)]


@pytest.fixture
def world(test_project, tmp_path, monkeypatch):
    monkeypatch.setattr("apps.utils.hours_import._llm_pick", lambda *a, **k: {})
    monkeypatch.setattr(tw, "ROOT", tmp_path)
    monkeypatch.setattr(tw, "_stuck", set())
    jira = []
    monkeypatch.setattr(plan_merge, "jira_sync", lambda task_id, text, project, owner=None: jira.append(text) or {"status": "created", "jira_issue_key": f"X-{len(jira)}"})
    tw.ensure_folders([test_project], tmp_path)
    return {"p": test_project, "root": tmp_path, "plan": tmp_path / test_project / "plan" / "plan.xlsx", "meetings": tmp_path / test_project / "meetings", "jira": jira}


def scan(w):
    return pfd.scan_all(w["root"], [w["p"]], settle_seconds=0, recheck_seconds=0)


def tasks(w):
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM tasks WHERE project_id = %s AND is_active ORDER BY created_at", (w["p"],))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def fake_meeting(monkeypatch, events):
    """The model is replaced: the 'meeting' says whatever `events` says (the plan's task ids filled in by name)."""
    def ask(text, tasks, people, today=None):
        ids = {t["task_text"]: str(t["task_id"]) for t in tasks}
        return [{"owner": None, "status": None, "deadline": None, "blocker": None, "quote": "…", "confidence": "high", **e, "task_id": ids.get(e["task_text"])} for e in events]
    monkeypatch.setattr("apps.utils.meeting_suggest.ask_model", ask)


MEETING = [{"task_text": "Build search", "owner": "Cy", "status": "in progress"}, {"task_text": "Prepare the data migration plan", "owner": "Bo", "deadline": "2026-11-06"}]


def read_meeting(w, monkeypatch, name="standup.txt", text=TRANSCRIPT, events=MEETING):
    fake_meeting(monkeypatch, events)
    (w["meetings"] / name).write_text(text)
    return scan(w)


def sheet(w):
    return [list(r) for r in load_workbook(w["plan"])["Plan"].iter_rows(values_only=True)]


def set_kickoff(w):
    from datetime import date
    from apps.db.hours_db import set_hours_settings
    set_hours_settings(w["p"], kickoff_date=date(2026, 9, 28))


# ---------------------------------------------------------------- the plan folder

def test_a_plan_dropped_in_the_folder_is_read_once_and_again_only_when_it_changes(world):
    w = world
    make_plan(w["plan"], BASE)
    first = scan(w)
    assert [(r["kind"], r["status"], r["added"]) for r in first] == [("plan", "applied", 3)]
    assert [t["plan_ref"] for t in tasks(w)] == ["T-1", "T-2", "T-3"] and scan(w) == []  # unchanged: not read again
    make_plan(w["plan"], [("T-1", "Build the login page", "Ana", 1, 2, 16, None)] + BASE[1:])
    os.utime(w["plan"], None)
    second = scan(w)
    assert [(r["status"], r["changed"]) for r in second] == [("applied", 1)]
    assert any(h["field"] == "estimate_hours" and h["new_value"] == "16" and h["source"] == "plan_file" for h in list_plan_changes(w["p"]))


def test_a_plan_that_cannot_be_read_or_drops_most_of_the_plan_is_held(world):
    w = world
    make_plan(w["plan"], [(f"T-{i}", f"Task number {i}", "Ana", 1, 2, 5, None) for i in range(1, 9)])
    scan(w)
    make_plan(w["plan"], [("T-1", "Task number 1", "Ana", 1, 2, 5, None)])
    held = scan(w)
    assert held[0]["status"] == "held" and "7 of the 8" in held[0]["reason"] and len(tasks(w)) == 8
    from apps.db.plan_sync import list_folder_files
    rec = next(f for f in list_folder_files(w["p"], "plan") if f["status"] == "held")
    assert pfd.apply_held_plan(w["p"], rec["file_id"], w["root"])["status"] == "applied" and len(tasks(w)) == 1
    w["plan"].write_bytes(b"not a workbook")
    assert scan(w)[0]["status"] == "held"


# ---------------------------------------------------------------- a meeting becomes suggestions

def test_a_transcript_becomes_suggestions_and_changes_nothing(world, monkeypatch):
    w = world
    make_plan(w["plan"], BASE)
    scan(w)
    set_kickoff(w)
    before = [(t["task_text"], t["owner"], t["status"]) for t in tasks(w)]
    r = read_meeting(w, monkeypatch)
    assert r[0]["status"] == "applied" and r[0]["suggestions"] == 2 and (w["meetings"] / "processed" / "standup.txt").exists()
    assert [(t["task_text"], t["owner"], t["status"]) for t in tasks(w)] == before and w["jira"] == []  # no task, no Jira until approved
    s = {x["task_text"]: x for x in list_suggestions(w["p"])}
    assert s["Build search"]["kind"] == "change" and s["Build search"]["changes"]["owner"] == {"from": "Bo", "to": "Cy"} and s["Build search"]["state"] == "suggested"
    assert s["Prepare the data migration plan"]["kind"] == "new_task" and s["Prepare the data migration plan"]["fields"]["finish_week"] == 6


def test_the_same_transcript_twice_and_an_unreadable_one(world, monkeypatch):
    w = world
    make_plan(w["plan"], BASE)
    scan(w)
    read_meeting(w, monkeypatch)
    again = read_meeting(w, monkeypatch, name="standup-copy.txt")
    assert again[0]["status"] == "duplicate" and len(list_suggestions(w["p"])) == 2
    bad = read_meeting(w, monkeypatch, name="empty.txt", text="hi")
    assert bad[0]["status"] == "held" and "too short" in bad[0]["reason"] and (w["meetings"] / "needs-review" / "empty.txt").exists()


# ---------------------------------------------------------------- approval writes into the plan file

def approve_all(w):
    ids = [s["suggestion_id"] for s in list_suggestions(w["p"], states=("suggested",))]
    return plan_merge.approve(w["p"], ids, w["root"])


def test_approving_writes_into_the_plan_file_and_the_next_read_confirms_it(world, monkeypatch):
    w = world
    make_plan(w["plan"], BASE)
    scan(w)
    set_kickoff(w)
    read_meeting(w, monkeypatch)
    out = approve_all(w)
    assert out["approved"] == 2 and out["merged"] == 2
    rows = sheet(w)
    assert rows[4][:6] == ["T-2", "Build search", "Cy", 2, 5, 20] and rows[4][7] == "In progress"  # owner and status changed (a Status column was added); weeks and estimate untouched
    assert rows[6][:3] == ["T-4", "Prepare the data migration plan", "Bo"] and "approved by the PMO" in rows[6][6]
    assert rows[8][1] == "Total estimated hours" and (list(w["plan"].parent.glob("versions/plan-*.xlsx")))  # total moved; a backup was kept
    states = {s["task_text"]: s for s in list_suggestions(w["p"])}
    assert {k: v["state"] for k, v in states.items()} == {"Build search": "confirmed", "Prepare the data migration plan": "confirmed"}
    new = next(t for t in tasks(w) if t["task_text"] == "Prepare the data migration plan")
    assert new["plan_ref"] == "T-4" and new["owner"] == "Bo" and new["plan_finish_week"] == 6
    assert next(t for t in tasks(w) if t["task_text"] == "Build search")["owner"] == "Cy"
    assert w["jira"] == ["Prepare the data migration plan"]  # Jira only for the approved new task, after the merge
    assert states["Prepare the data migration plan"]["jira_key"] == "X-1"
    assert scan(w) == []  # our own write is not a new version to read again


def test_a_rejected_suggestion_never_reaches_the_plan(world, monkeypatch):
    w = world
    make_plan(w["plan"], BASE)
    scan(w)
    read_meeting(w, monkeypatch)
    ids = [s["suggestion_id"] for s in list_suggestions(w["p"])]
    assert plan_merge.reject(w["p"], ids) == 2
    assert plan_merge.approve(w["p"], ids, w["root"])["approved"] == 0 and len(sheet(w)) == 8 and w["jira"] == []


def test_a_plan_open_in_excel_waits_and_is_updated_when_closed(world, monkeypatch):
    w = world
    make_plan(w["plan"], BASE)
    scan(w)
    set_kickoff(w)
    read_meeting(w, monkeypatch)
    lock = w["plan"].parent / "~$plan.xlsx"
    lock.write_bytes(b"lock")
    before = w["plan"].read_bytes()
    out = approve_all(w)
    assert out["merged"] == 0 and out["waiting"] == 2 and w["plan"].read_bytes() == before
    assert {s["state"] for s in list_suggestions(w["p"])} == {"waiting"} and "open in Excel" in list_suggestions(w["p"])[0]["reason"]
    lock.unlink()
    scan(w)  # the next check merges what was waiting
    assert {s["state"] for s in list_suggestions(w["p"])} == {"confirmed"} and len(sheet(w)) == 9


def test_someone_elses_edit_since_the_last_read_is_kept(world, monkeypatch):
    w = world
    make_plan(w["plan"], BASE)
    scan(w)
    set_kickoff(w)
    read_meeting(w, monkeypatch)
    wb = load_workbook(w["plan"])
    wb["Plan"]["F4"] = 40  # the PM raised the first estimate in Excel; the app has not looked yet
    wb.save(w["plan"])
    approve_all(w)
    assert sheet(w)[3][5] == 40  # the PM's edit survived the merge
    assert float(next(t for t in tasks(w) if t["task_text"] == "Build the login page")["estimate_hours"]) == 40  # and the app read it first


def test_a_project_without_a_plan_file_gets_the_approved_changes_in_its_task_list(world, monkeypatch):
    w = world
    from apps.db.db import insert_task
    insert_task({"task_text": "Build search", "normalized_task_text": "build search", "owner": "Bo", "status": "in_progress"}, w["p"])
    set_kickoff(w)
    read_meeting(w, monkeypatch)
    out = approve_all(w)
    assert out["applied_to_app"] == 2 and {s["state"] for s in list_suggestions(w["p"])} == {"applied"}
    by = {t["task_text"]: t for t in tasks(w)}
    assert by["Build search"]["owner"] == "Cy" and by["Prepare the data migration plan"]["owner"] == "Bo" and w["jira"] == ["Prepare the data migration plan"]


def test_a_task_that_a_later_plan_version_lacks_is_marked_missing_and_can_be_reapplied(world, monkeypatch):
    w = world
    make_plan(w["plan"], BASE)
    scan(w)
    set_kickoff(w)
    read_meeting(w, monkeypatch)
    approve_all(w)
    make_plan(w["plan"], BASE)  # someone saved an older copy over the plan: the approved task is gone again
    os.utime(w["plan"], None)
    scan(w)
    s = next(x for x in list_suggestions(w["p"]) if x["task_text"] == "Prepare the data migration plan")
    assert s["state"] == "missing" and "no longer" in s["reason"]
    plan_merge.approve(w["p"], [s["suggestion_id"]], w["root"])
    assert next(x for x in list_suggestions(w["p"]) if x["task_text"] == "Prepare the data migration plan")["state"] == "confirmed"
    assert any(t["task_text"] == "Prepare the data migration plan" for t in tasks(w))


def test_a_csv_plan_cannot_be_updated_and_says_so(world, monkeypatch):
    w = world
    csv = w["plan"].parent / "plan.csv"
    csv.write_text("Task ID,Task,Owner,Est. hours\nT-1,Build search,Bo,20\nT-2,Build login,Ana,10\n")
    scan(w)
    read_meeting(w, monkeypatch)
    approve_all(w)
    states = list_suggestions(w["p"])
    assert {s["state"] for s in states} == {"blocked"} and "only .xlsx" in states[0]["reason"] and "T-1,Build search,Bo,20" in csv.read_text()


# ---------------------------------------------------------------- the API the screens use

def test_the_api_lists_folders_and_suggestions_and_approves(world, monkeypatch):
    from fastapi.testclient import TestClient
    from apps.api import app
    client = TestClient(app)
    w = world
    make_plan(w["plan"], BASE)
    scan(w)
    set_kickoff(w)
    read_meeting(w, monkeypatch)
    f = client.get(f"/projects/{w['p']}/folders").json()
    assert f["plan"]["folder"] == f"projects/{w['p']}/plan" and f["plan"]["current_file"] == "plan.xlsx" and f["plan"]["writable"] and not f["plan"]["open_in_excel"]
    assert [x["status"] for x in f["plan"]["files"]] == ["applied"] and f["meetings"]["files"][0]["summary"]["suggestions"] == 2
    got = client.get(f"/projects/{w['p']}/suggestions").json()
    assert got["counts"] == {"suggested": 2} and len(got["suggestions"]) == 2
    ids = [s["suggestion_id"] for s in got["suggestions"]]
    assert client.post(f"/projects/{w['p']}/suggestions/reject", json={"ids": ids[:1]}).json() == {"rejected": 1}
    done = client.post(f"/projects/{w['p']}/suggestions/approve", json={"ids": ids[1:]}).json()
    assert done["approved"] == 1 and done["merged"] == 1
    assert client.get(f"/projects/{w['p']}/suggestions?states=confirmed").json()["counts"]["confirmed"] == 1
    assert client.post(f"/projects/{w['p']}/plan-files/00000000-0000-0000-0000-000000000000/apply").status_code == 404


def test_a_change_to_a_task_that_was_never_in_the_plan_file_is_made_in_the_app(world, monkeypatch):
    w = world
    from apps.db.db import insert_task
    make_plan(w["plan"], BASE)
    scan(w)
    legacy = str(insert_task({"task_text": "Review the VPN resolution", "normalized_task_text": "review the vpn resolution", "owner": "Ram", "status": "in_progress"}, w["p"]))
    read_meeting(w, monkeypatch, events=[{"task_text": "Review the VPN resolution", "status": "done"}, {"task_text": "Build search", "owner": "Cy"}])
    out = approve_all(w)
    assert out["approved"] == 2 and out["merged"] == 1 and out["applied_to_app"] == 1
    states = {s["task_text"]: s for s in list_suggestions(w["p"])}
    assert states["Review the VPN resolution"]["state"] == "applied" and "only exists in the app" in states["Review the VPN resolution"]["reason"]
    assert states["Build search"]["state"] == "confirmed"
    assert next(t for t in tasks(w) if str(t["task_id"]) == legacy)["status"] == "completed" and [r[1] for r in sheet(w)].count("Review the VPN resolution") == 0
