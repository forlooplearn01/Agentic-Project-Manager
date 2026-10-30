"""The project plan, maintained in the app: baseline, change history, adding and removing tasks, and the summary the Plan view shows.
The plan itself is the plan columns on `tasks` (estimate_hours, plan_start_week, plan_finish_week, phase, hours_still_needed, see
hours_db.py); this module adds what makes it a PLAN you can maintain over time. DECISIONS.md "The plan is maintained in the app".

  baseline  the plan as first agreed: tasks.baseline_* copied once, plus project_hours_settings.baseline_set_at. A task created after
            that moment is "added after baseline" (scope growth); one removed through the plan keeps its baseline hours in the baseline total.
  history   plan_changes: one row per change (what, from, to, when). No "who": the app has no sign-in yet."""
from datetime import timedelta

from psycopg2.extras import RealDictCursor

from apps.db.db import get_connection, insert_task, ensure_person_placeholder

HISTORY_FIELD_LABELS = {
    "estimate_hours": "Estimate", "plan_start_week": "Start week", "plan_finish_week": "Finish week", "phase": "Phase",
    "hours_still_needed": "Hours still needed", "budget_hours": "Hours budget", "kickoff_date": "Kickoff date",
    "task_added": "Task added", "task_removed": "Task removed", "baseline_set": "Baseline set", "plan_imported": "Plan imported",
    "import_undone": "Plan import undone",
}


def _text(v):
    if v is None:
        return None
    if isinstance(v, float) or (hasattr(v, "as_tuple") and not isinstance(v, str)):
        v = float(v)
        return f"{v:g}"
    return str(v)


def log_plan_change(cur, project_id: str, task_id, task_text, field: str, old, new, source: str = "edit"):
    """Records one change; nothing is written when the value did not actually change."""
    old_t, new_t = _text(old), _text(new)
    if field not in ("task_added", "task_removed", "baseline_set", "plan_imported", "import_undone") and old_t == new_t:
        return
    cur.execute("""
        INSERT INTO plan_changes (project_id, task_id, task_text, field, old_value, new_value, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (project_id, task_id, task_text, field, old_t, new_t, source))


# ---------------------------------------------------------------- baseline

def clear_plan_baseline(cur, project_id: str):
    """Used when the import that set the baseline is undone, and by the demo reset."""
    cur.execute("UPDATE tasks SET baseline_estimate_hours = NULL, baseline_start_week = NULL, baseline_finish_week = NULL "
                "WHERE project_id = %s", (project_id,))
    cur.execute("UPDATE project_hours_settings SET baseline_set_at = NULL WHERE project_id = %s", (project_id,))


def set_plan_baseline(project_id: str, source: str = "edit") -> dict:
    """Freeze the plan as it stands now. Only once per project: a baseline is the plan as first agreed."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT baseline_set_at FROM project_hours_settings WHERE project_id = %s", (project_id,))
            row = cur.fetchone()
            if row and row["baseline_set_at"]:
                raise ValueError("This project already has a baseline.")
            cur.execute("""
                SELECT COUNT(*) AS n, COALESCE(SUM(estimate_hours), 0) AS hours FROM tasks
                WHERE project_id = %s AND is_active AND (estimate_hours IS NOT NULL OR plan_start_week IS NOT NULL)
            """, (project_id,))
            found = cur.fetchone()
            if not found["n"]:
                raise ValueError("Add estimates or planned weeks to some tasks before setting a baseline.")
            cur.execute("""
                UPDATE tasks SET baseline_estimate_hours = estimate_hours, baseline_start_week = plan_start_week,
                                 baseline_finish_week = plan_finish_week
                WHERE project_id = %s AND is_active AND (estimate_hours IS NOT NULL OR plan_start_week IS NOT NULL)
            """, (project_id,))
            cur.execute("""
                INSERT INTO project_hours_settings (project_id, baseline_set_at) VALUES (%s, NOW())
                ON CONFLICT (project_id) DO UPDATE SET baseline_set_at = NOW()
            """, (project_id,))
            log_plan_change(cur, project_id, None, None, "baseline_set", None, f"{found['n']} tasks, {float(found['hours']):g} h", source)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return plan_summary(project_id)


# ---------------------------------------------------------------- what the Plan view shows

def plan_summary(project_id: str) -> dict:
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT baseline_set_at FROM project_hours_settings WHERE project_id = %s", (project_id,))
        s = cur.fetchone()
        baseline_at = s["baseline_set_at"] if s else None
        cur.execute("""
            SELECT
              COUNT(*) FILTER (WHERE is_active AND (estimate_hours IS NOT NULL OR plan_start_week IS NOT NULL)) AS planned_tasks,
              COALESCE(SUM(estimate_hours) FILTER (WHERE is_active), 0) AS current_hours,
              COALESCE(SUM(baseline_estimate_hours) FILTER (WHERE is_active OR removed_at IS NOT NULL), 0) AS baseline_hours,
              COUNT(*) FILTER (WHERE is_active AND %(at)s IS NOT NULL AND created_at > %(at)s) AS added,
              COUNT(*) FILTER (WHERE removed_at IS NOT NULL AND %(at)s IS NOT NULL AND baseline_estimate_hours IS NOT NULL) AS removed
            FROM tasks WHERE project_id = %(p)s
        """, {"p": project_id, "at": baseline_at})
        r = cur.fetchone()
    conn.close()
    baseline_h, current_h = float(r["baseline_hours"]), float(r["current_hours"])
    return {
        "has_plan": bool(r["planned_tasks"]) or baseline_at is not None,
        "baseline_set_at": baseline_at.isoformat() if baseline_at else None,
        "baseline_hours": round(baseline_h, 2) if baseline_at else None,
        "current_hours": round(current_h, 2),
        "change_hours": round(current_h - baseline_h, 2) if baseline_at else None,
        "tasks_added": int(r["added"]), "tasks_removed": int(r["removed"]),
    }


