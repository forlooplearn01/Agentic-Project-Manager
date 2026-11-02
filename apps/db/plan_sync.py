"""The project plan read from a folder: each version of the plan file is applied to the project's tasks.

The plan FILE (an Excel sheet in projects/<id>/plan/) is the source of truth. Every time its contents change, `apply_plan_version` brings the
app's tasks into line with it:
  - a row is matched to a task by its Task ID (tasks.plan_ref); a row without a known id is matched by name (exact, meaning, then a model
    check, the same matcher the old plan upload used), and the matched task takes the id;
  - a row with no task creates one; a task that came from the plan (has a plan_ref) and is no longer in it is removed from the plan;
  - changes between versions are written to the change history (plan_changes, source 'plan_file'), so the history comes from the files and
    nobody maintains it; the first version read becomes the baseline;
  - a file that would remove most of the plan at once is HELD for a person to confirm, since that is usually the wrong file or sheet.
Tasks that were never in a plan file (no plan_ref) are left alone. DECISIONS.md "Plan and meetings by folder"."""
import json
import re
from datetime import date

from psycopg2.extras import Json, RealDictCursor

from apps.db.db import add_task_dependency, ensure_person_placeholder, get_connection, insert_task
from apps.utils.plan_file import ref_key

LARGE_REMOVAL_SHARE = 0.3  # a version that drops more than this share of the plan's tasks (and at least 4) is held for confirmation


def ensure_plan_sync_schema():
    """Idempotent, run with the hours schema at startup."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS plan_ref TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_plan_ref ON tasks(project_id, plan_ref)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS folder_files (
            file_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id VARCHAR NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('plan', 'meeting')),
            filename TEXT NOT NULL,
            file_hash TEXT,
            status TEXT NOT NULL CHECK (status IN ('applied', 'held', 'duplicate', 'failed')),
            reason TEXT,
            summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            applied_at TIMESTAMPTZ
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_folder_files_project ON folder_files(project_id, kind, created_at DESC)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS plan_suggestions (
            suggestion_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id VARCHAR NOT NULL,
            file_id UUID,
            source_name TEXT,
            kind TEXT NOT NULL CHECK (kind IN ('new_task', 'change')),
            task_id UUID REFERENCES tasks(task_id) ON DELETE SET NULL,
            task_text TEXT NOT NULL,
            fields JSONB NOT NULL DEFAULT '{}'::jsonb,
            changes JSONB NOT NULL DEFAULT '{}'::jsonb,
            confidence TEXT,
            state TEXT NOT NULL DEFAULT 'suggested',
            reason TEXT,
            plan_ref TEXT,
            jira_key TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decided_at TIMESTAMPTZ,
            merged_at TIMESTAMPTZ,
            confirmed_at TIMESTAMPTZ
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_plan_suggestions_project ON plan_suggestions(project_id, state, created_at DESC)")
    conn.commit()
    cur.close()
    conn.close()


# ---------------------------------------------------------------- the files that were read

def record_folder_file(project_id: str, kind: str, filename: str, file_hash: str, status: str, reason: str = None,
                       summary: dict = None, payload: dict = None) -> str:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO folder_files (project_id, kind, filename, file_hash, status, reason, summary, payload, applied_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s = 'applied' THEN NOW() END) RETURNING file_id
    """, (project_id, kind, filename, file_hash, status, reason, Json(summary or {}), Json(payload or {}, dumps=lambda o: json.dumps(o, default=str)), status))
    file_id = str(cur.fetchone()[0])
    conn.commit()
    cur.close()
    conn.close()
    return file_id


def update_folder_file(file_id: str, status: str = None, reason: str = None, summary_extra: dict = None):
    sets, vals = [], []
    if status is not None:
        sets.append("status = %s")
        vals.append(status)
        if status == "applied":
            sets.append("applied_at = NOW()")
    if reason is not None:
        sets.append("reason = %s")
        vals.append(reason)
    if summary_extra:
        sets.append("summary = summary || %s::jsonb")
        vals.append(json.dumps(summary_extra, default=str))
    if not sets:
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"UPDATE folder_files SET {', '.join(sets)} WHERE file_id = %s", (*vals, file_id))
    conn.commit()
    cur.close()
    conn.close()


def get_folder_file(file_id: str):
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM folder_files WHERE file_id = %s", (file_id,))
        row = cur.fetchone()
    conn.close()
    return _file_row(row) if row else None


def latest_folder_file(project_id: str, kind: str, filename: str):
    """The latest record for a file name. A plan file stays in its folder and is edited in place, so it is read again only when its contents
    differ from the last time it was looked at (changing it back to an older version counts as a change)."""
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM folder_files WHERE project_id = %s AND kind = %s AND filename = %s ORDER BY created_at DESC LIMIT 1",
                    (project_id, kind, filename))
        row = cur.fetchone()
    conn.close()
    return _file_row(row) if row else None


def find_applied_meeting_by_hash(project_id: str, file_hash: str):
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM folder_files WHERE project_id = %s AND kind = 'meeting' AND file_hash = %s AND status = 'applied' "
                    "ORDER BY created_at DESC LIMIT 1", (project_id, file_hash))
        row = cur.fetchone()
    conn.close()
    return _file_row(row) if row else None


