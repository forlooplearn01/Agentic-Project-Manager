"""Automatic timesheet pickup (apps/utils/timesheet_watcher.py): a file dropped in timesheets/<project>/ is applied by itself only when it
passes the rules (known people, file name starts with the project id, not clearly about different tasks, every row readable); otherwise
it is held in needs-review/ with the reason. The project is decided by the folder, so a file can never touch another project."""
import json
import os
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api import app
from apps.db.db import insert_task, upsert_project_person
from apps.db.hours_db import (
    fetch_time_entries, get_import, list_folder_imports, reassign_import_group, set_hours_settings, undo_import,
)
from apps.utils import timesheet_service, timesheet_watcher as tw

client = TestClient(app)
DATA = Path(__file__).resolve().parents[2] / "datasets" / "hours" / "forloop"
WEEK5 = (DATA / "forloop-timesheet-week5.csv").read_bytes()
EXPECTED = json.loads((DATA / "forloop-hours-expected.json").read_text())


def _seed(project_id):
    """The forloop people and tasks under another project id."""
    for p in EXPECTED["people"]:
        upsert_project_person(project_id, p["name"], f"{p['name'].split()[0].lower()}@x.com", p["role"])
    ids = {}
    for t in EXPECTED["tasks"]:
        if t["db_task_text"]:
            ids[t["key"]] = str(insert_task({"task_text": t["db_task_text"], "normalized_task_text": t["db_task_text"].lower(),
                                             "owner": t["owner"], "status": "not_started"}, project_id))
    return ids


@pytest.fixture
def world(test_project, tmp_path, monkeypatch):
    monkeypatch.setattr("apps.utils.hours_import._llm_pick", lambda *a, **k: {})  # no model in tests
    monkeypatch.setattr(tw, "_stuck", set())
    monkeypatch.setattr(tw, "ROOT", tmp_path)  # the API's finish_review looks here too
    ids = _seed(test_project)
    return {"p": test_project, "root": tmp_path, "ids": ids, "known": [test_project]}


def drop(w, name, content, project=None):
    pid = project or w["p"]
    folder = w["root"] / pid / "timesheets"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(content if isinstance(content, bytes) else content.encode())
    return folder / name


def scan(w, settle=0):
    return tw.scan_once(w["root"], w["known"], settle_seconds=settle, recheck_seconds=0)


def total(project):
    return sum(e["hours"] for e in fetch_time_entries(project))


def good_name(w, suffix="week5.csv"):
    return f"{w['p']}-{suffix}"


# ---------------------------------------------------------------- the happy path

def test_a_clean_file_is_applied_once_and_moved_to_processed(world):
    w = world
    drop(w, good_name(w), WEEK5)
    [r] = scan(w)
    assert (r["status"], r["hours"]) == ("applied", 100)
    assert total(w["p"]) == 100
    inbox = w["root"] / w["p"] / "timesheets"
    assert not (inbox / good_name(w)).exists() and (inbox / "processed" / good_name(w)).exists()
    assert scan(w) == []  # nothing left to do
    rec = list_folder_imports(w["p"])[0]
    assert (rec["status"], rec["filename"]) == ("applied", good_name(w)) and rec["summary"]["hours_total"] == 100


def test_a_file_still_being_written_is_left_alone(world):
    w = world
    drop(w, good_name(w), WEEK5)
    assert scan(w, settle=3600) == []  # touched a moment ago: wait
    assert (w["root"] / w["p"] / "timesheets" / good_name(w)).exists() and total(w["p"]) == 0
    assert scan(w)[0]["status"] == "applied"


def test_the_same_file_is_never_applied_twice_but_can_be_after_an_undo(world):
    w = world
    drop(w, good_name(w), WEEK5)
    first = scan(w)[0]
    drop(w, good_name(w, "week5-copy.csv"), WEEK5)
    assert scan(w)[0]["status"] == "duplicate" and total(w["p"]) == 100
    undo_import(first["import_id"])
    assert total(w["p"]) == 0
    drop(w, good_name(w, "week5-again.csv"), WEEK5)  # an undone import is not a duplicate: the person wants it back
    assert scan(w)[0]["status"] == "applied" and total(w["p"]) == 100


# ---------------------------------------------------------------- rule 1: people

