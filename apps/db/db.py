import psycopg2
from psycopg2.extras import RealDictCursor
from pgvector.psycopg2 import register_vector
from apps.utils.embeddings import get_embedding
import os


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_connection():
    # Open a new psycopg2 connection using env-configured credentials.
    # register_vector() is required to enable pgvector type support for this connection.
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "task_planner"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "postgres"),
    )
    register_vector(conn)
    return conn


# ---------------------------------------------------------------------------
# Task writes
# ---------------------------------------------------------------------------

def insert_task(task, project_id: str = "default"):
    # Generate a 384-dim embedding before insert so the task is immediately
    # available for semantic similarity search in the match node
    embedding = get_embedding(task.get("task_text"))

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO tasks (
                task_text,
                normalized_task_text,
                owner,
                status,
                deadline,
                blocker,
                priority,
                task_embedding,
                project_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING task_id
    """, (
        task.get("task_text"),
        task.get("normalized_task_text"),
        task.get("owner"),
        task.get("status"),
        task.get("deadline"),
        task.get("blocker"),
        task.get("priority", "medium"),
        embedding,
        project_id,
    ))

    task_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return task_id


def ensure_source_documents_project_column():
    # Additive migration for a source_documents table created before the cache was
    # scoped per project - same idempotent pattern as ensure_jobs_table(). Without
    # this, the cache key is content_hash alone, so identical text submitted for two
    # different projects incorrectly shares one cached result. Existing rows get
    # project_id = NULL (their content_hash was already globally unique before this
    # migration, so no conflict) - they simply age out of the cache rather than being
    # guessed at, which is safe since a cache miss just means a fresh run.
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS project_id VARCHAR")
    cur.execute("ALTER TABLE source_documents DROP CONSTRAINT IF EXISTS source_documents_content_hash_key")
    # Postgres has no ADD CONSTRAINT ... IF NOT EXISTS, so check pg_constraint directly
    # to keep this idempotent across repeated calls (e.g. every --reload restart).
    cur.execute("SELECT 1 FROM pg_constraint WHERE conname = 'source_documents_content_hash_project_id_key'")
    if not cur.fetchone():
        cur.execute("""
            ALTER TABLE source_documents ADD CONSTRAINT source_documents_content_hash_project_id_key
            UNIQUE (content_hash, project_id)
        """)
    conn.commit()
    cur.close()
    conn.close()


def get_cached_pipeline_result(content_hash: str, project_id: str):
    # Return the cached formatted pipeline result for this document hash WITHIN this
    # project, or None if not cached - scoped so identical text in a different project
    # never returns another project's result.
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT pipeline_result FROM source_documents WHERE content_hash = %s AND project_id = %s",
            (content_hash, project_id)
        )
        row = cur.fetchone()
    conn.close()
    if row and row["pipeline_result"]:
        return row["pipeline_result"]
    return None


def cache_pipeline_result(content: str, result: dict, project_id: str):
    import hashlib
    import json as _json
    # Upsert: create the source_documents row if it doesn't exist yet (e.g. junk input where
    # store_events was skipped), then write the formatted result into pipeline_result.
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO source_documents (content, content_hash, project_id, pipeline_result)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (content_hash, project_id) DO UPDATE SET pipeline_result = EXCLUDED.pipeline_result
    """, (content, content_hash, project_id, _json.dumps(result)))
    conn.commit()
    cur.close()
    conn.close()


