import json
import os
import re
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _get_conn():
    # Open a psycopg2 connection using env-configured credentials.
    # pgvector registration is attempted but swallowed if the extension is not
    # present - tools that don't use vector search still work in that case.
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "task_planner"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "postgres"),
    )
    try:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)
    except Exception:
        pass
    return conn


# ---------------------------------------------------------------------------
# Internal helpers (not registered as MCP tools)
# ---------------------------------------------------------------------------

def _fetch_tasks_filtered(
    filter_owner: Optional[str] = None,
    filter_priority: Optional[str] = None,
    filter_status: Optional[str] = None,
    project_id: Optional[str] = None,
) -> list:
    # Build a dynamic WHERE clause from whichever filters were provided.
    # Returns a list of task dicts for use by other MCP tools (e.g. generate_report).
    conditions = ["is_active = TRUE"]
    params = []

    if filter_owner:
        conditions.append("owner ILIKE %s")
        params.append(f"%{filter_owner}%")
    if filter_priority:
        conditions.append("LOWER(priority) = LOWER(%s)")
        params.append(filter_priority)
    if filter_status:
        conditions.append("LOWER(status) = LOWER(%s)")
        params.append(filter_status)
    if project_id:
        conditions.append("project_id = %s")
        params.append(project_id)

    sql = f"""
        SELECT task_id, task_text, owner, status, deadline, priority, created_at, updated_at
        FROM tasks
        WHERE {' AND '.join(conditions)}
        ORDER BY
            CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
            updated_at DESC
    """
    conn = _get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# MCP tools - registered in server.py via mcp.tool()
# All functions return JSON strings as required by the MCP protocol.
# ---------------------------------------------------------------------------

def search_tasks(query: Optional[str] = None, owner: Optional[str] = None, status: Optional[str] = None, project_id: Optional[str] = None) -> str:
    """Search active tasks in Postgres. All filters are optional - omit to return recent tasks."""
    conditions = ["is_active = TRUE"]
    params = []

    # Build dynamic filters - each is optional and stacked with AND
    if query:
        conditions.append("(task_text ILIKE %s OR normalized_task_text ILIKE %s)")
        params.extend([f"%{query}%", f"%{query}%"])
    if owner:
        conditions.append("owner ILIKE %s")
        params.append(f"%{owner}%")
    if status == "blocked":
        # Matches the app's real definition of "blocked" everywhere else (the
        # dashboard's stat card, get_project_overview's blocked_tasks count) - a
        # task with blocker text set but a status that hasn't literally been flipped
        # to 'blocked' yet is still blocked. A plain status = 'blocked' equality here
        # would silently miss those and contradict what the UI shows for the same project.
        conditions.append("(status = 'blocked' OR blocker IS NOT NULL)")
    elif status:
        conditions.append("status = %s")
        params.append(status)
    if project_id:
        conditions.append("project_id = %s")
        params.append(project_id)

    where = " AND ".join(conditions)
    sql = f"""
        SELECT task_id, task_text, owner, status, deadline, priority, jira_issue_key, created_at, updated_at
        FROM tasks
        WHERE {where}
        ORDER BY updated_at DESC
        LIMIT 20
    """

    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return json.dumps(rows, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_task_by_id(task_id: str) -> str:
    """Fetch a single task by its UUID, including all fields."""
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT task_id, task_text, normalized_task_text, owner, status,
                       deadline, blocker, priority, jira_issue_key, is_active, created_at, updated_at
                FROM tasks
                WHERE task_id = %s
            """, (task_id,))
            row = cur.fetchone()
        conn.close()

        if row:
            return json.dumps(dict(row), default=str)
        return json.dumps({"error": f"No task found with id {task_id}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def create_task(task_text: str, owner: Optional[str] = None, deadline: Optional[str] = None, priority: str = "medium", project_id: Optional[str] = None) -> str:
    """Insert a new task into Postgres. Use only for tasks the pipeline missed - pipeline handles bulk creation."""
    allowed_priorities = {"high", "medium", "low"}
    if priority not in allowed_priorities:
        priority = "medium"

    normalized = re.sub(r"[^\w\s]", "", task_text.lower().strip())

    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tasks (task_text, normalized_task_text, owner, deadline, priority, status, project_id)
                VALUES (%s, %s, %s, %s, %s, 'not_started', %s)
                RETURNING task_id
            """, (task_text, normalized, owner or None, deadline or None, priority, project_id or None))
            task_id = str(cur.fetchone()[0])
        conn.commit()
        conn.close()
        return json.dumps({"task_id": task_id, "status": "created"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def list_projects() -> str:
    """List all distinct project IDs that have active tasks in Postgres."""
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT project_id
                FROM tasks
                WHERE is_active = TRUE AND project_id IS NOT NULL
                ORDER BY project_id
            """)
            projects = [row[0] for row in cur.fetchall()]
        conn.close()
        return json.dumps(projects)
    except Exception as e:
        return json.dumps({"error": str(e)})


def update_task_status(task_id: str, field: str, value: str) -> str:
    """Update a single field on an existing task. Allowed fields: status, priority, owner, deadline, blocker."""
    # Validate field name against an allowlist to prevent SQL injection
    allowed_fields = {"status", "priority", "owner", "deadline", "blocker"}
    if field not in allowed_fields:
        return json.dumps({
            "error": f"Field '{field}' not allowed. Choose from: {sorted(allowed_fields)}"
        })

    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            # Safe to interpolate field here - validated against allowlist above
            cur.execute(
                f"UPDATE tasks SET {field} = %s, updated_at = NOW() WHERE task_id = %s",
                (value, task_id)
            )
            updated = cur.rowcount
        conn.commit()
        conn.close()

        if updated == 0:
            return json.dumps({"error": f"No task found with id {task_id}"})
        return json.dumps({"task_id": task_id, "field": field, "value": value, "status": "updated"})
    except Exception as e:
        return json.dumps({"error": str(e)})
