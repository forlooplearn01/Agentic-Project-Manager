"""Builds a demo project from a spec, then freezes it as golden state (apps/utils/golden.py).

The project is made the way a real one comes to exist, so every screen has genuine data behind it: the plan is an Excel file that is READ by the
app (tasks, baseline, history and dependencies come from it), documents are stored and indexed for search, hours are saved as timesheets, and
the people, risks, deliverables and meetings are inserted directly. No model calls: the same spec gives the same project every time.

A spec is a dict (see scripts/golden_build/halvane.py for a small one): id, title, client, kickoff, weeks, week_start_day, budget_hours,
role_budgets, people, tasks, last_logged_week, other_hours, sow, history_meetings, drop, raid, deliverables, meetings, events."""
import hashlib
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from apps.db.db import get_connection, upsert_project_person, upsert_source_document
from apps.db.hours_db import save_timesheet_week, set_hours_settings, set_person_capacity
from apps.db.plan_sync import record_folder_file
from apps.utils import golden, project_folders
from apps.utils import timesheet_watcher as tw

STATUS_WORDS = {"completed": "Completed", "in_progress": "In progress", "not_started": "Not started", "blocked": "Blocked", "on_hold": "On hold"}
REAL_PEOPLE = ("Nirmal Babu", "Ram Anandkumar", "Rucheek")  # their emails come from forloop's roster (never written in code)


def real_emails() -> dict:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT name, email FROM project_people WHERE project_id = 'forloop' AND email IS NOT NULL")
        out = dict(cur.fetchall())
    conn.close()
    missing = [n for n in REAL_PEOPLE if n not in out]
    if missing:
        raise SystemExit(f"No email on file in forloop's roster for: {', '.join(missing)}")
    return out


# ---------------------------------------------------------------- the plan file

def write_plan_xlsx(path: Path, spec: dict, tasks: list, use_baseline_estimates: bool):
    """Plan sheet (Task ID, Phase, Task, Owner, Start wk, Finish wk, Est. hours, Depends on, Notes, Status) and a Budget sheet, like a real plan."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Plan"
    ws["A1"] = f"{spec['title']}"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = f"Project plan for {spec['client']}"
    ws["A3"] = f"Kickoff: {spec['kickoff']:%d %b %Y}    Duration: {spec['weeks']} weeks"
    header = ["Task ID", "Phase", "Task", "Owner", "Start wk", "Finish wk", "Est. hours", "Depends on", "Notes", "Status"]
    for c, v in enumerate(header, start=1):
        ws.cell(row=5, column=c, value=v).font = Font(bold=True)
    ref = {t["key"]: f"T-{i:03d}" for i, t in enumerate(spec["tasks"], start=1)}
    r = 6
    for t in tasks:
        est = t["est_baseline"] if use_baseline_estimates and t.get("est_baseline") else t["est"]
        depends = ", ".join(ref[k] for k in t.get("depends", []))
        values = [ref[t["key"]], t.get("phase"), t["text"], t["owner"], t["start"], t["finish"], est, depends or None, t.get("note"), STATUS_WORDS[t["status"]]]
        for c, v in enumerate(values, start=1):
            ws.cell(row=r, column=c, value=v)
        r += 1
    ws.cell(row=r + 1, column=3, value="Total estimated hours").font = Font(bold=True)
    ws.cell(row=r + 1, column=7, value=f"=SUM(G6:G{r - 1})").font = Font(bold=True)
    for i, w in enumerate((8, 30, 60, 20, 9, 9, 11, 16, 44, 13), start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    bs = wb.create_sheet("Budget")
    bs["A1"] = "Commercial budget (hours)"
    bs["A1"].font = Font(bold=True, size=13)
    bs["A3"] = "Total project budget (hours)"
    bs["B3"] = spec["budget_hours"]
    for c, v in enumerate(["Role", "Budgeted hours"], start=1):
        bs.cell(row=6, column=c, value=v).font = Font(bold=True)
    for i, (role, hours) in enumerate(spec["role_budgets"].items(), start=7):
        bs.cell(row=i, column=1, value=role)
        bs.cell(row=i, column=2, value=hours)
    bs.column_dimensions["A"].width = 34
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


# ---------------------------------------------------------------- hours

def _factor(key: str) -> float:
    """A repeatable 'actual versus estimate' wobble per task (0.9 to 1.12), the same on every build."""
    return 0.9 + (int(hashlib.sha256(key.encode()).hexdigest(), 16) % 23) / 100


def default_logged(t: dict) -> float:
    return {"completed": t["est"] * _factor(t["key"]), "in_progress": t["est"] * 0.5, "blocked": t["est"] * 0.55}.get(t["status"], 0.0)


def hours_entries(spec: dict) -> list:
    """[(person, week number, task key | None, hours)]: each task's logged hours spread over its weeks (up to the last logged week) in half hours."""
    out, last = [], spec["last_logged_week"]
    for t in spec["tasks"]:
        total = spec.get("logged", {}).get(t["key"], default_logged(t))
        if total <= 0:
            continue
        weeks = [w for w in range(t["start"], min(t["finish"], last) + 1)] or [min(last, max(1, t["start"]))]
        owner = t["owner"].split(",")[0].strip()
        total = round(total * 2) / 2
        share = round(total / len(weeks) * 2) / 2
        for i, w in enumerate(weeks):
            h = total - share * (len(weeks) - 1) if i == len(weeks) - 1 else share
            if h > 0:
                out.append((owner, w, t["key"], h))
    for person, per_week in spec.get("other_hours", {}).items():
        out += [(person, w, None, per_week) for w in range(1, last + 1)]
    return out