def upsert_source_document(content: str, document_type: str = None, project_id: str = None) -> str:
    import hashlib
    # SHA256 of the full content - combined with project_id as the unique key so the
    # same document submitted twice IN THE SAME PROJECT maps to one row, but identical
    # text submitted to a different project gets its own row instead of colliding.
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO source_documents (content, content_hash, project_id, document_type)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (content_hash, project_id) DO NOTHING
        RETURNING doc_id
    """, (content, content_hash, project_id, document_type))
    row = cur.fetchone()
    if row:
        doc_id = row[0]
    else:
        # Document already existed in this project - fetch the existing doc_id
        cur.execute(
            "SELECT doc_id FROM source_documents WHERE content_hash = %s AND project_id = %s",
            (content_hash, project_id)
        )
        doc_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return str(doc_id)


def insert_event(event):
    # Append an immutable event record to task_events.
    # Every matched event (new task, update, blocker, etc.) is logged here for audit history.
    # source_doc_id references source_documents - the full input text is stored once there.
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO task_events (
            task_id, event_type, task_text, owner, status,
            deadline, blocker, dependency, match_method,
            match_confidence, source_doc_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        event.get("matched_task_id"),
        event.get("event_type"),
        event.get("task_text"),
        event.get("owner"),
        event.get("status"),
        event.get("deadline"),
        event.get("blocker"),
        event.get("dependency"),
        event.get("match_method"),
        event.get("match_confidence"),
        event.get("source_doc_id"),
    ))

    conn.commit()
    cur.close()
    conn.close()


def update_task_field(task_id, field_name, field_value):
    # Update a single field on an existing task row.
    # field_name is validated against an allowlist to prevent SQL injection
    # before being interpolated into the query string.
    allowed_fields = {"status", "deadline", "owner", "blocker", "priority"}

    if field_name not in allowed_fields:
        raise ValueError(f"Invalid field name: {field_name}")

    conn = get_connection()
    cur = conn.cursor()

    # Safe to interpolate field_name here - it has been validated against the allowlist above
    query = f"""
        UPDATE tasks
        SET {field_name} = %s,
            updated_at = NOW()
        WHERE task_id = %s
    """

    cur.execute(query, (field_value, task_id))
    conn.commit()
    cur.close()
    conn.close()


def log_unresolved_event(event: dict, source_input: str = ""):
    # Write a failed-match update event to the unresolved_events review queue.
    # Called when a non-new_task event could not be matched to any existing DB task
    # after all 4 match stages (exact, partial, fuzzy, semantic).
    # No task is created - these events require human review before any action is taken.
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO unresolved_events (
            event_type, task_text, owner, status,
            deadline, blocker, dependency, reason, source_input
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        event.get("event_type"),
        event.get("task_text"),
        event.get("owner"),
        event.get("status"),
        event.get("deadline"),
        event.get("blocker"),
        event.get("dependency"),
        "no_match_found",
        source_input,
    ))

    conn.commit()
    cur.close()
    conn.close()


# ---------------------------------------------------------------------------
# Job store (agent_jobs table)
# ---------------------------------------------------------------------------

def ensure_jobs_table():
    # Create agent_jobs if it doesn't exist yet.
    # Called once at API startup so no manual migration is needed.
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_jobs (
            job_id UUID PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'running',
            project_id VARCHAR,
            result JSONB,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    # Additive migration for a table created before project_id existed.
    cur.execute("ALTER TABLE agent_jobs ADD COLUMN IF NOT EXISTS project_id VARCHAR")
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_agent_jobs_created_at ON agent_jobs(created_at)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_agent_jobs_project_id ON agent_jobs(project_id)
    """)
    conn.commit()
    cur.close()
    conn.close()


