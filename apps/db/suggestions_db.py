"""Plan suggestions: what a meeting transcript proposes (a new task, or a change to an existing one) and where each stands.

  suggested  waiting for the PMO                 rejected   the PMO said no
  approved   the PMO said yes; not in the plan   waiting    approved, but the plan file is open in Excel: retried every scan
  blocked    approved, but the file cannot be changed safely (reason given)
  merged     written into the plan file          confirmed  the next read of the plan file shows it
  missing    was confirmed, then a later version of the plan no longer has it (can be re-applied)
  applied    approved on a project with no plan file, so it went straight into the app's task list"""
import json

from psycopg2.extras import Json, RealDictCursor

from apps.db.db import get_connection

OPEN_STATES = ("suggested", "approved", "waiting", "blocked", "merged")
STATES = ("suggested", "approved", "waiting", "blocked", "merged", "confirmed", "missing", "applied", "rejected")


def _row(r) -> dict:
    d = dict(r)
    for k in ("suggestion_id", "file_id", "task_id"):
        d[k] = str(d[k]) if d.get(k) else None
    for k in ("created_at", "decided_at", "merged_at", "confirmed_at"):
        d[k] = d[k].isoformat() if d.get(k) else None
    return d


def create_suggestion(project_id: str, kind: str, task_text: str, fields: dict = None, changes: dict = None, task_id: str = None,
                      file_id: str = None, source_name: str = None, confidence: str = None) -> dict:
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            INSERT INTO plan_suggestions (project_id, file_id, source_name, kind, task_id, task_text, fields, changes, confidence)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """, (project_id, file_id, source_name, kind, task_id, task_text, Json(fields or {}, dumps=lambda o: json.dumps(o, default=str)),
              Json(changes or {}, dumps=lambda o: json.dumps(o, default=str)), confidence))
        row = cur.fetchone()
    conn.commit()
    conn.close()
    return _row(row)


def get_suggestion(suggestion_id: str):
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM plan_suggestions WHERE suggestion_id = %s", (suggestion_id,))
        row = cur.fetchone()
    conn.close()
    return _row(row) if row else None


def list_suggestions(project_id: str, states=None, file_id: str = None, limit: int = 200) -> list:
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT * FROM plan_suggestions WHERE project_id = %s AND (%s::text[] IS NULL OR state = ANY(%s))
                       AND (%s::uuid IS NULL OR file_id = %s) ORDER BY created_at DESC, task_text LIMIT %s""",
                    (project_id, list(states) if states else None, list(states) if states else None, file_id, file_id, limit))
        rows = [_row(r) for r in cur.fetchall()]
    conn.close()
    return rows


def set_state(suggestion_id: str, state: str, reason: str = None, **stamps) -> dict:
    """Move a suggestion to a state. stamps: decided_at / merged_at / confirmed_at = True (now), plan_ref / jira_key / task_id = value."""
    assert state in STATES, state
    sets, vals = ["state = %s", "reason = %s"], [state, reason]
    for col in ("decided_at", "merged_at", "confirmed_at"):
        if stamps.get(col):
            sets.append(f"{col} = NOW()")
    for col in ("plan_ref", "jira_key", "task_id"):
        if stamps.get(col) is not None:
            sets.append(f"{col} = %s")
            vals.append(stamps[col])
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"UPDATE plan_suggestions SET {', '.join(sets)} WHERE suggestion_id = %s RETURNING *", (*vals, suggestion_id))
        row = cur.fetchone()
    conn.commit()
    conn.close()
    return _row(row) if row else None


def project_ids_with_work() -> list:
    """Projects with approved suggestions that still have to reach the plan."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT project_id FROM plan_suggestions WHERE state IN ('approved', 'waiting', 'merged')")
        ids = [r[0] for r in cur.fetchall()]
    conn.close()
    return ids


def counts(project_id: str) -> dict:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT state, COUNT(*) FROM plan_suggestions WHERE project_id = %s GROUP BY state", (project_id,))
        out = {s: n for s, n in cur.fetchall()}
    conn.close()
    return out
