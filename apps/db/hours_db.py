"""Hours tracking storage: per-task estimates, the project hours budget, logged hours (time_entries) and the import history.
Kept apart from db.py only because it is one self-contained feature; connection handling and style are the same. All rules about
what the numbers MEAN live in apps/utils/hours.py."""
import json
import uuid
from datetime import date, timedelta

from psycopg2.extras import Json, RealDictCursor

from apps.db.db import get_connection, insert_task
from apps.utils.hours import last_workday_offset, week_start

NO_TASK = "00000000-0000-0000-0000-000000000000"  # stands in for "no task" inside the uniqueness key of time_entries
_KEY = f"project_id, lower(person), week_start, COALESCE(task_id, '{NO_TASK}'::uuid)"
TASK_HOURS_FIELDS = ("estimate_hours", "plan_start_week", "plan_finish_week", "hours_still_needed", "phase")


def ensure_hours_schema():
    """Idempotent, run at startup like the other ensure_* functions."""
    conn = get_connection()
    cur = conn.cursor()
    for col, typ in (("estimate_hours", "NUMERIC(8,2)"), ("plan_start_week", "INT"), ("plan_finish_week", "INT"),
                     ("hours_still_needed", "NUMERIC(8,2)")):
        cur.execute(f"ALTER TABLE tasks ADD COLUMN IF NOT EXISTS {col} {typ}")
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS phase TEXT")
    for col, typ in (("baseline_estimate_hours", "NUMERIC(8,2)"), ("baseline_start_week", "INT"), ("baseline_finish_week", "INT"),
                     ("removed_at", "TIMESTAMPTZ")):
        cur.execute(f"ALTER TABLE tasks ADD COLUMN IF NOT EXISTS {col} {typ}")
    cur.execute("ALTER TABLE project_people ADD COLUMN IF NOT EXISTS weekly_capacity_hours NUMERIC(6,2)")
    cur.execute("ALTER TABLE project_people ADD COLUMN IF NOT EXISTS skills TEXT[]")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS project_hours_settings (
            project_id VARCHAR PRIMARY KEY,
            budget_hours NUMERIC(10,2),
            kickoff_date DATE,
            role_budgets JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("ALTER TABLE project_hours_settings ADD COLUMN IF NOT EXISTS baseline_set_at TIMESTAMPTZ")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS plan_changes (
            change_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id VARCHAR NOT NULL,
            task_id UUID REFERENCES tasks(task_id) ON DELETE SET NULL,
            task_text TEXT,
            field TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            source TEXT NOT NULL DEFAULT 'edit',
            changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_plan_changes_project ON plan_changes(project_id, changed_at DESC)")
    cur.execute("ALTER TABLE project_hours_settings ADD COLUMN IF NOT EXISTS week_start_day SMALLINT NOT NULL DEFAULT 0 "
                "CHECK (week_start_day BETWEEN 0 AND 6)")  # 0 = Monday ... 6 = Sunday: which day a timesheet week starts on
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hours_imports (
            import_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id VARCHAR NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('timesheet', 'plan')),
            filename TEXT,
            layout TEXT,
            status TEXT NOT NULL DEFAULT 'preview' CHECK (status IN ('preview', 'applied', 'undone')),
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            applied_at TIMESTAMPTZ
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS time_entries (
            entry_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id VARCHAR NOT NULL,
            person TEXT NOT NULL,
            week_start DATE NOT NULL,
            task_id UUID REFERENCES tasks(task_id) ON DELETE SET NULL,
            hours NUMERIC(8,2) NOT NULL CHECK (hours >= 0),
            note TEXT,
            source TEXT NOT NULL DEFAULT 'manual',
            import_id UUID,
            source_ref TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_time_entries_key ON time_entries ({_KEY})")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_time_entries_project ON time_entries(project_id, week_start)")
    # Folder pickup: where an import came from, the file's fingerprint (to recognise the same file twice), and why it was held or failed.
    cur.execute("ALTER TABLE hours_imports ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'upload'")
    cur.execute("ALTER TABLE hours_imports ADD COLUMN IF NOT EXISTS file_hash TEXT")
    cur.execute("ALTER TABLE hours_imports ADD COLUMN IF NOT EXISTS reason TEXT")
    cur.execute("ALTER TABLE hours_imports DROP CONSTRAINT IF EXISTS hours_imports_status_check")
    cur.execute("ALTER TABLE hours_imports ADD CONSTRAINT hours_imports_status_check "
                "CHECK (status IN ('preview', 'applied', 'undone', 'held', 'duplicate', 'failed'))")
    conn.commit()
    cur.close()
    conn.close()
    from apps.db.plan_sync import ensure_plan_sync_schema  # plan ids, plan / meeting files, suggestions
    ensure_plan_sync_schema()


def merge_task_hours(cur, source_task_id: str, target_task_id: str):
    """Inside merge_tasks' transaction (a RealDictCursor): the merged-away duplicate's logged hours move to the surviving task
    (added together where the same person already has hours that week on both), and its estimate fills the survivor's blanks."""
    cur.execute("SELECT entry_id, project_id, lower(person) AS lp, week_start, hours FROM time_entries WHERE task_id = %s", (source_task_id,))
    for e in cur.fetchall():
        cur.execute("SELECT entry_id FROM time_entries WHERE task_id = %s AND project_id = %s AND lower(person) = %s AND week_start = %s",
                    (target_task_id, e["project_id"], e["lp"], e["week_start"]))
        existing = cur.fetchone()
        if existing:
            cur.execute("UPDATE time_entries SET hours = hours + %s, updated_at = NOW() WHERE entry_id = %s", (e["hours"], existing["entry_id"]))
            cur.execute("DELETE FROM time_entries WHERE entry_id = %s", (e["entry_id"],))
        else:
            cur.execute("UPDATE time_entries SET task_id = %s, updated_at = NOW() WHERE entry_id = %s", (target_task_id, e["entry_id"]))
    cur.execute("""
        UPDATE tasks t SET estimate_hours = COALESCE(t.estimate_hours, s.estimate_hours),
                           plan_start_week = COALESCE(t.plan_start_week, s.plan_start_week),
                           plan_finish_week = COALESCE(t.plan_finish_week, s.plan_finish_week),
                           hours_still_needed = COALESCE(t.hours_still_needed, s.hours_still_needed),
                           phase = COALESCE(t.phase, s.phase),
                           baseline_estimate_hours = COALESCE(t.baseline_estimate_hours, s.baseline_estimate_hours),
                           baseline_start_week = COALESCE(t.baseline_start_week, s.baseline_start_week),
                           baseline_finish_week = COALESCE(t.baseline_finish_week, s.baseline_finish_week)
        FROM tasks s WHERE t.task_id = %s AND s.task_id = %s
    """, (target_task_id, source_task_id))


def delete_project_hours(cur, project_id: str):
    """Called inside delete_project's transaction."""
    for table in ("time_entries", "project_hours_settings", "hours_imports", "plan_changes", "plan_suggestions", "folder_files"):
        cur.execute(f"DELETE FROM {table} WHERE project_id = %s", (project_id,))


def _f(x):
    return None if x is None else float(x)


# ---------------------------------------------------------------- settings

def get_hours_settings(project_id: str) -> dict:
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT budget_hours, kickoff_date, role_budgets, week_start_day FROM project_hours_settings WHERE project_id = %s", (project_id,))
        row = cur.fetchone()
    conn.close()
    if not row:
        return {"budget_hours": None, "kickoff_date": None, "role_budgets": {}, "week_start_day": 0}
    return {"budget_hours": _f(row["budget_hours"]), "kickoff_date": row["kickoff_date"],
            "role_budgets": {k: float(v) for k, v in (row["role_budgets"] or {}).items()}, "week_start_day": int(row["week_start_day"])}


def set_hours_settings(project_id: str, _source: str = "edit", **fields) -> dict:
    """Partial update: only the keys passed are changed (pass None to clear budget_hours / kickoff_date).
    Changing week_start_day re-files every logged week under the new convention in the same transaction: each stored week moves to the
    new week that contains its middle day, so "the week of 19 Oct" stays the week of 19 Oct (the mapping is reversible)."""
    allowed = {"budget_hours", "kickoff_date", "role_budgets", "week_start_day"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if "budget_hours" in fields and fields["budget_hours"] is not None and float(fields["budget_hours"]) < 0:
        raise ValueError("The budget cannot be negative.")
    for role, h in (fields.get("role_budgets") or {}).items():
        if float(h) < 0:
            raise ValueError(f"The budget for '{role}' cannot be negative.")
    if "week_start_day" in fields and (fields["week_start_day"] is None or int(fields["week_start_day"]) not in range(7)):
        raise ValueError("The week must start on a day from 0 (Monday) to 6 (Sunday).")
    current = get_hours_settings(project_id)
    merged = {**current, **fields}
    merged["week_start_day"] = int(merged["week_start_day"])
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO project_hours_settings (project_id, budget_hours, kickoff_date, role_budgets, week_start_day, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (project_id) DO UPDATE SET budget_hours = EXCLUDED.budget_hours, kickoff_date = EXCLUDED.kickoff_date,
                    role_budgets = EXCLUDED.role_budgets, week_start_day = EXCLUDED.week_start_day, updated_at = NOW()
            """, (project_id, merged["budget_hours"], merged["kickoff_date"], Json(merged["role_budgets"] or {}), merged["week_start_day"]))
            from apps.db.plan_db import log_plan_change
            for field in ("budget_hours", "kickoff_date"):
                log_plan_change(cur, project_id, None, None, field, current[field], merged[field], _source)
            if merged["week_start_day"] != current["week_start_day"]:
                cur.execute("SELECT entry_id, week_start FROM time_entries WHERE project_id = %s", (project_id,))
                for entry_id, old_week in cur.fetchall():
                    cur.execute("UPDATE time_entries SET week_start = %s WHERE entry_id = %s",
                                (week_start(old_week + timedelta(days=3), merged["week_start_day"]), entry_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_hours_settings(project_id)


# ---------------------------------------------------------------- inputs for the calculations

def fetch_active_tasks_for_hours(project_id: str) -> list:
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT task_id, task_text, owner, status, deadline, estimate_hours, plan_start_week, plan_finish_week,
                   hours_still_needed
            FROM tasks WHERE project_id = %s AND is_active = TRUE ORDER BY created_at
        """, (project_id,))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def fetch_time_entries(project_id: str) -> list:
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT person, week_start, task_id, hours, note, source FROM time_entries WHERE project_id = %s "
                    "ORDER BY week_start, person", (project_id,))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        r["hours"] = float(r["hours"])
    return rows


def fetch_hours_inputs(project_id: str) -> tuple:
    return get_hours_settings(project_id), fetch_active_tasks_for_hours(project_id), fetch_time_entries(project_id)


def project_ids_with_hours() -> list:
    """Projects that have anything hours-related, for the Dashboard card."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT project_id FROM project_hours_settings WHERE budget_hours IS NOT NULL
            UNION SELECT project_id FROM time_entries
            UNION SELECT project_id FROM tasks WHERE estimate_hours IS NOT NULL AND is_active
            ORDER BY 1
        """)
        ids = [r[0] for r in cur.fetchall()]
    conn.close()
    return ids


# ---------------------------------------------------------------- per-task estimate

def set_task_hours(task_id: str, _source: str = "edit", _log: bool = True, **fields) -> dict:
    """Partial update of a task's plan fields: estimate_hours / plan_start_week / plan_finish_week / hours_still_needed / phase (pass None
    to clear). Every field that really changed is written to the plan change history (old -> new) unless _log is False."""
    from apps.db.plan_db import log_plan_change
    fields = {k: v for k, v in fields.items() if k in TASK_HOURS_FIELDS}
    for k in ("estimate_hours", "hours_still_needed"):
        if fields.get(k) is not None and float(fields[k]) < 0:
            raise ValueError("Hours cannot be negative.")
    for k in ("plan_start_week", "plan_finish_week"):
        if fields.get(k) is not None and int(fields[k]) < 1:
            raise ValueError("Weeks start at 1.")
    if "phase" in fields:
        phase = " ".join(str(fields["phase"] or "").split())
        if len(phase) > 80:
            raise ValueError("A phase name can be at most 80 characters.")
        fields["phase"] = phase or None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SELECT project_id, task_text, {', '.join(TASK_HOURS_FIELDS)} FROM tasks WHERE task_id = %s", (task_id,))
            row = cur.fetchone()
            if not row:
                raise LookupError(f"No task with id '{task_id}'.")
            start = fields.get("plan_start_week", row["plan_start_week"])
            finish = fields.get("plan_finish_week", row["plan_finish_week"])
            if start is not None and finish is not None and int(finish) < int(start):
                raise ValueError("The finish week cannot be before the start week.")
            if fields:
                sets = ", ".join(f"{k} = %s" for k in fields)
                cur.execute(f"UPDATE tasks SET {sets}, updated_at = NOW() WHERE task_id = %s", (*fields.values(), task_id))
                if _log:
                    for k, v in fields.items():
                        log_plan_change(cur, row["project_id"], task_id, row["task_text"], k, row[k], v, _source)
            cur.execute(f"SELECT task_id, {', '.join(TASK_HOURS_FIELDS)} FROM tasks WHERE task_id = %s", (task_id,))
            out = dict(cur.fetchone())
        conn.commit()
    finally:
        conn.close()
    return {k: (_f(v) if k in ("estimate_hours", "hours_still_needed") else v) for k, v in out.items()}


# ---------------------------------------------------------------- people capacity

def set_person_capacity(project_id: str, name: str, weekly_capacity_hours="__unset__", skills="__unset__") -> dict:
    sets, vals = [], []
    if weekly_capacity_hours != "__unset__":
        if weekly_capacity_hours is not None and not 0 <= float(weekly_capacity_hours) <= 168:
            raise ValueError("Weekly capacity must be between 0 and 168 hours.")
        sets.append("weekly_capacity_hours = %s")
        vals.append(weekly_capacity_hours)
    if skills != "__unset__":
        seen = {}
        for s in (skills or []):
            if s and s.strip():
                seen.setdefault(s.strip().lower(), s.strip())  # one spelling per skill, the first given
        clean = sorted(seen.values(), key=str.lower)
        sets.append("skills = %s")
        vals.append(clean or None)
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if sets:
                cur.execute(f"UPDATE project_people SET {', '.join(sets)} WHERE project_id = %s AND lower(name) = lower(%s)",
                            (*vals, project_id, name))
                if cur.rowcount == 0:
                    raise LookupError(f"No person named '{name}' in project '{project_id}'.")
            cur.execute("SELECT name, email, role, weekly_capacity_hours, skills FROM project_people "
                        "WHERE project_id = %s AND lower(name) = lower(%s)", (project_id, name))
            row = cur.fetchone()
            if not row:
                raise LookupError(f"No person named '{name}' in project '{project_id}'.")
        conn.commit()
    finally:
        conn.close()
    row = dict(row)
    row["weekly_capacity_hours"] = _f(row["weekly_capacity_hours"])
    return row


# ---------------------------------------------------------------- timesheet (manual entry)

def get_timesheet_week(project_id: str, person: str, week: date) -> list:
    start_day = get_hours_settings(project_id)["week_start_day"]
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT task_id, hours, note FROM time_entries
            WHERE project_id = %s AND lower(person) = lower(%s) AND week_start = %s ORDER BY created_at
        """, (project_id, person, week_start(week, start_day)))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        r["hours"] = float(r["hours"])
    return rows


def _canonical_person(cur, project_id: str, person: str) -> str:
    cur.execute("SELECT name FROM project_people WHERE project_id = %s AND lower(name) = lower(%s)", (project_id, person.strip()))
    row = cur.fetchone()
    if not row:
        raise LookupError(f"'{person}' is not on this project's people list.")
    return row[0]


def save_timesheet_week(project_id: str, person: str, week: date, entries: list) -> list:
    """Replace everything one person logged for one week (the grid always shows the whole week). entries:
    [{task_id: str | None, hours, note}] - task_id None is the 'Other' row (meetings, admin, time on no listed task)."""
    wk = week_start(week, get_hours_settings(project_id)["week_start_day"])
    merged = {}
    for e in entries:
        hours = float(e.get("hours") or 0)
        if hours < 0:
            raise ValueError("Hours cannot be negative.")
        if hours == 0:
            continue
        key = e.get("task_id") or None
        prev = merged.get(key, {"hours": 0.0, "note": None})
        merged[key] = {"hours": prev["hours"] + hours, "note": e.get("note") or prev["note"]}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            name = _canonical_person(cur, project_id, person)
            for task_id in [k for k in merged if k]:
                cur.execute("SELECT 1 FROM tasks WHERE task_id = %s AND project_id = %s", (task_id, project_id))
                if not cur.fetchone():
                    raise LookupError(f"Task '{task_id}' is not in project '{project_id}'.")
            cur.execute("DELETE FROM time_entries WHERE project_id = %s AND lower(person) = lower(%s) AND week_start = %s",
                        (project_id, name, wk))
            for task_id, v in merged.items():
                cur.execute("""
                    INSERT INTO time_entries (project_id, person, week_start, task_id, hours, note, source)
                    VALUES (%s, %s, %s, %s, %s, %s, 'manual')
                """, (project_id, name, wk, task_id, v["hours"], v["note"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_timesheet_week(project_id, name, wk)


# ---------------------------------------------------------------- imports

def create_import_record(project_id: str, kind: str, filename: str, layout: str, payload: dict, source: str = "upload",
                         file_hash: str = None, status: str = "preview", reason: str = None) -> str:
    import_id = str(uuid.uuid4())
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO hours_imports (import_id, project_id, kind, filename, layout, payload, source, file_hash, status, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (import_id, project_id, kind, filename, layout, Json(payload, dumps=lambda o: json.dumps(o, default=str)), source, file_hash,
                 status, reason))
    conn.commit()
    cur.close()
    conn.close()
    return import_id


def get_import(import_id: str) -> dict:
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM hours_imports WHERE import_id = %s", (import_id,))
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_imports(project_id: str) -> list:
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT import_id, kind, filename, layout, status, summary, created_at, applied_at FROM hours_imports
            WHERE project_id = %s AND status <> 'preview' ORDER BY created_at DESC LIMIT 50
        """, (project_id,))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def apply_timesheet_import(import_id: str, entries: list) -> dict:
    """entries: [{person (canonical), week_start (date), task_id | None, hours, source_ref}]. Rows with the same
    (person, week, task) are summed, then WRITTEN OVER whatever is stored for that key - so re-uploading the same or a
    cumulative file never double counts. What each key held before is saved so the import can be undone."""
    imp = get_import(import_id)
    if not imp or imp["kind"] != "timesheet":
        raise LookupError("No such timesheet import.")
    if imp["status"] not in ("preview", "held"):  # a held folder file is applied after a person has reviewed it
        raise ValueError(f"This import was already {imp['status']}.")
    merged = {}
    for e in entries:
        k = (e["person"].lower(), e["week_start"], e.get("task_id"))
        cur_v = merged.setdefault(k, {"person": e["person"], "week_start": e["week_start"], "task_id": e.get("task_id"),
                                       "hours": 0.0, "refs": []})
        cur_v["hours"] += float(e["hours"])
        if e.get("source_ref"):
            cur_v["refs"].append(e["source_ref"])
    project_id = imp["project_id"]
    previous = []
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for v in merged.values():
                cur.execute(f"""
                    SELECT person, week_start, task_id, hours, note, source, import_id, source_ref FROM time_entries
                    WHERE project_id = %s AND lower(person) = lower(%s) AND week_start = %s
                      AND COALESCE(task_id, '{NO_TASK}'::uuid) = COALESCE(%s::uuid, '{NO_TASK}'::uuid)
                """, (project_id, v["person"], v["week_start"], v["task_id"]))
                old = cur.fetchone()
                if old:
                    previous.append({k: (str(x) if k in ("week_start", "task_id", "import_id") and x is not None else
                                         (float(x) if k == "hours" else x)) for k, x in old.items()})
                cur.execute(f"""
                    INSERT INTO time_entries (project_id, person, week_start, task_id, hours, source, import_id, source_ref)
                    VALUES (%s, %s, %s, %s, %s, 'import', %s, %s)
                    ON CONFLICT ({_KEY}) DO UPDATE SET hours = EXCLUDED.hours, source = 'import', import_id = EXCLUDED.import_id,
                        source_ref = EXCLUDED.source_ref, updated_at = NOW()
                """, (project_id, v["person"], v["week_start"], v["task_id"], v["hours"], import_id, "; ".join(v["refs"])[:500]))
            summary = {"entries_written": len(merged), "hours_total": round(sum(v["hours"] for v in merged.values()), 2),
                       "replaced": len(previous), "previous": previous}
            cur.execute("UPDATE hours_imports SET status = 'applied', applied_at = NOW(), summary = %s WHERE import_id = %s",
                        (Json(summary), import_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {k: v for k, v in summary.items() if k != "previous"}


def apply_plan_import(import_id: str, items: list, settings_update: dict) -> dict:
    """items: [{action: 'update', task_id, estimate_hours, plan_start_week, plan_finish_week} |
               {action: 'create', task_text, owner, estimate_hours, plan_start_week, plan_finish_week}].
    Previous values are kept in the import record so it can be undone."""
    imp = get_import(import_id)
    if not imp or imp["kind"] != "plan":
        raise LookupError("No such plan import.")
    if imp["status"] != "preview":
        raise ValueError(f"This import was already {imp['status']}.")
    project_id = imp["project_id"]
    previous, created = [], []
    for it in items:
        if it["action"] == "create":
            tid = insert_task({"task_text": it["task_text"], "normalized_task_text": it["task_text"].lower(),
                               "owner": it.get("owner"), "status": "not_started", "deadline": None}, project_id)
            created.append(str(tid))
            set_task_hours(str(tid), _log=False, estimate_hours=it.get("estimate_hours"), plan_start_week=it.get("plan_start_week"),
                           plan_finish_week=it.get("plan_finish_week"), phase=it.get("phase"))
        else:
            conn = get_connection()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(f"SELECT task_id, {', '.join(TASK_HOURS_FIELDS)} FROM tasks WHERE task_id = %s AND project_id = %s",
                            (it["task_id"], project_id))
                old = cur.fetchone()
            conn.close()
            if not old:
                continue
            previous.append({k: (str(v) if k == "task_id" else _f(v) if "hours" in k else v) for k, v in old.items()})
            set_task_hours(it["task_id"], _log=False, estimate_hours=it.get("estimate_hours"), plan_start_week=it.get("plan_start_week"),
                           plan_finish_week=it.get("plan_finish_week"), phase=it.get("phase"))
    prev_settings = get_hours_settings(project_id)
    if settings_update:
        set_hours_settings(project_id, _source="import", **settings_update)
    from apps.db.plan_db import log_plan_change, plan_summary, set_plan_baseline
    conn = get_connection()
    with conn.cursor() as cur:  # one line in the history for the whole import, not one per task
        total = sum(float(i.get("estimate_hours") or 0) for i in items)
        log_plan_change(cur, project_id, None, None, "plan_imported", None, f"{imp['filename']}: {len(items)} tasks, {total:g} h", "import")
    conn.commit()
    conn.close()
    baseline_by_import = False
    if plan_summary(project_id)["baseline_set_at"] is None and items:
        set_plan_baseline(project_id, source="import")  # the first plan loaded is the baseline
        baseline_by_import = True
    summary = {"tasks_updated": len(previous), "tasks_created": len(created), "created_task_ids": created, "previous": previous,
              "baseline_set_by_import": baseline_by_import,
              "previous_settings": {k: (str(v) if k == "kickoff_date" and v else v) for k, v in prev_settings.items()},
              "settings_changed": sorted(settings_update or {})}
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE hours_imports SET status = 'applied', applied_at = NOW(), summary = %s WHERE import_id = %s",
                (Json(summary), import_id))
    conn.commit()
    cur.close()
    conn.close()
    return {k: v for k, v in summary.items() if k not in ("previous", "previous_settings", "created_task_ids")}


def undo_import(import_id: str) -> dict:
    imp = get_import(import_id)
    if not imp:
        raise LookupError("No such import.")
    if imp["status"] != "applied":
        raise ValueError("Only an applied import can be undone.")
    project_id, s = imp["project_id"], imp["summary"] or {}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if imp["kind"] == "timesheet":
                # Only rows this import still owns are touched: a later import that overwrote a key keeps its own value.
                cur.execute("DELETE FROM time_entries WHERE import_id = %s", (import_id,))
                for p in s.get("previous", []):
                    cur.execute("""
                        INSERT INTO time_entries (project_id, person, week_start, task_id, hours, note, source, import_id, source_ref)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (project_id, p["person"], p["week_start"], p["task_id"], p["hours"], p["note"], p["source"],
                          p["import_id"], p["source_ref"]))
            else:
                for p in s.get("previous", []):
                    cur.execute("UPDATE tasks SET estimate_hours = %s, plan_start_week = %s, plan_finish_week = %s, "
                                "hours_still_needed = %s, phase = %s WHERE task_id = %s",
                                (p["estimate_hours"], p["plan_start_week"], p["plan_finish_week"], p["hours_still_needed"], p.get("phase"), p["task_id"]))
                for tid in s.get("created_task_ids", []):
                    cur.execute("UPDATE tasks SET is_active = FALSE WHERE task_id = %s", (tid,))
                from apps.db.plan_db import clear_plan_baseline, log_plan_change
                if s.get("baseline_set_by_import"):
                    clear_plan_baseline(cur, project_id)
                log_plan_change(cur, project_id, None, None, "import_undone", None, imp["filename"], "import")
            cur.execute("UPDATE hours_imports SET status = 'undone' WHERE import_id = %s", (import_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if imp["kind"] == "plan" and s.get("settings_changed"):
        old = s["previous_settings"]
        set_hours_settings(project_id, budget_hours=old["budget_hours"],
                           kickoff_date=date.fromisoformat(old["kickoff_date"]) if old.get("kickoff_date") else None,
                           role_budgets=old["role_budgets"])
    return {"status": "undone"}


# ---------------------------------------------------------------- reports (database inputs + the rules in apps/utils/hours.py)

def project_hours_report(project_id: str, as_of: date = None) -> dict:
    """as_of None = the end of the latest week that has hours logged (never later than today), so a project's numbers do not drift
    with the calendar between timesheets; a note says so when no hours have been logged for over a week."""
    from apps.utils.hours import compute_project_hours
    settings, tasks, entries = fetch_hours_inputs(project_id)
    today = date.today()
    basis_note = None
    if as_of is None:
        as_of = today
        if entries:
            offset = last_workday_offset(settings["week_start_day"])
            last_friday = max(e["week_start"] for e in entries) + timedelta(days=offset)
            as_of = min(today, last_friday)
            if today - last_friday > timedelta(days=7):
                basis_note = (f"No hours have been logged since the week of {(last_friday - timedelta(days=offset)).isoformat()}, so work done is "
                              f"compared with the plan as of {last_friday.isoformat()}.")
    report = compute_project_hours(settings, tasks, entries, as_of)
    if basis_note:
        report["notes"].append(basis_note)
    report["kickoff_date"] = settings["kickoff_date"].isoformat() if settings["kickoff_date"] else None
    report["role_budgets"] = settings["role_budgets"]
    report["week_start_day"] = settings["week_start_day"]
    return report


def availability_report(project_id: str, window_start: date, window_end: date, skill: str = None, as_of: date = None) -> dict:
    from apps.db.db import list_project_people
    from apps.utils.hours import compute_availability
    settings, tasks, entries = fetch_hours_inputs(project_id)
    return compute_availability(list_project_people(project_id), tasks, entries, settings, window_start, window_end,
                                as_of or date.today(), skill)


# ---------------------------------------------------------------- folder pickup: records, lookups, moving hours between tasks

def update_import(import_id: str, status: str = None, reason: str = None, summary_extra: dict = None, payload_extra: dict = None):
    """Change an import's status / reason and merge extra keys into its summary or payload (JSON)."""
    sets, vals = [], []
    if status is not None:
        sets.append("status = %s")
        vals.append(status)
    if reason is not None:
        sets.append("reason = %s")
        vals.append(reason)
    if summary_extra:
        sets.append("summary = summary || %s::jsonb")
        vals.append(json.dumps(summary_extra, default=str))
    if payload_extra:
        sets.append("payload = payload || %s::jsonb")
        vals.append(json.dumps(payload_extra, default=str))
    if not sets:
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"UPDATE hours_imports SET {', '.join(sets)} WHERE import_id = %s", (*vals, import_id))
    conn.commit()
    cur.close()
    conn.close()


def find_applied_import_by_hash(project_id: str, file_hash: str):
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT import_id, filename, applied_at FROM hours_imports WHERE project_id = %s AND file_hash = %s AND status = 'applied' "
                    "ORDER BY applied_at DESC LIMIT 1", (project_id, file_hash))
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_project_ids() -> list:
    """Projects that exist (have at least one active task): the only ones a timesheet folder can belong to."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT project_id FROM tasks WHERE project_id IS NOT NULL AND is_active ORDER BY 1")
        ids = [r[0] for r in cur.fetchall()]
    conn.close()
    return ids


def list_folder_imports(project_id: str, limit: int = 30) -> list:
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT import_id, filename, layout, status, reason, summary, created_at, applied_at, (payload->'rows') IS NOT NULL AS has_rows
            FROM hours_imports
            WHERE project_id = %s AND kind = 'timesheet' AND source = 'folder' ORDER BY created_at DESC LIMIT %s
        """, (project_id, limit))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def last_applied_at(project_id: str):
    """When hours last arrived from an import (any source); the Hours page watches this to refresh itself."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(applied_at) FROM hours_imports WHERE project_id = %s AND status IN ('applied', 'undone')", (project_id,))
        value = cur.fetchone()[0]
    conn.close()
    return value


def reassign_import_group(import_id: str, label, person: str, task_id) -> dict:
    """Move the hours of one 'label by person' group of an APPLIED timesheet import to another task (None = Other). Totals never change;
    the import stays undoable because the entries it touches are taken over by it (what they held before is added to its undo snapshot)."""
    imp = get_import(import_id)
    if not imp or imp["kind"] != "timesheet":
        raise LookupError("No such timesheet import.")
    if imp["status"] != "applied":
        raise ValueError("Only an applied import can be corrected.")
    project_id, summary = imp["project_id"], imp["summary"] or {}
    group = [r for r in imp["payload"]["rows"] if (r["label"] or None) == (label or None) and r["person"] and r["person"].lower() == person.lower()]
    item = next((c for c in summary.get("to_check", []) if (c["label"] or None) == (label or None) and c["person"].lower() == person.lower()), None)
    if not group or item is None:
        raise LookupError("That group of hours is not part of this import.")
    old_task = item["task_id"]
    if task_id == old_task:
        return {"moved_hours": 0}
    previous = summary.get("previous", [])
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if task_id:
                cur.execute("SELECT task_text FROM tasks WHERE task_id = %s AND project_id = %s AND is_active", (task_id, project_id))
                found = cur.fetchone()
                if not found:
                    raise LookupError("That task is not in this project.")
                new_text = found["task_text"]
            else:
                new_text = None
            moved = 0.0
            for r in group:
                wk, hours = date.fromisoformat(r["week_start"]), float(r["hours"])
                for key_task, sign in ((old_task, -1), (task_id, 1)):
                    cur.execute(f"""
                        SELECT entry_id, hours, import_id, person, week_start, task_id, note, source, source_ref FROM time_entries
                        WHERE project_id = %s AND lower(person) = lower(%s) AND week_start = %s
                          AND COALESCE(task_id, '{NO_TASK}'::uuid) = COALESCE(%s::uuid, '{NO_TASK}'::uuid)
                    """, (project_id, r["person"], wk, key_task))
                    e = cur.fetchone()
                    if e and str(e["import_id"] or "") != import_id:  # take it over, keeping what it held for the undo
                        previous.append({"person": e["person"], "week_start": str(e["week_start"]), "task_id": str(e["task_id"]) if e["task_id"] else None,
                                         "hours": float(e["hours"]), "note": e["note"], "source": e["source"],
                                         "import_id": str(e["import_id"]) if e["import_id"] else None, "source_ref": e["source_ref"]})
                    if sign < 0:
                        if e:
                            left = float(e["hours"]) - hours
                            if left <= 0.0001:
                                cur.execute("DELETE FROM time_entries WHERE entry_id = %s", (e["entry_id"],))
                            else:
                                cur.execute("UPDATE time_entries SET hours = %s, import_id = %s, updated_at = NOW() WHERE entry_id = %s", (left, import_id, e["entry_id"]))
                    elif e:
                        cur.execute("UPDATE time_entries SET hours = hours + %s, import_id = %s, updated_at = NOW() WHERE entry_id = %s", (hours, import_id, e["entry_id"]))
                    else:
                        cur.execute("INSERT INTO time_entries (project_id, person, week_start, task_id, hours, source, import_id, source_ref) "
                                    "VALUES (%s, %s, %s, %s, %s, 'import', %s, %s)", (project_id, r["person"], wk, task_id, hours, import_id, r["source_ref"]))
                moved += hours
            item["task_id"], item["task_text"], item["match"] = task_id, new_text, "moved"
            summary["previous"] = previous
            cur.execute("UPDATE hours_imports SET summary = %s WHERE import_id = %s", (Json(summary), import_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"moved_hours": round(moved, 2), "task_text": new_text}