def create_job(job_id: str, project_id: str = None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO agent_jobs (job_id, status, project_id) VALUES (%s, 'running', %s)",
        (job_id, project_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def complete_job(job_id: str, result: dict):
    import json as _json
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE agent_jobs SET status = 'completed', result = %s WHERE job_id = %s",
        (_json.dumps(result), job_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def fail_job(job_id: str, error: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE agent_jobs SET status = 'failed', error = %s WHERE job_id = %s",
        (error, job_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def fetch_recent_agent_activity(project_id: str = None, limit: int = 20):
    # Flattens each recent completed job's planning/sync_reporting/meeting_coordinator
    # traces into individual activity events - powers the dashboard's activity feed.
    # Pulled from agent_jobs.result (already persisted per run), not a new table.
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if project_id:
            cur.execute("""
                SELECT job_id, project_id, result, created_at FROM agent_jobs
                WHERE status = 'completed' AND project_id = %s
                ORDER BY created_at DESC LIMIT 30
            """, (project_id,))
        else:
            cur.execute("""
                SELECT job_id, project_id, result, created_at FROM agent_jobs
                WHERE status = 'completed'
                ORDER BY created_at DESC LIMIT 30
            """)
        rows = cur.fetchall()
    conn.close()

    events = []
    agent_traces = [
        ("planning", "planning_trace"),
        ("sync_reporting", "sync_reporting_trace"),
        ("meeting_coordinator", "meeting_coordinator_trace"),
    ]
    for row in rows:
        result = row.get("result") or {}
        for agent_name, trace_key in agent_traces:
            for entry in result.get(trace_key, []):
                events.append({
                    "job_id": str(row["job_id"]),
                    "project_id": row["project_id"],
                    "agent": agent_name,
                    "tool": entry.get("tool"),
                    "result": entry.get("result"),
                    "created_at": row["created_at"],
                })

    events.sort(key=lambda e: e["created_at"], reverse=True)
    return events[:limit]


def get_job(job_id: str):
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT job_id, status, result, error FROM agent_jobs WHERE job_id = %s",
            (job_id,),
        )
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def cleanup_old_jobs(max_age_hours: int = 24):
    # Delete completed/failed jobs older than max_age_hours to prevent unbounded table growth.
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM agent_jobs WHERE created_at < NOW() - INTERVAL '%s hours'",
        (max_age_hours,),
    )
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return deleted


# ---------------------------------------------------------------------------
# Task reads
# ---------------------------------------------------------------------------

def fetch_task_by_normalized_text(normalized_text: str, project_id: str = "default"):
    # Check if a task with this normalized text already exists in the DB for this project.
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT task_id
            FROM tasks
            WHERE normalized_task_text = %s
              AND project_id = %s
              AND is_active = TRUE
            LIMIT 1
        """, (normalized_text, project_id))
        row = cur.fetchone()
    conn.close()
    return row["task_id"] if row else None


def fetch_active_tasks(project_id: str = "default"):
    # Fetch active tasks scoped to this project for the match node.
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                task_id,
                task_text,
                normalized_task_text,
                owner,
                status,
                deadline,
                blocker,
                task_embedding,
                created_at,
                updated_at
            FROM tasks
            WHERE is_active = TRUE
              AND project_id = %s
            ORDER BY updated_at DESC, created_at DESC
        """, (project_id,))
        rows = cur.fetchall()

    conn.close()
    return [dict(row) for row in rows]


def fetch_semantic_task_match(task_text: str, project_id: str = "default"):
    # Find the closest active task within this project using pgvector cosine similarity.
    if not task_text:
        return None

    embedding = get_embedding(task_text)
    if not embedding:
        return None

    conn = get_connection()

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                task_id,
                task_text,
                normalized_task_text,
                owner,
                status,
                deadline,
                blocker,
                task_embedding,
                1 - (task_embedding <=> %s::vector) AS similarity_score
            FROM tasks
            WHERE is_active = TRUE
              AND project_id = %s
              AND task_embedding IS NOT NULL
            ORDER BY task_embedding <=> %s::vector
            LIMIT 1
        """, (embedding, project_id, embedding))

        row = cur.fetchone()

    conn.close()

    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Task dependencies (task_dependencies table)
# ---------------------------------------------------------------------------

def add_task_dependency(task_id: str, depends_on_task_id: str):
    # Record that task_id depends on depends_on_task_id. Called by the Planning
    # agent when it identifies a genuine dependency between two tasks it can see,
    # and by a human editing dependencies manually from the task tracker.
    if task_id == depends_on_task_id:
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO task_dependencies (task_id, depends_on_task_id)
        VALUES (%s, %s)
        ON CONFLICT (task_id, depends_on_task_id) DO NOTHING
    """, (task_id, depends_on_task_id))
    conn.commit()
    cur.close()
    conn.close()


def remove_task_dependency(task_id: str, depends_on_task_id: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM task_dependencies WHERE task_id = %s AND depends_on_task_id = %s
    """, (task_id, depends_on_task_id))
    conn.commit()
    cur.close()
    conn.close()


def fetch_dependencies(task_id: str):
    # Tasks that task_id depends on (must finish first).
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT t.task_id, t.task_text, t.status
            FROM task_dependencies d
            JOIN tasks t ON t.task_id = d.depends_on_task_id
            WHERE d.task_id = %s
        """, (task_id,))
        rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def fetch_blocking(task_id: str):
    # Tasks that depend on task_id (this task blocks them).
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT t.task_id, t.task_text, t.status
            FROM task_dependencies d
            JOIN tasks t ON t.task_id = d.task_id
            WHERE d.depends_on_task_id = %s
        """, (task_id,))
        rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def fetch_dependencies_for_project(project_id: str):
    # All dependency edges for a project in one query - used by the dashboard
    # to join dependency info onto the task board without an N+1 query per row.
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT d.task_id, d.depends_on_task_id
            FROM task_dependencies d
            JOIN tasks t ON t.task_id = d.task_id
            WHERE t.project_id = %s
        """, (project_id,))
        rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Project people (project_people table)