def test_a_file_with_an_unknown_person_is_held_and_nothing_is_written(world):
    w = world
    csv = WEEK5.decode() + f"30-Oct-2026,Zed Unknown,Forloop,Marketing stuff,5,submitted\n"
    drop(w, good_name(w), csv)
    [r] = scan(w)
    assert r["status"] == "held" and "Zed Unknown" in r["reason"] and "people list" in r["reason"]
    assert total(w["p"]) == 0
    assert (w["root"] / w["p"] / "timesheets" / "needs-review" / good_name(w)).exists()


def test_a_held_file_can_be_reviewed_and_approved(world):
    w = world
    csv = WEEK5.decode() + "30-Oct-2026,Zed Unknown,Forloop,Marketing stuff,5,submitted\n"
    drop(w, good_name(w), csv)
    held = scan(w)[0]
    review = client.get(f"/hours/imports/{held['import_id']}/review").json()
    assert review["status"] == "held" and review["summary"]["unknown_people"] == ["Zed Unknown"] and "Ram Anandkumar" in review["people"]
    zed = next(r for r in review["rows"] if r["person_raw"] == "Zed Unknown")
    done = client.post(f"/hours/imports/{held['import_id']}/confirm", json={"decisions": [{"idx": zed["idx"], "person": "Ram Anandkumar"}]})
    assert done.status_code == 200 and total(w["p"]) == 105
    assert (w["root"] / w["p"] / "timesheets" / "processed" / good_name(w)).exists() and not (w["root"] / w["p"] / "timesheets" / "needs-review" / good_name(w)).exists()
    assert get_import(held["import_id"])["status"] == "applied"


def test_a_file_that_could_not_be_read_has_nothing_to_review(world):
    w = world
    drop(w, good_name(w, "bad.xlsx"), b"this is not a spreadsheet")
    [r] = scan(w)
    assert r["status"] == "held" and "could not be opened" in r["reason"]
    assert client.get(f"/hours/imports/{r['import_id']}/review").status_code == 404
    items = client.get(f"/projects/{w['p']}/timesheet-inbox").json()["items"]
    assert items[0]["status"] == "held" and items[0]["can_review"] is False


# ---------------------------------------------------------------- rule 2: the file name

def test_the_file_name_must_start_with_the_project_id(world):
    w = world
    drop(w, "week5.csv", WEEK5)
    [r] = scan(w)
    assert r["status"] == "held" and "must start with the project id" in r["reason"] and total(w["p"]) == 0


def test_a_name_starting_with_another_projects_longer_id_is_held(world):
    w = world
    other = f"{w['p']}-demo"
    insert_task({"task_text": "x", "normalized_task_text": "x", "owner": None, "status": "not_started"}, other)
    w["known"] = [w["p"], other]
    try:
        drop(w, f"{other}-week5.csv", WEEK5)  # starts with this project's id too, but it is the other project's file
        [r] = scan(w)
        assert r["status"] == "held" and "another project's id" in r["reason"] and total(w["p"]) == 0
    finally:
        from apps.db.db import delete_project
        delete_project(other)


# ---------------------------------------------------------------- rule 3: not clearly about different tasks

def test_a_file_about_entirely_different_tasks_is_held(world):
    w = world
    rows = "".join(f"30-Oct-2026,Ram Anandkumar,P,{label},3,x\n" for label in ("Quantum widget polish", "Zebra paint schedule", "Rocket fuel audit", "Banana count"))
    drop(w, good_name(w), "Week Ending,Employee,Project,Activity,Hours,Comments\n" + rows)
    [r] = scan(w)
    assert r["status"] == "held" and "None of the task names" in r["reason"]


def test_one_matching_task_line_is_enough_and_untasked_layouts_are_not_judged(world):
    w = world
    mixed = ("Week Ending,Employee,Project,Activity,Hours,Comments\n30-Oct-2026,Ram Anandkumar,P,Perform load testing on the backend API,3,x\n"
             + "".join(f"30-Oct-2026,Ram Anandkumar,P,{label},1,x\n" for label in ("Zebra paint", "Rocket fuel", "Banana count")))
    drop(w, good_name(w), mixed)
    assert scan(w)[0]["status"] == "applied"
    w2_summary = (DATA / "forloop-timesheet-summary.xlsx").read_bytes()  # person by week: no task names at all
    drop(w, good_name(w, "summary.xlsx"), w2_summary)
    assert scan(w)[0]["status"] == "applied"