# ---------------------------------------------------------------- timestamps

def backdate(spec: dict):
    """Everything was just created, so every timestamp says 'today'. Move them to when they would really have happened: the baseline and the
    tasks at kickoff (scope added later at the change-request date), documents and meeting records on their meeting dates, hours at the end of
    their week."""
    pid, kick = spec["id"], datetime.combine(spec["kickoff"], datetime.min.time()).replace(hour=9)
    cr = datetime.combine(spec["cr_date"], datetime.min.time()).replace(hour=14) if spec.get("cr_date") else None
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE tasks SET created_at = %s, updated_at = %s WHERE project_id = %s", (kick, kick, pid))
        if cr:
            cur.execute("UPDATE tasks SET created_at = %s, updated_at = %s WHERE project_id = %s AND plan_ref = ANY(%s)", (cr, cr, pid, [
                f"T-{i:03d}" for i, t in enumerate(spec["tasks"], start=1) if t.get("cr")]))
        cur.execute("UPDATE project_hours_settings SET baseline_set_at = %s, updated_at = %s WHERE project_id = %s", (kick, kick, pid))
        cur.execute("UPDATE plan_changes SET changed_at = %s WHERE project_id = %s", (cr or kick, pid))
        cur.execute("UPDATE plan_changes SET changed_at = %s WHERE project_id = %s AND field IN ('baseline_set', 'plan_imported', 'budget_hours', 'kickoff_date')", (kick, pid))
        cur.execute("SELECT file_id FROM folder_files WHERE project_id = %s AND kind = 'plan' ORDER BY created_at", (pid,))
        for (fid), when in zip([r[0] for r in cur.fetchall()], [kick, cr or kick]):
            cur.execute("UPDATE folder_files SET created_at = %s, applied_at = %s WHERE file_id = %s", (when, when, fid))
        for name, _text, when in spec["history_meetings"]:
            at = datetime.combine(when, datetime.min.time()).replace(hour=12)
            cur.execute("UPDATE folder_files SET created_at = %s, applied_at = %s WHERE project_id = %s AND kind = 'meeting' AND filename = %s", (at, at, pid, name))
            cur.execute("UPDATE source_documents SET created_at = %s WHERE project_id = %s AND content = %s", (at, pid, _text))
            cur.execute("UPDATE task_events SET created_at = %s WHERE source_doc_id IN (SELECT doc_id FROM source_documents WHERE project_id = %s AND content = %s)", (at, pid, _text))
        cur.execute("UPDATE source_documents SET created_at = %s WHERE project_id = %s AND document_type = 'sow'", (kick - timedelta(days=3), pid))
        cur.execute("UPDATE task_events SET created_at = %s WHERE source_doc_id IN (SELECT doc_id FROM source_documents WHERE project_id = %s AND document_type = 'sow')", (kick, pid))
        cur.execute("UPDATE time_entries SET created_at = week_start + interval '4 days 17 hours', updated_at = week_start + interval '4 days 17 hours' WHERE project_id = %s", (pid,))
        for table in ("raid_items", "deliverables"):
            cur.execute(f"UPDATE {table} SET created_at = %s, updated_at = %s WHERE project_id = %s", (kick + timedelta(days=2), kick + timedelta(days=2), pid))
        cur.execute("UPDATE meetings SET created_at = start_iso::timestamp - interval '3 days', updated_at = start_iso::timestamp - interval '3 days' WHERE project_id = %s AND status = 'scheduled'", (pid,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- build

def build_project(spec: dict, root=None) -> dict:
    pid = spec["id"]
    root = Path(root or tw.ROOT)
    emails = real_emails()
    if not tw.pause(root):
        tw.resume(root)
        raise SystemExit("The folder watcher is still scanning; try again in a minute.")
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            golden._wipe(cur, pid)
        conn.commit()
        conn.close()
        for sub in golden.FOLDERS:
            shutil.rmtree(root / pid / sub, ignore_errors=True)
        tw.ensure_folders([pid], root)

        set_hours_settings(pid, budget_hours=spec["budget_hours"], kickoff_date=spec["kickoff"], role_budgets=spec["role_budgets"],
                           week_start_day=spec.get("week_start_day", 0))
        for p in spec["people"]:
            upsert_project_person(pid, p["name"], emails.get(p["name"]) if p["name"] in REAL_PEOPLE else None, p["role"])
            set_person_capacity(pid, p["name"], weekly_capacity_hours=p["capacity"], skills=p.get("skills", []))

        docs = {}
        docs["sow"] = upsert_source_document(spec["sow"], "sow", pid)
        for name, text, _when in spec["history_meetings"]:
            docs[name] = upsert_source_document(text, "mom", pid)

        # the plan: version 1 is the baseline; version 2 (scope added or estimates raised since) makes the history real
        plan_path = root / pid / "plan" / f"{pid}-plan.xlsx"
        base = [t for t in spec["tasks"] if not t.get("cr")]
        write_plan_xlsx(plan_path, spec, base, use_baseline_estimates=True)
        assert project_folders.process_plan_file(pid, plan_path, root=root)["status"] == "applied"
        if len(base) != len(spec["tasks"]) or any(t.get("est_baseline") for t in spec["tasks"]):
            write_plan_xlsx(plan_path, spec, spec["tasks"], use_baseline_estimates=False)
            assert project_folders.process_plan_file(pid, plan_path, root=root)["status"] == "applied"

        ref = {t["key"]: f"T-{i:03d}" for i, t in enumerate(spec["tasks"], start=1)}
        conn = get_connection()
        with conn.cursor() as cur:
            ids = {}
            for t in spec["tasks"]:
                cur.execute("SELECT task_id FROM tasks WHERE project_id = %s AND plan_ref = %s", (pid, ref[t["key"]]))
                ids[t["key"]] = str(cur.fetchone()[0])
                cur.execute("UPDATE tasks SET priority = %s, deadline = %s, blocker = %s WHERE task_id = %s",
                            (t.get("priority", "medium"), t.get("deadline"), t.get("blocker"), ids[t["key"]]))
            for e in spec.get("events", []):
                cur.execute("""INSERT INTO task_events (task_id, event_type, task_text, owner, status, deadline, blocker, match_method, match_confidence, source_doc_id)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'high', %s)""",
                            (ids[e["task"]], e.get("type", "status_change"), next(t["text"] for t in spec["tasks"] if t["key"] == e["task"]),
                             e.get("owner"), e.get("status"), e.get("deadline"), e.get("blocker"), e.get("match", "created"), docs.get(e["doc"], docs["sow"])))
        conn.commit()
        conn.close()

        grouped = {}
        for person, week, key, hours in hours_entries(spec):
            grouped.setdefault((person, week), []).append({"task_id": ids[key] if key else None, "hours": hours})
        for (person, week), entries in sorted(grouped.items()):
            save_timesheet_week(pid, person, spec["kickoff"] + timedelta(days=7 * (week - 1)), entries)

        conn = get_connection()
        with conn.cursor() as cur:
            for r in spec["raid"]:
                cur.execute("""INSERT INTO raid_items (project_id, item_type, title, description, owner, status, probability, impact, review_state, origin, evidence)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'document', %s)""",
                            (pid, r["type"], r["title"], r.get("description"), r.get("owner"), r.get("status", "open"), r.get("probability"), r.get("impact"),
                             r.get("review_state", "confirmed"), r.get("evidence")))
            for d in spec["deliverables"]:
                cur.execute("""INSERT INTO deliverables (project_id, number, title, owner, deadline, status, origin, source_task_id, review_state, evidence)
                               VALUES (%s, %s, %s, %s, %s, %s, 'document', %s, %s, %s) RETURNING deliverable_id""",
                            (pid, d["number"], d["title"], d.get("owner"), d.get("deadline"), d.get("status", "not_started"),
                             ids.get(d.get("task")), d.get("review_state", "confirmed"), d.get("evidence")))
                did = cur.fetchone()[0]
                if d.get("task"):
                    cur.execute("UPDATE tasks SET deliverable_id = %s WHERE task_id = %s", (did, ids[d["task"]]))
                for person, role in d.get("raci", {}).items():
                    cur.execute("INSERT INTO deliverable_raci (deliverable_id, person, role) VALUES (%s, %s, %s)", (did, person, role))
            for m in spec["meetings"]:
                resolved = [emails[n] for n in m["attendees"] if n in REAL_PEOPLE]
                unresolved = [n for n in m["attendees"] if n not in REAL_PEOPLE]
                cur.execute("""INSERT INTO meetings (project_id, title, description, attendee_emails, unresolved_attendees, start_iso, end_iso, status, reason)
                               VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)""",
                            (pid, m["title"], m.get("description"), __import__("json").dumps(resolved), __import__("json").dumps(unresolved),
                             m["start"], m["end"], m["status"], m.get("reason")))
        conn.commit()
        conn.close()

        # the meeting transcripts already read: kept in processed/ and recorded, like a real run of the meetings folder
        for name, text, _when in spec["history_meetings"]:
            target = root / pid / "meetings" / "processed" / name
            target.write_text(text)
            record_folder_file(pid, "meeting", name, hashlib.sha256(text.encode()).hexdigest(), "applied",
                               summary={"suggestions": 0, "state": "done", "folder_file": f"processed/{name}"})
    finally:
        tw.resume(root)

    backdate(spec)
    golden.export_project(pid, root)
    drop_dir = golden.GOLDEN_DIR / pid / "drop"
    shutil.rmtree(drop_dir, ignore_errors=True)
    drop_dir.mkdir(parents=True)
    for name, content in spec["drop"].items():
        (drop_dir / name).write_bytes(content if isinstance(content, bytes) else content.encode())
    if spec.get("drop_plan"):  # the ready-to-run file is a new version of the plan: same name, so it replaces the one in the plan folder
        changed = spec["drop_plan"]([dict(t) for t in spec["tasks"]])
        write_plan_xlsx(drop_dir / f"{pid}-plan.xlsx", {**spec, "tasks": changed}, changed, use_baseline_estimates=False)
    golden.refresh_pending_meetings(pid)
    return golden.verify_project(pid, root=root)