# ---------------------------------------------------------------------------

def upsert_project_person(project_id: str, name: str, email: str, role: str = None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO project_people (project_id, name, email, role)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (project_id, name) DO UPDATE SET email = EXCLUDED.email, role = EXCLUDED.role
    """, (project_id, name, email, role))
    conn.commit()
    cur.close()
    conn.close()


def delete_project_person(project_id: str, name: str) -> bool:
    # Case-insensitive to match resolve_person_email's own lookup semantics.
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM project_people WHERE project_id = %s AND lower(name) = lower(%s)",
        (project_id, name),
    )
    deleted = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return deleted


def resolve_person_email(project_id: str, name: str):
    # Case-insensitive lookup - names extracted by the LLM won't always match seeded casing exactly.
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT name, email, role FROM project_people
            WHERE project_id = %s AND lower(name) = lower(%s)
        """, (project_id, name))
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_project_people(project_id: str):
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT name, email, role FROM project_people WHERE project_id = %s ORDER BY name
        """, (project_id,))
        rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Project integrations (project_integrations table) - per-project external-system
# mapping. Auth is one global connection (env vars); WHICH Jira project a given
# task-manager project syncs into is per-project, stored here.
# ---------------------------------------------------------------------------

def get_project_jira_key(project_id: str):
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT jira_project_key FROM project_integrations WHERE project_id = %s",
            (project_id,),
        )
        row = cur.fetchone()
    conn.close()
    return row["jira_project_key"] if row else None


def set_project_jira_key(project_id: str, jira_project_key: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO project_integrations (project_id, jira_project_key)
        VALUES (%s, %s)
        ON CONFLICT (project_id) DO UPDATE SET jira_project_key = EXCLUDED.jira_project_key, updated_at = NOW()
    """, (project_id, jira_project_key))
    conn.commit()
    cur.close()
    conn.close()


def ensure_tasks_jira_column():
    # Additive migration for a tasks table created before jira_issue_key existed -
    # same idempotent ALTER pattern ensure_jobs_table() uses for agent_jobs.project_id.
    # Without this, sync_task_to_jira has no memory of a prior successful sync and
    # will happily create a second Jira issue for the same task on a later run.
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS jira_issue_key TEXT")
    conn.commit()
    cur.close()
    conn.close()


def get_task_jira_key(task_id: str):
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT jira_issue_key FROM tasks WHERE task_id = %s", (task_id,))
        row = cur.fetchone()
    conn.close()
    return row["jira_issue_key"] if row else None