def last_applied_plan_file(project_id: str):
    """The most recent plan version applied, with the layout recorded when it was read (the plan writer needs it)."""
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM folder_files WHERE project_id = %s AND kind = 'plan' AND status = 'applied' ORDER BY applied_at DESC LIMIT 1",
                    (project_id,))
        row = cur.fetchone()
    conn.close()
    return _file_row(row) if row else None


def list_folder_files(project_id: str, kind: str = None, limit: int = 30) -> list:
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM folder_files WHERE project_id = %s AND (%s::text IS NULL OR kind = %s) ORDER BY created_at DESC LIMIT %s",
                    (project_id, kind, kind, limit))
        rows = [_file_row(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _file_row(r) -> dict:
    d = dict(r)
    d["file_id"] = str(d["file_id"])
    for k in ("created_at", "applied_at"):
        d[k] = d[k].isoformat() if d.get(k) else None
    return d


# ---------------------------------------------------------------- applying a plan version

PLAN_FIELDS = (("owner", "owner"), ("status", "status"), ("phase", "phase"), ("estimate", "estimate_hours"),
               ("start_wk", "plan_start_week"), ("finish_wk", "plan_finish_week"))


def _norm_text(text) -> str:
    return re.sub(r"[^\w\s]", "", (text or "").lower().strip())


def _row_ref(row) -> str:
    """A row's id: its Task ID cell, or a stand-in made from its name when the plan has no id column."""
    return row.get("task_ref") or f"name:{_norm_text(row['label'])}"


def _fetch_tasks(cur, project_id: str) -> list:
    cur.execute("""SELECT task_id, task_text, owner, status, phase, plan_ref, estimate_hours, plan_start_week, plan_finish_week
                   FROM tasks WHERE project_id = %s AND is_active = TRUE ORDER BY created_at""", (project_id,))
    return [dict(r) for r in cur.fetchall()]


def match_rows_to_tasks(project_id: str, rows: list, use_llm: bool = True) -> tuple:
    """-> ({row index: task}, [tasks not matched by any row]). By Task ID first, then by name among the tasks not yet claimed."""
    from apps.utils.hours_import import propose_task_matches
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        tasks = _fetch_tasks(cur, project_id)
    conn.close()
    by_ref = {ref_key(t["plan_ref"]): t for t in tasks if t["plan_ref"]}
    matched, claimed = {}, set()
    for i, row in enumerate(rows):
        t = by_ref.get(ref_key(_row_ref(row)))
        if t and str(t["task_id"]) not in claimed:
            matched[i] = t
            claimed.add(str(t["task_id"]))
    pending = [i for i in range(len(rows)) if i not in matched]
    free = [t for t in tasks if str(t["task_id"]) not in claimed]
    if pending and free:
        proposals = propose_task_matches([{"label": rows[i]["label"], "person": rows[i]["owner"]} for i in pending], free, use_llm=use_llm, mode="plan")
        by_id = {str(t["task_id"]): t for t in free}
        for i, pr in zip(pending, proposals):
            if pr["task_id"] and pr["match"] in ("exact", "llm", "suggested") and str(pr["task_id"]) in by_id and str(pr["task_id"]) not in claimed:
                matched[i] = by_id[str(pr["task_id"])]
                claimed.add(str(pr["task_id"]))
    unmatched = [t for t in tasks if str(t["task_id"]) not in claimed]
    return matched, unmatched


def removals_to_confirm(unmatched: list, total_tasks_with_ref: int):
    """The tasks a version would remove from the plan, and a reason when that is suspiciously many."""
    removed = [t for t in unmatched if t["plan_ref"]]
    if len(removed) >= 4 and total_tasks_with_ref and len(removed) / total_tasks_with_ref > LARGE_REMOVAL_SHARE:
        return removed, (f"This version of the plan is missing {len(removed)} of the {total_tasks_with_ref} tasks that were in the plan before. "
                         "That usually means the wrong file or sheet. If it is right, apply it anyway.")
    return removed, None


def apply_plan_version(project_id: str, parsed: dict, filename: str, force: bool = False, use_llm: bool = True) -> dict:
    """Bring the project's tasks into line with a parsed plan file. -> {"status": "applied", ...counts...} or {"status": "held", "reason"}."""
    from apps.db.hours_db import get_hours_settings, set_hours_settings
    from apps.db.plan_db import log_plan_change, plan_summary, set_plan_baseline
    rows = parsed["rows"]
    matched, unmatched = match_rows_to_tasks(project_id, rows, use_llm=use_llm)
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM tasks WHERE project_id = %s AND is_active AND plan_ref IS NOT NULL", (project_id,))
        with_ref = cur.fetchone()[0]
    conn.close()
    removed, hold_reason = removals_to_confirm(unmatched, with_ref)
    if hold_reason and not force:
        return {"status": "held", "reason": hold_reason, "removed": len(removed)}

    first_read = plan_summary(project_id)["baseline_set_at"] is None
    added, changed, ref_to_task = [], [], {}
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for i, row in enumerate(rows):
                ref = _row_ref(row)
                task = matched.get(i)
                if task:
                    sets, log = {}, []
                    if task["plan_ref"] != ref:
                        sets["plan_ref"] = ref
                    if ref_key(task["plan_ref"]) == ref_key(ref) and _norm_text(task["task_text"]) != _norm_text(row["label"]):
                        sets["task_text"], sets["normalized_task_text"] = row["label"], _norm_text(row["label"])
                        log.append(("task_text", task["task_text"], row["label"]))
                    for src, col in PLAN_FIELDS:
                        new = row.get(src)
                        if new is not None and new != "" and (str(new) != str(task[col]) if col not in ("estimate_hours",) else task[col] is None or float(task[col]) != float(new)):
                            sets[col] = new
                            log.append((col, task[col], new))
                    if sets:
                        cur.execute(f"UPDATE tasks SET {', '.join(f'{k} = %s' for k in sets)}, updated_at = NOW() WHERE task_id = %s", (*sets.values(), task["task_id"]))
                    # filling in a blank owner or phase is not a change to the plan, so it is not counted or put in the history
                    log = [x for x in log if not (x[0] in ("owner", "phase") and x[1] in (None, ""))]
                    if log:
                        changed.append(str(task["task_id"]))
                        if not first_read:
                            for field, old, new in log:
                                log_plan_change(cur, project_id, task["task_id"], row["label"], field, old, new, "plan_file")
                    ref_to_task[ref_key(ref)] = str(task["task_id"])
                    ref_to_task[_norm_text(row["label"])] = str(task["task_id"])
                else:
                    task_id = str(insert_task({"task_text": row["label"], "normalized_task_text": _norm_text(row["label"]), "owner": row["owner"],
                                               "status": row.get("status") or "not_started", "deadline": None}, project_id))
                    cur.execute("""UPDATE tasks SET plan_ref = %s, phase = %s, estimate_hours = %s, plan_start_week = %s, plan_finish_week = %s
                                   WHERE task_id = %s""", (ref, row["phase"], row["estimate"], row["start_wk"], row["finish_wk"], task_id))
                    added.append({"task_id": task_id, "ref": ref})
                    ref_to_task[ref_key(ref)] = task_id
                    ref_to_task[_norm_text(row["label"])] = task_id
                    if not first_read:
                        log_plan_change(cur, project_id, task_id, row["label"], "task_added", None, row["estimate"], "plan_file")
                if row.get("owner"):
                    ensure_person_placeholder(project_id, row["owner"])
            for t in removed:
                cur.execute("UPDATE tasks SET is_active = FALSE, removed_at = NOW(), updated_at = NOW() WHERE task_id = %s", (t["task_id"],))
                cur.execute("DELETE FROM task_dependencies WHERE task_id = %s OR depends_on_task_id = %s", (t["task_id"], t["task_id"]))
                log_plan_change(cur, project_id, t["task_id"], t["task_text"], "task_removed", t["estimate_hours"], None, "plan_file")
                cur.execute("UPDATE plan_suggestions SET state = 'missing', reason = 'The task is no longer in the plan file.' "
                            "WHERE task_id = %s AND state = 'confirmed'", (t["task_id"],))
            total = sum(float(r["estimate"] or 0) for r in rows)
            if first_read:
                log_plan_change(cur, project_id, None, None, "plan_imported", None, f"{filename}: {len(rows)} tasks, {total:g} h", "plan_file")
            elif added or changed or removed:
                log_plan_change(cur, project_id, None, None, "plan_file_read", None,
                                f"{filename}: {len(added)} added, {len(changed)} changed, {len(removed)} removed", "plan_file")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # dependencies ("Depends on" column): ids or names of other rows
    for i, row in enumerate(rows):
        if not row.get("depends"):
            continue
        this = ref_to_task.get(ref_key(_row_ref(row)))
        for token in re.split(r"[;,]", row["depends"]):
            dep = ref_to_task.get(ref_key(token)) or ref_to_task.get(_norm_text(token))
            if this and dep:
                add_task_dependency(this, dep)

    current = get_hours_settings(project_id)
    updates = {}
    if parsed.get("budget_hours") is not None and parsed["budget_hours"] != current["budget_hours"]:
        updates["budget_hours"] = parsed["budget_hours"]
    if isinstance(parsed.get("kickoff_date"), date) and parsed["kickoff_date"] != current["kickoff_date"]:
        updates["kickoff_date"] = parsed["kickoff_date"]
    if parsed.get("role_budgets") and parsed["role_budgets"] != current["role_budgets"]:
        updates["role_budgets"] = parsed["role_budgets"]
    if updates:
        set_hours_settings(project_id, _source="plan_file", **updates)

    baseline_set = False
    if first_read:
        try:
            set_plan_baseline(project_id, source="plan_file")  # the first plan read is the baseline
            baseline_set = True
        except ValueError:
            pass
    return {"status": "applied", "tasks": len(rows), "added": len(added), "changed": len(changed), "removed": len(removed),
            "added_tasks": added, "baseline_set": baseline_set, "settings_changed": sorted(updates)}
