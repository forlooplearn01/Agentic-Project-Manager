"""Golden state of a demo project: export it once, restore it exactly any number of times.

    export_project   a project's rows (every table), its folder files and a manifest of key numbers -> datasets/golden/<id>/
    restore_project  wipes the project (rows and folders) and rebuilds it from that export: same ids, same rows, same files. No model calls
                     except the local embedding model (which is deterministic) for search vectors, so it takes seconds and gives the same
                     result every time. The folder watcher is paused meanwhile.
    verify_project   compares the live project with the manifest and lists every difference.

Nothing outside the database and projects/<id>/ is touched: Jira issues and calendar events a demo created are NOT undone by a restore
(DECISIONS.md "Demo projects and the reset")."""
import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from psycopg2.extras import Json, RealDictCursor

from apps.db.db import get_connection
from apps.utils import timesheet_watcher as tw
from apps.utils.embeddings import get_embeddings_batch

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "datasets" / "golden"
FORMAT_VERSION = 1
VECTOR_COLUMNS = {"tasks": "task_embedding", "document_chunks": "chunk_embedding"}
# rows that carry a project_id, in the order they can be inserted (tasks and deliverables point at each other: links are put back at the end)
PROJECT_TABLES = ["project_integrations", "project_hours_settings", "project_people", "source_documents", "document_chunks", "deliverables", "tasks",
                  "hours_imports", "time_entries", "plan_changes", "plan_suggestions", "folder_files", "meetings", "raid_items", "duplicate_decisions"]
# rows that hang off a task or deliverable and have no project_id of their own: (table, column, parent table)
CHILD_TABLES = [("task_dependencies", "task_id", "tasks"), ("task_raci", "task_id", "tasks"), ("deliverable_raci", "deliverable_id", "deliverables"),
                ("task_events", "task_id", "tasks")]
FOLDERS = ("plan", "meetings", "timesheets")
# numbers that are compared after a restore (counts per table are always compared)
KEY_NUMBERS = ("budget_hours", "logged_hours", "hours_left_in_budget", "remaining_hours_on_open_tasks", "forecast_at_completion", "sum_of_task_estimates")


def _columns(cur, table: str) -> dict:
    with cur.connection.cursor() as plain:  # a plain cursor, whatever kind the caller's is
        plain.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position", (table,))
        return {c: t for c, t in plain.fetchall()}


def _jsonable(rows) -> list:
    return json.loads(json.dumps([dict(r) for r in rows], default=str))


def counts(project_id: str) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    out = {}
    for t in PROJECT_TABLES:
        cur.execute(f"SELECT COUNT(*) FROM {t} WHERE project_id = %s", (project_id,))
        out[t] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM task_dependencies WHERE task_id IN (SELECT task_id FROM tasks WHERE project_id = %s)", (project_id,))
    out["task_dependencies"] = cur.fetchone()[0]
    cur.close()
    conn.close()
    return out


def manifest(project_id: str) -> dict:
    from apps.db.hours_db import project_hours_report
    from apps.db.plan_db import plan_summary
    report = project_hours_report(project_id)
    return {"counts": counts(project_id), "numbers": {k: report.get(k) for k in KEY_NUMBERS}, "status": report["burn_check"]["status"],
            "plan": {k: v for k, v in plan_summary(project_id).items() if k != "baseline_set_at"}}


# ---------------------------------------------------------------- emails stay out of the exported files

EMAILS_FILE = "emails.local.json"  # {name: email}, next to the exports; gitignored. The exports hold <<email:Name>> in place of every address.
_TOKEN = re.compile(r"<<email:([^>]+)>>")


def _hide_emails(text: str, people: list) -> str:
    known = {}
    path = GOLDEN_DIR / EMAILS_FILE
    if path.exists():
        known.update(json.loads(path.read_text()))
    for p in people:
        if p.get("email"):
            known[p["name"]] = p["email"]
    for name, email in known.items():
        text = text.replace(email, f"<<email:{name}>>")
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(known, indent=1, sort_keys=True) + "\n")
    return text


def _placeholder_email(name: str) -> str:
    return name.lower().replace(" ", ".") + "@example.com"


def _show_emails(text: str) -> str:
    names = set(_TOKEN.findall(text))
    if not names:
        return text
    path = GOLDEN_DIR / EMAILS_FILE
    known = json.loads(path.read_text()) if path.exists() else {}
    missing = sorted(n for n in names if n not in known)
    if missing:  # fall back to any project that has them on its roster
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ON (name) name, email FROM project_people WHERE name = ANY(%s) AND email IS NOT NULL", (missing,))
            known.update(dict(cur.fetchall()))
        conn.close()
        missing = sorted(n for n in names if n not in known)
    if missing and not path.exists():  # a fresh checkout: the real addresses are deliberately not in the repository, so use ones that go nowhere
        known.update({n: _placeholder_email(n) for n in missing})
        missing = []
    if missing:
        raise RuntimeError(f"No email known for {', '.join(missing)}: add them to datasets/golden/{EMAILS_FILE} as {{\"Name\": \"address\"}}.")
    return _TOKEN.sub(lambda m: known[m.group(1)], text)