def set_task_jira_key(task_id: str, jira_issue_key: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET jira_issue_key = %s WHERE task_id = %s", (jira_issue_key, task_id))
    conn.commit()
    cur.close()
    conn.close()


# ---------------------------------------------------------------------------
# Meetings - Meeting Coordinator only ever PROPOSES a meeting (see
# mcp_server/tools/calendar.py's propose_meeting); it never calls Google Calendar
# directly. A row here is the durable record of that proposal from the moment it's
# made through approval/rejection, so a person can review and act on it from the UI
# instead of it only existing transiently inside one job's trace.
# ---------------------------------------------------------------------------

def ensure_meetings_table():
    # Created idempotently at API startup, same pattern as ensure_jobs_table().
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            meeting_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id VARCHAR NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            attendee_emails JSONB NOT NULL DEFAULT '[]',
            unresolved_attendees JSONB NOT NULL DEFAULT '[]',
            start_iso TEXT NOT NULL,
            end_iso TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_approval',
            reason TEXT,
            google_event_id TEXT,
            meeting_link TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_meetings_project_id ON meetings(project_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status)")
    conn.commit()
    cur.close()
    conn.close()


def create_pending_meeting(
    project_id: str,
    title: str,
    description: str,
    attendee_emails: list,
    unresolved_attendees: list,
    start_iso: str,
    end_iso: str,
    reason: str,
):
    import json as _json
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            INSERT INTO meetings
                (project_id, title, description, attendee_emails, unresolved_attendees, start_iso, end_iso, reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING meeting_id
        """, (
            project_id, title, description, _json.dumps(attendee_emails),
            _json.dumps(unresolved_attendees), start_iso, end_iso, reason,
        ))
        meeting_id = cur.fetchone()["meeting_id"]
    conn.commit()
    conn.close()
    return str(meeting_id)


def list_meetings(project_id: str = None, status: str = None):
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        clauses, params = [], []
        if project_id:
            clauses.append("project_id = %s")
            params.append(project_id)
        if status:
            clauses.append("status = %s")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cur.execute(f"""
            SELECT * FROM meetings {where} ORDER BY created_at DESC
        """, params)
        rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_meeting(meeting_id: str):
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM meetings WHERE meeting_id = %s", (meeting_id,))
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def approve_meeting_record(meeting_id: str, google_event_id: str, meeting_link: str, title: str, attendee_emails: list, start_iso: str, end_iso: str):
    import json as _json
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            UPDATE meetings
            SET status = 'scheduled', google_event_id = %s, meeting_link = %s,
                title = %s, attendee_emails = %s, start_iso = %s, end_iso = %s, updated_at = NOW()
            WHERE meeting_id = %s
            RETURNING *
        """, (google_event_id, meeting_link, title, _json.dumps(attendee_emails), start_iso, end_iso, meeting_id))
        row = cur.fetchone()
    conn.commit()
    conn.close()
    return dict(row) if row else None


def reject_meeting_record(meeting_id: str):
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            UPDATE meetings SET status = 'rejected', updated_at = NOW()
            WHERE meeting_id = %s
            RETURNING *
        """, (meeting_id,))
        row = cur.fetchone()
    conn.commit()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Project-level reads (Supervisor routing + dashboard)
# ---------------------------------------------------------------------------

def count_tasks_for_project(project_id: str) -> int:
    # Used by the Supervisor to decide SOW-path (no tasks yet -> create) vs
    # MoM-path (tasks already exist -> update) for an incoming document.
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM tasks WHERE project_id = %s AND is_active = TRUE", (project_id,))
        count = cur.fetchone()[0]
    conn.close()
    return count


def fetch_projects():
    # One row per project_id with a rollup - powers the dashboard's metrics and
    # project selector. Status values are exactly: not_started, in_progress,
    # completed, blocked, on_hold (see prompts/normalize's allowed values) - 'done'
    # is not a real status and never matched, which previously left completed_tasks
    # always at 0 and open_tasks over-counting finished work as still open.
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                project_id,
                COUNT(*) AS total_tasks,
                COUNT(*) FILTER (WHERE status = 'blocked' OR blocker IS NOT NULL) AS blocked_tasks,
                COUNT(*) FILTER (WHERE status = 'completed') AS completed_tasks,
                COUNT(*) FILTER (WHERE status = 'in_progress') AS in_progress_tasks,
                COUNT(*) FILTER (WHERE status NOT IN ('completed')) AS open_tasks,
                COUNT(*) FILTER (WHERE priority = 'high') AS high_priority_tasks,
                COUNT(DISTINCT owner) FILTER (WHERE owner IS NOT NULL) AS unique_owners,
                MAX(updated_at) AS last_updated
            FROM tasks
            WHERE is_active = TRUE AND project_id IS NOT NULL
            GROUP BY project_id
            ORDER BY last_updated DESC
        """)
        rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def fetch_project_tasks_with_dependencies(project_id: str):
    # Task board rows joined with dependency info in one round trip - the dashboard
    # shows owner/status/priority/deadline/dependency/blocker per row without a click-through.
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT task_id, task_text, owner, status, deadline, blocker, priority, updated_at
            FROM tasks
            WHERE project_id = %s AND is_active = TRUE
            ORDER BY
                CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                updated_at DESC
        """, (project_id,))
        tasks = [dict(row) for row in cur.fetchall()]

    deps = fetch_dependencies_for_project(project_id)
    depends_on_map = {}
    blocks_map = {}
    for edge in deps:
        depends_on_map.setdefault(edge["task_id"], []).append(edge["depends_on_task_id"])
        blocks_map.setdefault(edge["depends_on_task_id"], []).append(edge["task_id"])

    task_text_by_id = {t["task_id"]: t["task_text"] for t in tasks}
    for t in tasks:
        t["depends_on"] = [
            {"task_id": tid, "task_text": task_text_by_id.get(tid)}
            for tid in depends_on_map.get(t["task_id"], [])
        ]
        t["blocks"] = [
            {"task_id": tid, "task_text": task_text_by_id.get(tid)}
            for tid in blocks_map.get(t["task_id"], [])
        ]

    conn.close()
    return tasks


def fetch_owner_workload(project_id: str = None):
    # Per-owner task counts, used by the Chat agent to answer "who's overloaded"
    # style questions - one round trip rather than the agent inferring this itself
    # from a raw task list.
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""
            SELECT
                owner,
                COUNT(*) AS total_tasks,
                COUNT(*) FILTER (WHERE status NOT IN ('completed')) AS open_tasks,
                COUNT(*) FILTER (WHERE status = 'blocked' OR blocker IS NOT NULL) AS blocked_tasks,
                COUNT(*) FILTER (WHERE priority = 'high') AS high_priority_tasks
            FROM tasks
            WHERE is_active = TRUE AND owner IS NOT NULL
                {"AND project_id = %s" if project_id else ""}
            GROUP BY owner
            ORDER BY open_tasks DESC
        """, (project_id,) if project_id else ())
        rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def insert_chat_message(session_id: str, role: str, content: str):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO chat_messages (session_id, role, content) VALUES (%s, %s, %s)",
            (session_id, role, content),
        )
    conn.commit()
    conn.close()


