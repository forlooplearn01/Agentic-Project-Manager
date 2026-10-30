"""Loads the synthetic forloop hours data (datasets/hours/forloop) through the same API calls a person makes in the Hours page:
upload the plan, upload the two timesheets, confirm each preview.

Two modes (run inside the api container):

  python -m scripts.seed_forloop_hours_demo [project_id]
      A DISPOSABLE copy (default `forloop-hours-demo`): the project is deleted and rebuilt from the dataset every run. Shows the
      importer's own accuracy, since nothing corrects its matches.

  python -m scripts.seed_forloop_hours_demo forloop --existing
      The live golden project: its tasks and everything else are KEPT; only hours data is (re)loaded on top, so it can be re-run
      before every client demo and always ends in the same state. It also (1) sets each task's status to the state at the end of
      Week 4 (the stored ones are all "not_started"; the old values are saved to logs/ first), (2) fills people's blank roles from the
      SOW and sets capacity and skills, and (3) acts as the REVIEWER in the import step: wherever the importer's proposed task for a
      label differs from the answer key, it applies the key's task, exactly as a person correcting the "check" rows would.
      It never deletes a task (except one added by hand in the Plan view during a demo) and never touches a deadline.

Uses the real LLM for task matching, so the printed preview counts are what a user sees in the review step."""
import json
import sys
from datetime import datetime
from pathlib import Path

import requests

from apps.db.db import delete_project, get_connection, insert_task, upsert_project_person
from apps.utils import timesheet_watcher

API = "http://localhost:8000"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "datasets" / "hours" / "forloop"
args = [a for a in sys.argv[1:] if not a.startswith("--")]
EXISTING = "--existing" in sys.argv
PROJECT = args[0] if args else "forloop-hours-demo"
if EXISTING and not args:
    raise SystemExit("--existing needs the project id, e.g. `forloop --existing`.")
SKILLS = {  # skill tags per role, an invented but plausible starting point
    "Engineering Lead": ["architecture", "backend", "engineering"], "Backend Engineer": ["backend", "python", "etl"],
    "Frontend Engineer": ["frontend", "react"], "QA Engineer": ["qa", "testing"],
    "Infrastructure Engineer": ["infrastructure", "networking", "vpn"], "Project Manager": ["project management"],
    "Delivery Manager": ["delivery"], "Technical Director": ["architecture", "engineering"],
}


def call(method, path, **kw):
    r = requests.request(method, f"{API}{path}", timeout=180, **kw)
    if not r.ok:
        raise SystemExit(f"{method} {path} -> {r.status_code} {r.text[:300]}")
    return r.json()


def upload(name, kind, key_for_task=None, label_truth=None):
    """Preview a file, then confirm it. With label_truth (label -> task key) and key_for_task (task key -> task id) the reviewer
    step is simulated: every row whose proposed task differs from the key is corrected before confirming."""
    pv = call("POST", f"/projects/{PROJECT}/hours/import/preview", data={"kind": kind}, files={"file": (name, (DATA / name).read_bytes())})
    by = pv["summary"].get("by_match") or {"update": pv["summary"].get("to_update"), "create": pv["summary"].get("to_create")}
    print(f"preview {name}: layout={pv['layout']} {by}")
    decisions = []
    if label_truth and kind == "timesheet":
        for r in pv["rows"]:
            key = label_truth.get(r["label"])
            if key is None:
                continue
            want = None if key == "other" else key_for_task[key]
            if r["task_id"] != want:
                decisions.append({"idx": r["idx"], "task_id": want})
        print(f"  reviewer corrected {len(decisions)} of {len(pv['rows'])} rows")
    done = call("POST", f"/hours/imports/{pv['import_id']}/confirm", json={"decisions": decisions})
    print(f"  confirmed: {done}")
    return pv


exp = json.loads((DATA / "forloop-hours-expected.json").read_text())