# ---------------------------------------------------------------- rule 4: every row readable

def test_a_file_with_an_unreadable_row_is_held_not_partly_applied(world):
    w = world
    drop(w, good_name(w), WEEK5.decode() + "30-Oct-2026,Ram Anandkumar,P,VPN access resolution review,eight,x\n")
    [r] = scan(w)
    assert r["status"] == "held" and "could not be read" in r["reason"] and total(w["p"]) == 0


# ---------------------------------------------------------------- one bad file never stops the others

def test_bad_files_do_not_stop_the_good_one(world, monkeypatch):
    w = world
    drop(w, good_name(w, "a-corrupt.xlsx"), b"nope")
    drop(w, good_name(w, "b-good.csv"), WEEK5)
    drop(w, good_name(w, "c-notes.pdf"), b"%PDF-1.4")
    drop(w, good_name(w, "d-huge.csv"), b"x" * (tw.hours_import.MAX_BYTES + 10))
    results = {r["file"].split("-", 2)[-1]: r["status"] for r in scan(w)}
    assert list(results.values()).count("applied") == 1 and results["b-good.csv"] == "applied"
    assert results["a-corrupt.xlsx"] == "held" and results["c-notes.pdf"] == "held" and results["d-huge.csv"] == "held"
    reasons = {r["filename"]: r["reason"] for r in list_folder_imports(w["p"])}
    assert "5 MB" in reasons[good_name(w, "d-huge.csv")] and "Excel" in reasons[good_name(w, "c-notes.pdf")]


def test_an_unexpected_error_is_recorded_and_the_loop_carries_on(world, monkeypatch):
    w = world
    real = timesheet_service.preview_timesheet

    def flaky(project_id, filename, *a, **k):
        if "boom" in filename:
            raise RuntimeError("kaboom")
        return real(project_id, filename, *a, **k)

    monkeypatch.setattr(timesheet_service, "preview_timesheet", flaky)
    drop(w, good_name(w, "a-boom.csv"), WEEK5)
    drop(w, good_name(w, "b-fine.csv"), WEEK5.replace(b"Sales Trends view", b"Sales Trends view "))
    statuses = {r["file"]: r["status"] for r in scan(w)}
    assert statuses[good_name(w, "a-boom.csv")] == "failed" and statuses[good_name(w, "b-fine.csv")] == "applied"
    assert (w["root"] / w["p"] / "timesheets" / "needs-review" / good_name(w, "a-boom.csv")).exists()
    assert {r["status"] for r in list_folder_imports(w["p"])} == {"failed", "applied"}


# ---------------------------------------------------------------- what it must ignore

def test_junk_and_misplaced_files_are_ignored(world):
    w = world
    inbox = w["root"] / w["p"] / "timesheets"
    drop(w, ".hidden.csv", WEEK5)
    drop(w, "~$lock.xlsx", WEEK5)
    drop(w, good_name(w, "half.csv.tmp"), WEEK5)
    (inbox / "sub").mkdir()
    (inbox / "sub" / good_name(w)).write_bytes(WEEK5)  # a subfolder is not an inbox
    outside = w["root"] / "elsewhere.csv"
    outside.write_bytes(WEEK5)
    (inbox / good_name(w, "link.csv")).symlink_to(outside)  # a shortcut to a file elsewhere is never followed
    (w["root"] / "stray.csv").write_bytes(WEEK5)  # loose in the main folder: belongs to no project
    drop(w, good_name(w), WEEK5, project="not-a-project")  # a folder that is not a project
    assert scan(w) == []
    assert total(w["p"]) == 0 and outside.exists() and (w["root"] / "stray.csv").exists()