# ---------------------------------------------------------------- export

def export_project(project_id: str, root=None, out_dir: Path = None) -> Path:
    """Write datasets/golden/<id>/golden.json and files/ (the project's plan, meetings and timesheets folders)."""
    root = Path(root or tw.ROOT)
    out_dir = Path(out_dir or GOLDEN_DIR / project_id)
    conn = get_connection()
    tables, cols = {}, {}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        for t in PROJECT_TABLES:
            names = [c for c in _columns(cur, t) if c != VECTOR_COLUMNS.get(t)]
            cols[t] = names
            cur.execute(f"SELECT {', '.join(names)} FROM {t} WHERE project_id = %s ORDER BY 1", (project_id,))
            tables[t] = _jsonable(cur.fetchall())
        task_ids = [r["task_id"] for r in tables["tasks"]]
        deliverable_ids = [r["deliverable_id"] for r in tables["deliverables"]]
        for t, col, parent in CHILD_TABLES:
            ids = task_ids if parent == "tasks" else deliverable_ids
            names = list(_columns(cur, t))
            cur.execute(f"SELECT {', '.join(names)} FROM {t} WHERE {col} = ANY(%s::uuid[]) ORDER BY 1", (ids,))
            tables[t] = _jsonable(cur.fetchall())
    conn.close()
    data = {"format": FORMAT_VERSION, "project_id": project_id, "tables": tables, "manifest": manifest(project_id)}
    shutil.rmtree(out_dir / "files", ignore_errors=True)  # only what an export writes; the drop/ folder (the demo's ready-to-run file) is left alone
    (out_dir / "files").mkdir(parents=True)
    for sub in FOLDERS:
        for path in sorted((root / project_id / sub).rglob("*")):
            if path.is_file() and not path.name.startswith(".") and not path.is_symlink():
                target = out_dir / "files" / path.relative_to(root / project_id)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    (out_dir / "golden.json").write_text(_hide_emails(json.dumps(data, indent=1, sort_keys=True, default=str), tables["project_people"]) + "\n")
    return out_dir / "golden.json"


# ---------------------------------------------------------------- restore

def _wipe(cur, project_id: str):
    cur.execute("""DELETE FROM task_events WHERE task_id IN (SELECT task_id FROM tasks WHERE project_id = %s)
                   OR source_doc_id IN (SELECT doc_id FROM source_documents WHERE project_id = %s)""", (project_id, project_id))
    # rows that point at tasks go first: deleting a task would otherwise detach them one by one (and two hours entries that lose their task
    # can collide on the person / week / task uniqueness rule)
    for t in ("time_entries", "plan_changes", "plan_suggestions", "hours_imports", "folder_files"):
        cur.execute(f"DELETE FROM {t} WHERE project_id = %s", (project_id,))
    for t in ("source_documents", "deliverables", "tasks"):
        cur.execute(f"DELETE FROM {t} WHERE project_id = %s", (project_id,))
    for t in PROJECT_TABLES:
        cur.execute(f"DELETE FROM {t} WHERE project_id = %s", (project_id,))


def _insert(cur, table: str, rows: list, skip: tuple = ()):
    if not rows:
        return
    types = _columns(cur, table)
    names = [c for c in rows[0] if c not in skip and c in types]
    sql = f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))})"
    for r in rows:
        cur.execute(sql, [Json(r[c]) if types[c] == "jsonb" and r[c] is not None else r[c] for c in names])