if EXISTING:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT task_id, task_text, status FROM tasks WHERE project_id = %s AND is_active", (PROJECT,))
    live = {text: (str(tid), status) for tid, text, status in cur.fetchall()}
    if not live:
        raise SystemExit(f"Project '{PROJECT}' has no tasks; nothing to load hours onto.")
    backup_dir = ROOT / "logs"
    backup_dir.mkdir(exist_ok=True)
    earlier = sorted(backup_dir.glob(f"{PROJECT}-before-hours-*.json"))
    if earlier:  # the first run's file holds the ORIGINAL statuses; later runs would only record the ones this script set
        print(f"original statuses were saved earlier in {earlier[0]}")
    else:
        backup = backup_dir / f"{PROJECT}-before-hours-{datetime.now():%Y%m%d-%H%M%S}.json"
        backup.write_text(json.dumps({"statuses": {t: s for t, (_, s) in live.items()}}, indent=1))
        print(f"saved the original statuses to {backup}")
    # Hours data only: clear it so a re-run ends in the same state (tasks, deadlines, owners are not touched).
    # The plan: tasks added by hand in the Plan view during a demo go (found through the change history), tasks removed there come back,
    # and the baseline, history, phases and estimates are cleared so the plan import below sets them fresh.
    cur.execute("SELECT DISTINCT task_id FROM plan_changes WHERE project_id = %s AND field = 'task_added' AND source = 'edit' AND task_id IS NOT NULL", (PROJECT,))
    for (hand_added,) in cur.fetchall():
        cur.execute("DELETE FROM tasks WHERE task_id = %s", (hand_added,))
    cur.execute("UPDATE tasks SET is_active = TRUE, removed_at = NULL WHERE project_id = %s AND removed_at IS NOT NULL", (PROJECT,))
    for table in ("time_entries", "hours_imports", "project_hours_settings", "plan_changes"):
        cur.execute(f"DELETE FROM {table} WHERE project_id = %s", (PROJECT,))
    cur.execute("UPDATE tasks SET estimate_hours = NULL, plan_start_week = NULL, plan_finish_week = NULL, hours_still_needed = NULL, phase = NULL, "
                "baseline_estimate_hours = NULL, baseline_start_week = NULL, baseline_finish_week = NULL WHERE project_id = %s", (PROJECT,))
    for p in exp["people"]:  # roles from the SOW, only where blank
        cur.execute("UPDATE project_people SET role = %s WHERE project_id = %s AND name = %s AND role IS NULL", (p["role"], PROJECT, p["name"]))
    conn.commit()
    cur.close()
    conn.close()
    # Files the folder pickup already read (processed/, needs-review/) or has not read yet: moved aside so the next demo drop starts clean.
    moved = timesheet_watcher.archive_project_folder(PROJECT)
    if moved:
        print(f"moved {moved} timesheet file(s) out of timesheets/{PROJECT}/ into timesheets/_archive/")
else:
    delete_project(PROJECT)  # a clean slate: tasks, hours, imports and settings of the demo project
    timesheet_watcher.archive_project_folder(PROJECT)
    for p in exp["people"]:
        upsert_project_person(PROJECT, p["name"], f"{p['name'].split()[0].lower()}@forloop.example", p["role"])
    for t in exp["tasks"]:
        if t["db_task_text"]:
            insert_task({"task_text": t["db_task_text"], "normalized_task_text": t["db_task_text"].lower(), "owner": t["owner"],
                         "status": t["status_assumed"]}, PROJECT)

for p in exp["people"]:
    call("PUT", f"/projects/{PROJECT}/people/{p['name']}/capacity", json={"weekly_capacity_hours": p["weekly_capacity_hours"], "skills": SKILLS.get(p["role"], [])})

upload("forloop-plan.xlsx", "plan")
# The reviewer step needs each task key's id and each label's true task (flat CSV and grid labels).
tasks_now = call("GET", f"/projects/{PROJECT}/tasks")["tasks"]
text_to_id = {t["task_text"]: t["task_id"] for t in tasks_now}
key_for_task = {t["key"]: text_to_id[t["db_task_text"] or t["plan_label"]] for t in exp["tasks"] if (t["db_task_text"] or t["plan_label"]) in text_to_id}
label_truth = {label: keys[0] for label, keys in exp["label_to_task_key"].items()}
if EXISTING:
    # Task statuses at the end of Week 4, through the same API the Tasks table uses (after the plan import, which may have created
    # the project-management task).
    changed = 0
    for t in exp["tasks"]:
        text = t["db_task_text"] or t["plan_label"]
        current = next((x for x in tasks_now if x["task_text"] == text), None)
        if current and current["status"] != t["status_assumed"]:
            call("PATCH", f"/tasks/{current['task_id']}", json={"field": "status", "value": t["status_assumed"]})
            changed += 1
    print(f"set {changed} task statuses to the end-of-Week-4 state")
reviewer = (key_for_task, label_truth) if EXISTING else (None, None)
upload("forloop-timesheet-flat.csv", "timesheet", *reviewer)
upload("forloop-timesheet-grid.xlsx", "timesheet", *reviewer)

r = call("GET", f"/projects/{PROJECT}/hours")
want = exp["metrics"]
print(f"\nanchored to {r['as_of']} (the end of the latest week with hours):")
print(f"  logged {r['logged_hours']} (expected {want['actual_hours_total']}), remaining {r['remaining_hours_on_open_tasks']} "
      f"(expected {want['remaining_hours_on_open_estimated_tasks']}), forecast {r['forecast_at_completion']} (expected {want['forecast_at_completion']})")
print(f"  burn check {r['burn_check']}")
print(f"  open without estimate: {r['open_tasks_without_estimate']}")
print(f"  open over estimate: {[o['task'][:40] for o in r['open_tasks_over_their_estimate']]}")
print(f"\nproject: {PROJECT}")