def test_unsafe_project_ids_never_get_folders(tmp_path):
    tw.ensure_folders(["good-id", "../escape", ".hidden", "a/b", ""], tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["good-id"]
    for sub in ("plan", "plan/versions", "meetings", "meetings/processed", "meetings/needs-review", "timesheets", "timesheets/processed", "timesheets/needs-review"):
        assert (tmp_path / "good-id" / sub).is_dir()


# ---------------------------------------------------------------- a file can only touch the project whose folder it is in

def test_a_file_only_ever_changes_the_project_of_its_folder(world):
    w = world
    other = f"{w['p']}-b"
    _seed(other)  # the same people and tasks, a different project
    w["known"] = [w["p"], other]
    try:
        drop(w, good_name(w), WEEK5)
        assert scan(w)[0]["status"] == "applied"
        assert total(w["p"]) == 100 and total(other) == 0
        drop(w, good_name(w, "from-a.csv"), WEEK5.replace(b"Sales Trends view", b"Sales Trends views"), project=other)  # A's name in B's folder
        [r] = scan(w)
        assert r["status"] == "held" and total(other) == 0 and total(w["p"]) == 100
    finally:
        from apps.db.db import delete_project
        delete_project(other)


def test_the_projects_week_start_decides_the_weeks(world):
    w = world
    set_hours_settings(w["p"], week_start_day=6)
    drop(w, good_name(w), WEEK5)
    assert scan(w)[0]["status"] == "applied"
    assert {e["week_start"] for e in fetch_time_entries(w["p"])} == {date(2026, 10, 25)}  # Fri 30 Oct belongs to the week starting Sun 25 Oct


# ---------------------------------------------------------------- check these matches, move, undo

def test_guessed_lines_are_listed_and_can_be_moved_without_changing_totals(world):
    w = world
    drop(w, good_name(w), WEEK5)
    r = scan(w)[0]
    inbox = client.get(f"/projects/{w['p']}/timesheet-inbox").json()
    assert inbox["enabled"] in (True, False) and inbox["folder"] == f"projects/{w['p']}/timesheets" and inbox["last_applied_at"]
    to_check = inbox["to_check"]
    assert to_check and all(c["match"] in ("suggested", "llm", "none") for c in to_check)
    assert all(c["label"] not in ("Meetings", "Standup / meetings") for c in to_check)  # plain meetings are not "guesses"
    item = next(c for c in to_check if c["label"] == "Sales Trends view")
    target = w["ids"]["custseg"]
    before = total(w["p"])
    moved = client.post(f"/hours/imports/{r['import_id']}/reassign", json={"label": item["label"], "person": item["person"], "task_id": target})
    assert moved.status_code == 200 and moved.json()["moved_hours"] == 16
    assert total(w["p"]) == before
    by_task = {}
    for e in fetch_time_entries(w["p"]):
        by_task[e["task_id"]] = by_task.get(e["task_id"], 0) + e["hours"]
    assert by_task[target] >= 16 and by_task.get(w["ids"]["sales"], 0) == 0
    again = client.get(f"/projects/{w['p']}/timesheet-inbox").json()["to_check"]
    assert next(c for c in again if c["label"] == "Sales Trends view")["match"] == "moved"
    undo_import(r["import_id"])  # still fully undoable after a correction
    assert total(w["p"]) == 0


def test_moving_hours_checks_its_inputs(world):
    w = world
    drop(w, good_name(w), WEEK5)
    r = scan(w)[0]
    foreign = str(insert_task({"task_text": "other project task", "normalized_task_text": "x", "owner": None, "status": "not_started"}, w["p"] + "-zz"))
    try:
        assert client.post(f"/hours/imports/{r['import_id']}/reassign", json={"label": "Sales Trends view", "person": "Priya Patel", "task_id": foreign}).status_code == 404
        assert client.post(f"/hours/imports/{r['import_id']}/reassign", json={"label": "nope", "person": "Priya Patel", "task_id": None}).status_code == 404
    finally:
        from apps.db.db import delete_project
        delete_project(w["p"] + "-zz")
    undo_import(r["import_id"])
    with pytest.raises(ValueError):
        reassign_import_group(r["import_id"], "Sales Trends view", "Priya Patel", None)  # only an applied import can be corrected


# ---------------------------------------------------------------- housekeeping

def test_the_demo_reset_empties_the_inbox_into_the_archive(world):
    w = world
    drop(w, good_name(w), WEEK5)
    scan(w)
    drop(w, "stray-note.csv", WEEK5)
    moved = tw.archive_project_folder(w["p"], w["root"])
    assert moved == 2
    assert not list((w["root"] / w["p"] / "timesheets").rglob("*.csv")) and len(list((w["root"] / "_archive").rglob("*.csv"))) == 2


def test_the_inbox_endpoint_for_an_empty_project(test_project):
    body = client.get(f"/projects/{test_project}/timesheet-inbox").json()
    assert body["items"] == [] and body["to_check"] == [] and body["last_applied_at"] is None