def restore_project(project_id: str, golden_dir: Path = None, root=None) -> dict:
    """Rebuild a project exactly as exported. Pauses the folder watcher for the duration."""
    golden_dir = Path(golden_dir or GOLDEN_DIR / project_id)
    data = json.loads(_show_emails((golden_dir / "golden.json").read_text()))
    root = Path(root or tw.ROOT)
    if not tw.pause(root):
        tw.resume(root)  # never leave the watcher paused because a reset could not start
        raise RuntimeError("The folder watcher is still scanning; try again in a minute.")
    try:
        t = data["tables"]
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _wipe(cur, project_id)
                for table in PROJECT_TABLES:
                    rows = t[table]
                    if table == "tasks":
                        _insert(cur, "tasks", rows, skip=("deliverable_id",))
                    elif table == "deliverables":
                        _insert(cur, "deliverables", rows, skip=("source_task_id",))
                    else:
                        _insert(cur, table, rows)
                for r in t["tasks"]:  # the links between tasks and deliverables, now that both exist
                    if r.get("deliverable_id"):
                        cur.execute("UPDATE tasks SET deliverable_id = %s WHERE task_id = %s", (r["deliverable_id"], r["task_id"]))
                for r in t["deliverables"]:
                    if r.get("source_task_id"):
                        cur.execute("UPDATE deliverables SET source_task_id = %s WHERE deliverable_id = %s", (r["source_task_id"], r["deliverable_id"]))
                for table, _, _ in CHILD_TABLES:
                    _insert(cur, table, t[table])
                if t["tasks"]:  # search vectors are rebuilt (the local embedding model gives the same vectors every time)
                    for tid, vec in zip([r["task_id"] for r in t["tasks"]], get_embeddings_batch([r["task_text"] for r in t["tasks"]])):
                        cur.execute("UPDATE tasks SET task_embedding = %s WHERE task_id = %s", (vec, tid))
                if t["document_chunks"]:
                    for cid, vec in zip([r["chunk_id"] for r in t["document_chunks"]], get_embeddings_batch([r["chunk_text"] for r in t["document_chunks"]])):
                        cur.execute("UPDATE document_chunks SET chunk_embedding = %s WHERE chunk_id = %s", (vec, cid))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        for sub in FOLDERS:
            shutil.rmtree(root / project_id / sub, ignore_errors=True)
        tw.ensure_folders([project_id], root)
        files = golden_dir / "files"
        for path in sorted(files.rglob("*")) if files.is_dir() else []:
            if path.is_file():
                target = root / project_id / path.relative_to(files)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    finally:
        tw.resume(root)
    refresh_pending_meetings(project_id)
    return verify_project(project_id, golden_dir)


def refresh_pending_meetings(project_id: str, now: datetime = None) -> int:
    """A calendar invite has to be in the future, so each meeting still waiting for approval moves to 10:00 on the next business day (same length)."""
    now = now or datetime.now()
    day = now.date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    conn = get_connection()
    n = 0
    with conn.cursor() as cur:
        cur.execute("SELECT meeting_id, start_iso, end_iso FROM meetings WHERE project_id = %s AND status = 'pending_approval'", (project_id,))
        for mid, start, end in cur.fetchall():
            length = datetime.fromisoformat(end) - datetime.fromisoformat(start)
            new_start = datetime.combine(day, datetime.min.time()).replace(hour=10)
            cur.execute("UPDATE meetings SET start_iso = %s, end_iso = %s WHERE meeting_id = %s", (new_start.isoformat(), (new_start + length).isoformat(), mid))
            n += 1
    conn.commit()
    conn.close()
    return n


# ---------------------------------------------------------------- verify

def verify_project(project_id: str, golden_dir: Path = None, root=None) -> dict:
    """-> {"ok": bool, "differences": [text, ...]}: the live project against the manifest saved at export, and its files against the saved files."""
    golden_dir = Path(golden_dir or GOLDEN_DIR / project_id)
    root = Path(root or tw.ROOT)
    want = json.loads((golden_dir / "golden.json").read_text())["manifest"]
    have = manifest(project_id)
    diffs = []
    for table, n in want["counts"].items():
        if have["counts"].get(table) != n:
            diffs.append(f"{table}: {have['counts'].get(table)} rows, expected {n}")
    for k, v in want["numbers"].items():
        if have["numbers"].get(k) != v:
            diffs.append(f"{k}: {have['numbers'].get(k)}, expected {v}")
    if have["status"] != want["status"]:
        diffs.append(f"status: {have['status']}, expected {want['status']}")
    for k, v in want["plan"].items():
        if have["plan"].get(k) != v:
            diffs.append(f"plan {k}: {have['plan'].get(k)}, expected {v}")
    files = golden_dir / "files"
    expected = {p.relative_to(files) for p in files.rglob("*") if p.is_file()} if files.is_dir() else set()
    present = {p.relative_to(root / project_id) for sub in FOLDERS for p in (root / project_id / sub).rglob("*")
               if p.is_file() and not p.name.startswith(".") and not p.is_symlink()}
    for rel in sorted(expected - present):
        diffs.append(f"file missing: {rel}")
    for rel in sorted(present - expected):
        diffs.append(f"extra file: {rel}")
    for rel in sorted(expected & present):
        if (files / rel).read_bytes() != (root / project_id / rel).read_bytes():
            diffs.append(f"file differs: {rel}")
    return {"ok": not diffs, "differences": diffs}