def plan_has_content(project_id: str) -> bool:
    return plan_summary(project_id)["has_plan"]


def list_plan_changes(project_id: str, limit: int = 100) -> list:
    """Newest first. Tasks created after the baseline by something other than the plan (a document run) have no logged row, so they are
    listed from the tasks themselves."""
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT task_id, task_text, field, old_value, new_value, source, changed_at FROM plan_changes "
                    "WHERE project_id = %s ORDER BY changed_at DESC LIMIT %s", (project_id, limit))
        rows = [dict(r) for r in cur.fetchall()]
        cur.execute("""
            SELECT t.task_id, t.task_text, t.created_at FROM tasks t
            JOIN project_hours_settings s ON s.project_id = t.project_id AND s.baseline_set_at IS NOT NULL
            WHERE t.project_id = %s AND t.is_active AND t.created_at > s.baseline_set_at
              AND NOT EXISTS (SELECT 1 FROM plan_changes c WHERE c.task_id = t.task_id AND c.field = 'task_added')
        """, (project_id,))
        rows += [{"task_id": str(r["task_id"]), "task_text": r["task_text"], "field": "task_added", "old_value": None, "new_value": None,
                  "source": "document", "changed_at": r["created_at"]} for r in cur.fetchall()]
    conn.close()
    rows.sort(key=lambda r: r["changed_at"], reverse=True)
    for r in rows:
        r["task_id"] = str(r["task_id"]) if r["task_id"] else None
        r["label"] = HISTORY_FIELD_LABELS.get(r["field"], r["field"])
    return rows[:limit]


# ---------------------------------------------------------------- add and remove tasks

def create_plan_task(project_id: str, task_text: str, owner: str = None, phase: str = None, estimate_hours=None,
                     plan_start_week=None, plan_finish_week=None) -> dict:
    from apps.db.hours_db import set_task_hours
    text = " ".join((task_text or "").split())
    if not text:
        raise ValueError("A task needs a name.")
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM tasks WHERE project_id = %s AND is_active AND lower(task_text) = lower(%s)", (project_id, text))
        if cur.fetchone():
            raise ValueError("A task with this name already exists in the project.")
    conn.close()
    owner = (owner or "").strip() or None
    task_id = str(insert_task({"task_text": text, "normalized_task_text": text.lower(), "owner": owner, "status": "not_started"}, project_id))
    if owner:
        ensure_person_placeholder(project_id, owner)  # same as the pipeline: the roster builds itself from task owners
    try:
        set_task_hours(task_id, _log=False, estimate_hours=estimate_hours, plan_start_week=plan_start_week,
                       plan_finish_week=plan_finish_week, phase=phase)
    except ValueError:
        conn = get_connection()
        with conn.cursor() as cur:  # an invalid number must not leave a half-made task behind
            cur.execute("DELETE FROM tasks WHERE task_id = %s", (task_id,))
        conn.commit()
        conn.close()
        raise
    conn = get_connection()
    with conn.cursor() as cur:
        log_plan_change(cur, project_id, task_id, text, "task_added", None, estimate_hours)
    conn.commit()
    conn.close()
    return {"task_id": task_id, "task_text": text}


def remove_plan_task(task_id: str) -> dict:
    """Takes a task out of the plan (kept in the database, marked removed). Its logged hours stay in the project total as hours not on a
    task. Its dependency links go, so nothing shows 'unknown task'. A Jira issue it has is NOT touched."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT project_id, task_text, estimate_hours, jira_issue_key FROM tasks WHERE task_id = %s AND is_active", (task_id,))
            t = cur.fetchone()
            if not t:
                raise LookupError("No active task with that id.")
            cur.execute("SELECT COALESCE(SUM(hours), 0) AS h FROM time_entries WHERE task_id = %s", (task_id,))
            logged = float(cur.fetchone()["h"])
            cur.execute("UPDATE tasks SET is_active = FALSE, removed_at = NOW(), updated_at = NOW() WHERE task_id = %s", (task_id,))
            cur.execute("DELETE FROM task_dependencies WHERE task_id = %s OR depends_on_task_id = %s", (task_id, task_id))
            log_plan_change(cur, t["project_id"], task_id, t["task_text"], "task_removed", t["estimate_hours"], None)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"task_text": t["task_text"], "logged_hours": logged, "jira_issue_key": t["jira_issue_key"]}