def fetch_chat_history(session_id: str, limit: int = 20):
    # Oldest-first, capped to the most recent `limit` turns - matches what gets fed
    # back into the chat agent's context window each turn.
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT role, content, created_at FROM (
                SELECT role, content, created_at
                FROM chat_messages
                WHERE session_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            ) recent
            ORDER BY created_at ASC
        """, (session_id, limit))
        rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_project(project_id: str) -> int:
    """Permanently deletes a project: its tasks (task_dependencies cascade automatically
    via their own FK), project_integrations, and agent_jobs. One transaction, so a
    failure partway through leaves nothing deleted rather than a partially-removed
    project. Returns how many tasks were deleted, so the caller can tell a real project
    from a no-op on an id that never existed.

    project_people is deliberately NOT cleared here - like the content-hash cache in
    source_documents, a person's name-to-email mapping isn't really "this project's
    data" so much as reusable context that happens to be scoped by project_id; wiping
    it on every delete meant re-adding it by hand after every delete-and-rerun cycle,
    which defeats the point of a quick demo reset. Same tradeoff as the cache: this can
    leave orphaned rows behind for a project id that's gone for good, which is fine for
    now and worth revisiting alongside the cache scoping fix later."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE project_id = %s", (project_id,))
            deleted_tasks = cur.rowcount
            cur.execute("DELETE FROM project_integrations WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM agent_jobs WHERE project_id = %s", (project_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return deleted_tasks
