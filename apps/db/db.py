import psycopg2
from psycopg2.extras import RealDictCursor
from pgvector.psycopg2 import register_vector
from apps.utils.embeddings import get_embedding, get_embeddings_batch
from apps.utils.chunking import chunk_document
from apps.utils.deliverables import number_sort_key
from apps.utils.hours import task_remaining
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
    is_new_document = row is not None
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

    # Chunk + embed a genuinely new document - never redundantly on a resubmission of identical
    # text, which would otherwise duplicate every chunk on each rerun. A document stored before
    # search existed (no chunks at all) is chunked now, so resubmitting it heals it.
    if is_new_document or not _has_document_chunks(str(doc_id)):
        _create_document_chunks(str(doc_id), project_id, content)

    return str(doc_id)


# ---------------------------------------------------------------------------
# Document chunks (document_chunks table) - semantic search over the raw SOW/MoM
# text for the Chat agent's search_documents tool. Separate from tasks.task_embedding,
# which is scoped to task-dedup matching during extraction, not document Q&A.
# ---------------------------------------------------------------------------

def ensure_document_chunks_table():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            doc_id UUID NOT NULL REFERENCES source_documents(doc_id) ON DELETE CASCADE,
            project_id VARCHAR NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            chunk_embedding vector(384),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (doc_id, chunk_index)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_document_chunks_project_id ON document_chunks(project_id)")
    # Same HNSW-over-IVFFlat reasoning as idx_tasks_embedding_hnsw: this table starts
    # empty and grows incrementally as documents are submitted, which is exactly the
    # case IVFFlat handles badly (needs existing data to train on, needs periodic
    # rebuilding as data grows) and HNSW handles cleanly (correct from row zero).
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw ON document_chunks
        USING hnsw (chunk_embedding vector_cosine_ops)
    """)
    conn.commit()
    cur.close()
    conn.close()


def _has_document_chunks(doc_id: str) -> bool:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM document_chunks WHERE doc_id = %s LIMIT 1", (doc_id,))
        found = cur.fetchone() is not None
    conn.close()
    return found


def backfill_missing_document_chunks() -> int:
    """Chunk + embed every stored source document that has no chunks (stored before search existed, or whose chunking failed), so
    Chat's search_documents can find what they say. Returns how many documents were filled."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT d.doc_id, d.project_id, d.content FROM source_documents d
            WHERE d.project_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM document_chunks c WHERE c.doc_id = d.doc_id)
        """)
        missing = cur.fetchall()
    conn.close()
    for doc_id, project_id, content in missing:
        _create_document_chunks(str(doc_id), project_id, content)
    return len(missing)


def _create_document_chunks(doc_id: str, project_id: str, content: str):
    chunks = chunk_document(content)
    if not chunks:
        return

    embeddings = get_embeddings_batch(chunks)

    conn = get_connection()
    cur = conn.cursor()
    for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
        cur.execute("""
            INSERT INTO document_chunks (doc_id, project_id, chunk_index, chunk_text, chunk_embedding)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (doc_id, chunk_index) DO NOTHING
        """, (doc_id, project_id, i, chunk_text, embedding))
    conn.commit()
    cur.close()
    conn.close()


def search_document_chunks(project_id: str, query: str, limit: int = 5):
    embedding = get_embedding(query)
    if not embedding:
        return []

    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                dc.chunk_text,
                sd.document_type,
                sd.created_at AS document_submitted_at,
                1 - (dc.chunk_embedding <=> %s::vector) AS similarity_score
            FROM document_chunks dc
            JOIN source_documents sd ON sd.doc_id = dc.doc_id
            WHERE dc.project_id = %s AND dc.chunk_embedding IS NOT NULL
            ORDER BY dc.chunk_embedding <=> %s::vector
            LIMIT %s
        """, (embedding, project_id, embedding, limit))
        rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


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
# Possible duplicates - the match gate's "unsure" / high-similarity rejections leave a
# visible duplicate by design (a wrong merge silently loses a task, a duplicate doesn't);
# these let a person resolve each one: merge it into the existing task, or say it is a
# genuinely different task. Every resolution is also kept in duplicate_decisions - clean,
# human-confirmed same/different pairs, the labeled data a future fine-tune would need.
# ---------------------------------------------------------------------------

def ensure_duplicate_handling_schema():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS possible_duplicate_of UUID")
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS possible_duplicate_reason TEXT")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS duplicate_decisions (
            decision_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id TEXT,
            task_id UUID NOT NULL,
            other_task_id UUID NOT NULL,
            task_text TEXT,
            other_task_text TEXT,
            decision TEXT NOT NULL CHECK (decision IN ('merged', 'not_duplicate')),
            reason TEXT,
            decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def set_possible_duplicate(task_id: str, other_task_id: str, reason: str = None):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE tasks SET possible_duplicate_of = %s, possible_duplicate_reason = %s WHERE task_id = %s",
        (other_task_id, reason, task_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def dismiss_possible_duplicate(task_id: str) -> dict:
    """The person says this task is genuinely different from the one it was flagged against."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT t.task_id, t.project_id, t.task_text, t.possible_duplicate_of, t.possible_duplicate_reason,
                       o.task_text AS other_text
                FROM tasks t LEFT JOIN tasks o ON o.task_id = t.possible_duplicate_of
                WHERE t.task_id = %s AND t.is_active = TRUE FOR UPDATE OF t
            """, (task_id,))
            row = cur.fetchone()
            if not row:
                raise LookupError(f"No active task with id {task_id}")
            if not row["possible_duplicate_of"]:
                raise ValueError("Task is not flagged as a possible duplicate")
            cur.execute(
                "UPDATE tasks SET possible_duplicate_of = NULL, possible_duplicate_reason = NULL WHERE task_id = %s",
                (task_id,),
            )
            cur.execute("""
                INSERT INTO duplicate_decisions
                    (project_id, task_id, other_task_id, task_text, other_task_text, decision, reason)
                VALUES (%s, %s, %s, %s, %s, 'not_duplicate', %s)
            """, (row["project_id"], task_id, row["possible_duplicate_of"], row["task_text"],
                  row["other_text"], row["possible_duplicate_reason"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"task_id": task_id, "status": "kept_separate"}


def merge_tasks(source_task_id: str, target_task_id: str) -> dict:
    """Merge source into target (one transaction): target keeps its own fields and only gets
    blanks (owner, deadline, blocker, Jira key, hours estimate) filled from source; source's event history,
    logged hours and dependency edges move to target; source is deactivated (soft delete - nothing is lost).
    Raises LookupError (missing/inactive task) or ValueError (invalid merge). If both tasks
    already have a Jira issue the source's key is returned for the caller to deal with - no Jira
    call is made here."""
    if source_task_id == target_task_id:
        raise ValueError("A task cannot be merged into itself")
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT task_id, project_id, task_text, owner, deadline, blocker, jira_issue_key,
                       possible_duplicate_reason
                FROM tasks WHERE task_id IN (%s, %s) AND is_active = TRUE FOR UPDATE
            """, (source_task_id, target_task_id))
            rows = {str(r["task_id"]): r for r in cur.fetchall()}
            src, tgt = rows.get(str(source_task_id)), rows.get(str(target_task_id))
            if not src or not tgt:
                raise LookupError("Both tasks must exist and be active")
            if src["project_id"] != tgt["project_id"]:
                raise ValueError("Tasks belong to different projects")

            fills = {f: src[f] for f in ("owner", "deadline", "blocker") if tgt[f] is None and src[f] is not None}
            jira_to_close = None
            if src["jira_issue_key"]:
                if tgt["jira_issue_key"] is None:
                    fills["jira_issue_key"] = src["jira_issue_key"]
                else:
                    jira_to_close = src["jira_issue_key"]
            for field, value in fills.items():  # field names come from the fixed tuple above, not input
                cur.execute(f"UPDATE tasks SET {field} = %s WHERE task_id = %s", (value, target_task_id))

            cur.execute("UPDATE task_events SET task_id = %s WHERE task_id = %s", (target_task_id, source_task_id))
            from apps.db.hours_db import merge_task_hours  # lazy: hours_db imports this module
            merge_task_hours(cur, source_task_id, target_task_id)

            # Re-point dependency edges. Edges between the two tasks would become self-loops: drop them.
            cur.execute("""
                DELETE FROM task_dependencies
                WHERE (task_id = %s AND depends_on_task_id = %s) OR (task_id = %s AND depends_on_task_id = %s)
            """, (source_task_id, target_task_id, target_task_id, source_task_id))
            cur.execute("""
                INSERT INTO task_dependencies (task_id, depends_on_task_id)
                SELECT %s, depends_on_task_id FROM task_dependencies WHERE task_id = %s
                ON CONFLICT DO NOTHING
            """, (target_task_id, source_task_id))
            cur.execute("""
                INSERT INTO task_dependencies (task_id, depends_on_task_id)
                SELECT task_id, %s FROM task_dependencies WHERE depends_on_task_id = %s
                ON CONFLICT DO NOTHING
            """, (target_task_id, source_task_id))
            cur.execute(
                "DELETE FROM task_dependencies WHERE task_id = %s OR depends_on_task_id = %s",
                (source_task_id, source_task_id),
            )

            cur.execute(
                "UPDATE tasks SET possible_duplicate_of = NULL, possible_duplicate_reason = NULL "
                "WHERE possible_duplicate_of = %s OR task_id = %s",
                (source_task_id, target_task_id),
            )
            cur.execute("""
                UPDATE tasks SET is_active = FALSE, possible_duplicate_of = NULL,
                                 possible_duplicate_reason = NULL, updated_at = now()
                WHERE task_id = %s
            """, (source_task_id,))
            cur.execute("UPDATE tasks SET updated_at = now() WHERE task_id = %s", (target_task_id,))
            cur.execute("""
                INSERT INTO duplicate_decisions
                    (project_id, task_id, other_task_id, task_text, other_task_text, decision, reason)
                VALUES (%s, %s, %s, %s, %s, 'merged', %s)
            """, (src["project_id"], source_task_id, target_task_id, src["task_text"], tgt["task_text"],
                  src["possible_duplicate_reason"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "merged_task_id": source_task_id,
        "into_task_id": target_task_id,
        "filled_fields": {k: str(v) if v is not None else None for k, v in fills.items()},
        "source_jira_issue_key_not_transferred": jira_to_close,
    }


# ---------------------------------------------------------------------------
# Registers: RAID log (raid_items) and RACI matrix (task_raci). Manual-first: a PM can keep
# both by hand today; the AI will later PROPOSE items with review_state = 'suggested' (shown in
# a review queue) and a person confirms or rejects them - same confirm pattern as possible
# duplicates. See DECISIONS.md ("RAID log, RACI matrix and PMP").
# ---------------------------------------------------------------------------

RAID_TYPES = ("risk", "assumption", "issue", "dependency")
RAID_STATUSES = ("open", "mitigating", "closed")
RAID_LEVELS = ("low", "medium", "high")
RAID_REVIEW_STATES = ("suggested", "confirmed", "rejected")
RACI_ROLES = ("R", "A", "C", "I")
_RAID_EDITABLE = {
    "item_type", "title", "description", "owner", "status", "probability", "impact", "review_date", "review_state",
}


def ensure_registers_schema():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS raid_items (
            item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id TEXT NOT NULL,
            item_type TEXT NOT NULL CHECK (item_type IN ('risk', 'assumption', 'issue', 'dependency')),
            title TEXT NOT NULL,
            description TEXT,
            owner TEXT,
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'mitigating', 'closed')),
            probability TEXT CHECK (probability IN ('low', 'medium', 'high')),
            impact TEXT CHECK (impact IN ('low', 'medium', 'high')),
            review_date DATE,
            review_state TEXT NOT NULL DEFAULT 'confirmed' CHECK (review_state IN ('suggested', 'confirmed', 'rejected')),
            origin TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual', 'agent', 'document')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_raid_items_project ON raid_items (project_id)")
    # A risk that happened is promoted to a new Issue (promote_risk_to_issue) and this points at it.
    cur.execute("ALTER TABLE raid_items ADD COLUMN IF NOT EXISTS promoted_to UUID")
    # Items imported from a document's numbered list carry a stable reference so re-running the same document never
    # duplicates them; tables created before 'document' existed as an origin get their CHECK widened.
    cur.execute("ALTER TABLE raid_items ADD COLUMN IF NOT EXISTS source_ref TEXT")
    cur.execute("ALTER TABLE raid_items ADD COLUMN IF NOT EXISTS evidence TEXT")  # verbatim quote behind an AI suggestion
    # Which stored documents the "Suggest from documents" button has already read (so a second click only reads new ones).
    cur.execute("ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS raid_scanned_at TIMESTAMPTZ")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_raid_items_source_ref ON raid_items (project_id, source_ref) "
                "WHERE source_ref IS NOT NULL")
    cur.execute("""
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'raid_items'::regclass AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%%origin%%' AND pg_get_constraintdef(oid) NOT LIKE '%%document%%'
    """)
    for (old_origin_constraint,) in cur.fetchall():
        cur.execute(f'ALTER TABLE raid_items DROP CONSTRAINT "{old_origin_constraint}"')
        cur.execute("ALTER TABLE raid_items ADD CONSTRAINT raid_items_origin_check "
                    "CHECK (origin IN ('manual', 'agent', 'document'))")
    # The first version allowed a 'materialized' status; it was dropped in favour of promoting the risk to an
    # Issue. Tables created back then still carry the old CHECK - migrate any such rows and replace it.
    cur.execute("""
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'raid_items'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%%materialized%%'
    """)
    for (old_constraint,) in cur.fetchall():
        cur.execute("UPDATE raid_items SET status = 'closed' WHERE status = 'materialized'")
        cur.execute(f'ALTER TABLE raid_items DROP CONSTRAINT "{old_constraint}"')
        cur.execute("ALTER TABLE raid_items ADD CONSTRAINT raid_items_status_check "
                    "CHECK (status IN ('open', 'mitigating', 'closed'))")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS task_raci (
            task_id UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            person TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('R', 'A', 'C', 'I')),
            PRIMARY KEY (task_id, person, role)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS deliverables (
            deliverable_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id TEXT NOT NULL,
            number TEXT,
            title TEXT NOT NULL,
            owner TEXT,
            deadline TEXT,
            status TEXT NOT NULL DEFAULT 'not_started'
                CHECK (status IN ('not_started', 'in_progress', 'completed', 'blocked', 'on_hold')),
            origin TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual', 'task', 'document', 'agent')),
            review_state TEXT NOT NULL DEFAULT 'confirmed' CHECK (review_state IN ('suggested', 'confirmed', 'rejected')),
            evidence TEXT,
            source_task_id UUID REFERENCES tasks(task_id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # Columns/values added after the first version of this table: AI suggestions (review_state, evidence, origin 'agent').
    cur.execute("ALTER TABLE deliverables ADD COLUMN IF NOT EXISTS review_state TEXT NOT NULL DEFAULT 'confirmed' "
                "CHECK (review_state IN ('suggested', 'confirmed', 'rejected'))")
    cur.execute("ALTER TABLE deliverables ADD COLUMN IF NOT EXISTS evidence TEXT")
    cur.execute("""
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'deliverables'::regclass AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%%origin%%' AND pg_get_constraintdef(oid) NOT LIKE '%%agent%%'
    """)
    for (old_origin_constraint,) in cur.fetchall():
        cur.execute(f'ALTER TABLE deliverables DROP CONSTRAINT "{old_origin_constraint}"')
        cur.execute("ALTER TABLE deliverables ADD CONSTRAINT deliverables_origin_check "
                    "CHECK (origin IN ('manual', 'task', 'document', 'agent'))")
    # Which deliverable a task contributes to (nullable: most tasks are unassigned until someone decides).
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS deliverable_id UUID "
                "REFERENCES deliverables(deliverable_id) ON DELETE SET NULL")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_deliverables_project ON deliverables (project_id)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_deliverables_project_number "
                "ON deliverables (project_id, number) WHERE number IS NOT NULL")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_deliverables_source_task "
                "ON deliverables (source_task_id) WHERE source_task_id IS NOT NULL")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS deliverable_raci (
            deliverable_id UUID NOT NULL REFERENCES deliverables(deliverable_id) ON DELETE CASCADE,
            person TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('R', 'A', 'C', 'I')),
            PRIMARY KEY (deliverable_id, person, role)
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def list_raid_items(project_id: str, item_type: str = None, status: str = None, review_state: str = None):
    conditions, params = ["project_id = %s"], [project_id]
    for column, value in (("item_type", item_type), ("status", status), ("review_state", review_state)):
        if value:
            conditions.append(f"{column} = %s")
            params.append(value)
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""
            SELECT item_id, project_id, item_type, title, description, owner, status, probability, impact,
                   review_date, review_state, origin, promoted_to, evidence, created_at, updated_at
            FROM raid_items
            WHERE {" AND ".join(conditions)}
            ORDER BY CASE review_state WHEN 'suggested' THEN 0 ELSE 1 END,
                     CASE item_type WHEN 'risk' THEN 0 WHEN 'issue' THEN 1 WHEN 'dependency' THEN 2 ELSE 3 END,
                     created_at
        """, params)
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def create_raid_item(project_id: str, item_type: str, title: str, description: str = None, owner: str = None,
                     status: str = "open", probability: str = None, impact: str = None, review_date=None,
                     review_state: str = "confirmed", origin: str = "manual", evidence: str = None) -> dict:
    if not title or not title.strip():
        raise ValueError("A RAID item needs a title")
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO raid_items
                    (project_id, item_type, title, description, owner, status, probability, impact, review_date,
                     review_state, origin, evidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING item_id, project_id, item_type, title, description, owner, status, probability, impact,
                          review_date, review_state, origin, promoted_to, evidence, created_at, updated_at
            """, (project_id, item_type, title.strip(), description, owner, status, probability, impact, review_date,
                  review_state, origin, evidence))
            row = dict(cur.fetchone())
        conn.commit()
    except psycopg2.errors.CheckViolation as e:
        conn.rollback()
        raise ValueError(f"Invalid value: {e.diag.constraint_name}")
    finally:
        conn.close()
    return row


def update_raid_item(item_id: str, fields: dict):
    """Partial update; returns the updated row or None if the item doesn't exist."""
    unknown = set(fields) - _RAID_EDITABLE
    if unknown:
        raise ValueError(f"Cannot edit: {', '.join(sorted(unknown))}")
    if "title" in fields and not (fields["title"] or "").strip():
        raise ValueError("A RAID item needs a title")
    if not fields:
        raise ValueError("Nothing to update")
    assignments = ", ".join(f"{column} = %s" for column in fields)  # column names come from _RAID_EDITABLE only
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"""
                UPDATE raid_items SET {assignments}, updated_at = now() WHERE item_id = %s
                RETURNING item_id, project_id, item_type, title, description, owner, status, probability, impact,
                          review_date, review_state, origin, promoted_to, evidence, created_at, updated_at
            """, list(fields.values()) + [item_id])
            row = cur.fetchone()
        conn.commit()
    except psycopg2.errors.CheckViolation as e:
        conn.rollback()
        raise ValueError(f"Invalid value: {e.diag.constraint_name}")
    finally:
        conn.close()
    return dict(row) if row else None


def delete_raid_item(item_id: str) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM raid_items WHERE item_id = %s", (item_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return deleted


def import_document_raid_items(project_id: str, doc_key: str, items: list) -> list:
    """Insert assumptions/dependencies parsed from a document's numbered lists (apps/utils/sow_sections.py).
    They are explicit statements in the document, not inferences, so they join the log as confirmed items
    (origin 'document'). Idempotent per document: doc_key identifies the document, so re-running it adds
    nothing, while a different document's '7.1' is a separate item. Returns the rows created."""
    from apps.utils.sow_sections import title_and_description
    created = []
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for it in items:
                title, description = title_and_description(it["text"])
                cur.execute("""
                    INSERT INTO raid_items (project_id, item_type, title, description, status, review_state, origin, source_ref, created_at)
                    VALUES (%s, %s, %s, %s, 'open', 'confirmed', 'document', %s, clock_timestamp())
                    ON CONFLICT (project_id, source_ref) WHERE source_ref IS NOT NULL DO NOTHING
                    RETURNING item_id, project_id, item_type, title, description, owner, status, probability, impact,
                              review_date, review_state, origin, promoted_to, evidence, created_at, updated_at
                """, (project_id, it["item_type"], title, description, f"{doc_key}:{it['item_type']}:{it['number']}"))
                row = cur.fetchone()
                if row:
                    created.append(dict(row))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return created


# Same-thing-reworded cutoff for RAID titles of the SAME type. Measured on the synthetic SOW's 15 assumptions/dependencies:
# distinct items score up to 0.53 against each other, the same item reworded scores 0.54-0.74. 0.65 sits above every distinct
# pair, so it only hides near-restatements; a looser paraphrase just shows up as a suggestion for a person to reject.
SAME_RAID_TITLE_MIN_SCORE = 0.65


def fetch_documents_to_scan(project_id: str, force: bool = False) -> list:
    """Stored documents of the project (oldest first) that the RAID suggester has not read yet (all of them if force)."""
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""
            SELECT doc_id, content, document_type, created_at FROM source_documents
            WHERE project_id = %s {"" if force else "AND raid_scanned_at IS NULL"} ORDER BY created_at
        """, (project_id,))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def mark_documents_scanned(doc_ids: list) -> None:
    if not doc_ids:
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE source_documents SET raid_scanned_at = now() WHERE doc_id = ANY(%s::uuid[])", ([str(d) for d in doc_ids],))
    conn.commit()
    cur.close()
    conn.close()


def store_suggested_raid_items(project_id: str, suggestions: list) -> dict:
    """Save AI suggestions as review_state 'suggested' (origin 'agent'), skipping any that restate an item of the SAME type
    already in the log in ANY state - including a rejected one, so a rejection is remembered - or repeated within this batch."""
    import numpy as _np
    existing = list_raid_items(project_id)
    known = {t: [] for t in ("risk", "assumption", "issue", "dependency")}
    for item in existing:
        known[item["item_type"]].append(item["title"])
    known_embs = {t: [_np.array(e, dtype=float) for e in get_embeddings_batch(titles)] if titles else [] for t, titles in known.items()}
    new_embs = [_np.array(e, dtype=float) for e in get_embeddings_batch([x["title"] for x in suggestions])] if suggestions else []

    def cos(a, b):
        denom = _np.linalg.norm(a) * _np.linalg.norm(b)
        return float(_np.dot(a, b) / denom) if denom else 0.0

    created, skipped = [], 0
    for vec, sug in zip(new_embs, suggestions):
        t = sug["item_type"]
        if any(sug["title"].strip().lower() == k.strip().lower() for k in known[t]) or \
           any(cos(vec, k) >= SAME_RAID_TITLE_MIN_SCORE for k in known_embs[t]):
            skipped += 1
            continue
        known[t].append(sug["title"])
        known_embs[t].append(vec)
        created.append(create_raid_item(
            project_id, t, sug["title"], description=sug.get("description"), owner=sug.get("owner"),
            probability=sug.get("probability"), impact=sug.get("impact"), review_state="suggested", origin="agent",
            evidence=sug.get("evidence")))
    return {"created": created, "skipped_existing": skipped}


def promote_risk_to_issue(item_id: str) -> dict:
    """A risk has happened: create an Issue from it and close the risk, linked to the new issue (the risk's
    promoted_to). One transaction. The Issue starts open, keeps the risk's title/description/owner/impact."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM raid_items WHERE item_id = %s FOR UPDATE", (item_id,))
            risk = cur.fetchone()
            if not risk:
                raise LookupError(f"No RAID item with id {item_id}")
            if risk["item_type"] != "risk":
                raise ValueError("Only a risk can be promoted to an issue")
            if risk["promoted_to"]:
                raise ValueError("This risk was already promoted to an issue")
            if risk["review_state"] != "confirmed":
                raise ValueError("Accept the suggestion before promoting it")
            cur.execute("""
                INSERT INTO raid_items (project_id, item_type, title, description, owner, status, impact, review_state, origin)
                VALUES (%s, 'issue', %s, %s, %s, 'open', %s, 'confirmed', 'manual')
                RETURNING item_id, project_id, item_type, title, description, owner, status, probability, impact,
                          review_date, review_state, origin, promoted_to, evidence, created_at, updated_at
            """, (risk["project_id"], risk["title"], risk["description"], risk["owner"], risk["impact"]))
            issue = dict(cur.fetchone())
            cur.execute("""
                UPDATE raid_items SET status = 'closed', promoted_to = %s, updated_at = now() WHERE item_id = %s
                RETURNING item_id, project_id, item_type, title, description, owner, status, probability, impact,
                          review_date, review_state, origin, promoted_to, evidence, created_at, updated_at
            """, (issue["item_id"], item_id))
            closed_risk = dict(cur.fetchone())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"risk": closed_risk, "issue": issue}


def _split_owners(owner) -> list:
    """Task owners can hold several people ("Jordan Mills, Priya Nair" / "A and B")."""
    if not owner:
        return []
    import re as _re
    return [n.strip() for n in _re.split(r",|&|\band\b", owner) if n.strip()]


def _raci_rows(subjects: list, explicit: list, roster: list) -> dict:
    """Pure: turn subjects (tasks OR deliverables) plus their saved R/A/C/I rows into the matrix payload.
    A subject's owner counts as an IMPLIED Responsible (shown lighter in the UI) until someone is given
    an explicit role on it - so the matrix starts useful instead of empty - and a person with ANY explicit
    role is no longer guessed at. Warnings only fire on rows someone has started (has an explicit
    assignment), otherwise every untouched row would be flagged: 'multiple_accountable',
    'no_responsible', 'no_accountable'."""
    by_subject = {}
    for row in explicit:
        by_subject.setdefault(str(row["subject_id"]), {}).setdefault(row["person"], set()).add(row["role"])

    people = list(roster)
    seen = {n.lower() for n in people}

    def add_person(name):
        if name and name.lower() not in seen:
            seen.add(name.lower())
            people.append(name)

    rows = []
    for subj in subjects:
        sid = str(subj["id"])
        roles_by_person = {p: set(r) for p, r in by_subject.get(sid, {}).items()}
        has_explicit = bool(roles_by_person)
        has_explicit_r = any("R" in r for r in roles_by_person.values())
        implied = []
        if not has_explicit_r:
            for owner in _split_owners(subj["owner"]):
                canon = next((p for p in people if p.lower() == owner.lower()), owner)
                if canon not in roles_by_person:  # someone given ANY explicit role is no longer guessed at
                    implied.append(canon)
        for p in list(roles_by_person) + implied:
            add_person(p)
        assignments = [{"person": p, "roles": sorted(r, key=RACI_ROLES.index), "implied": False}
                       for p, r in roles_by_person.items()]
        assignments += [{"person": p, "roles": ["R"], "implied": True} for p in implied]

        accountable = [a for a in assignments if "A" in a["roles"] and not a["implied"]]
        warnings = []
        if has_explicit:
            if len(accountable) > 1:
                warnings.append("multiple_accountable")
            if not any("R" in a["roles"] for a in assignments):
                warnings.append("no_responsible")
            if not accountable:
                warnings.append("no_accountable")
        rows.append({
            "subject_id": sid, "number": subj.get("number"), "title": subj["title"], "owner": subj["owner"],
            "status": subj["status"], "assignments": assignments, "warnings": warnings,
        })
    return {"people": people, "rows": rows}


def fetch_raci_matrix(project_id: str, subject: str = "task") -> dict:
    """Everything the RACI view needs in one call, for either subject: 'task' (one row per active task) or
    'deliverable' (one row per deliverable, in number order)."""
    if subject not in ("task", "deliverable"):
        raise ValueError("subject must be 'task' or 'deliverable'")
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if subject == "task":
            cur.execute("""
                SELECT task_id AS id, task_text AS title, owner, status
                FROM tasks WHERE project_id = %s AND is_active = TRUE
                ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at
            """, (project_id,))
            subjects = [dict(r) for r in cur.fetchall()]
            cur.execute("""
                SELECT r.task_id AS subject_id, r.person, r.role FROM task_raci r
                JOIN tasks t ON t.task_id = r.task_id WHERE t.project_id = %s AND t.is_active = TRUE
            """, (project_id,))
        else:
            cur.execute("""
                SELECT deliverable_id AS id, number, title, owner, status, created_at
                FROM deliverables WHERE project_id = %s AND review_state = 'confirmed'
            """, (project_id,))
            subjects = sorted((dict(r) for r in cur.fetchall()),
                              key=lambda d: (number_sort_key(d["number"]), d["created_at"]))
            cur.execute("""
                SELECT r.deliverable_id AS subject_id, r.person, r.role FROM deliverable_raci r
                JOIN deliverables d ON d.deliverable_id = r.deliverable_id
                WHERE d.project_id = %s AND d.review_state = 'confirmed'
            """, (project_id,))
        explicit = cur.fetchall()
        cur.execute("SELECT name FROM project_people WHERE project_id = %s ORDER BY name", (project_id,))
        roster = [r["name"] for r in cur.fetchall()]
    conn.close()
    return _raci_rows(subjects, explicit, roster)


# Fixed table/column names per subject - never built from user input.
_RACI_TARGETS = {
    "task": ("task_raci", "task_id", "SELECT 1 FROM tasks WHERE task_id = %s AND is_active = TRUE", "task"),
    "deliverable": ("deliverable_raci", "deliverable_id", "SELECT 1 FROM deliverables WHERE deliverable_id = %s", "deliverable"),
}


def _set_raci(subject: str, subject_id: str, person: str, roles: list) -> list:
    person = (person or "").strip()
    if not person:
        raise ValueError("A person is required")
    bad = [r for r in roles if r not in RACI_ROLES]
    if bad:
        raise ValueError(f"Invalid role(s): {', '.join(bad)}")
    table, id_col, exists_sql, label = _RACI_TARGETS[subject]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(exists_sql, (subject_id,))
            if not cur.fetchone():
                raise LookupError(f"No active {label} with id {subject_id}")
            cur.execute(f"DELETE FROM {table} WHERE {id_col} = %s AND person = %s", (subject_id, person))
            for role in dict.fromkeys(roles):
                cur.execute(f"INSERT INTO {table} ({id_col}, person, role) VALUES (%s, %s, %s)", (subject_id, person, role))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return sorted(dict.fromkeys(roles), key=RACI_ROLES.index)


def set_task_raci(task_id: str, person: str, roles: list) -> list:
    """Replace this person's roles on this task (empty list clears them). Returns the saved roles."""
    return _set_raci("task", task_id, person, roles)


def set_deliverable_raci(deliverable_id: str, person: str, roles: list) -> list:
    return _set_raci("deliverable", deliverable_id, person, roles)


# ---------------------------------------------------------------------------
# Deliverables register: manual-first. Created by hand, by promoting extracted tasks, or (rarely) parsed from a
# numbered DELIVERABLES section; AI discovery is deliberately NOT here yet (DECISIONS.md, "Deliverable-level RACI").
# ---------------------------------------------------------------------------

DELIVERABLE_STATUSES = ("not_started", "in_progress", "completed", "blocked", "on_hold")
_DELIVERABLE_EDITABLE = {"number", "title", "owner", "deadline", "status", "review_state"}
_DELIVERABLE_COLS = ("deliverable_id, project_id, number, title, owner, deadline, status, origin, review_state, evidence, "
                     "source_task_id, created_at, updated_at")


def list_deliverables(project_id: str, states: tuple = ("confirmed", "suggested")) -> list:
    """Deliverables in natural number order, each with task_count (active tasks assigned to it). Rejected suggestions
    are excluded by default; callers that need only real deliverables (RACI rows, similarity, assignment) pass
    states=("confirmed",)."""
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""
            SELECT {", ".join("d." + c.strip() for c in _DELIVERABLE_COLS.replace(chr(10), " ").split(","))},
                   COALESCE(tc.n, 0) AS task_count
            FROM deliverables d
            LEFT JOIN (SELECT deliverable_id, COUNT(*) AS n FROM tasks WHERE is_active = TRUE GROUP BY deliverable_id) tc
                   ON tc.deliverable_id = d.deliverable_id
            WHERE d.project_id = %s AND d.review_state = ANY(%s)
        """, (project_id, list(states)))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return sorted(rows, key=lambda d: (number_sort_key(d["number"]), d["created_at"]))


def create_deliverable(project_id: str, title: str, number: str = None, owner: str = None, deadline: str = None,
                       status: str = "not_started", origin: str = "manual", source_task_id: str = None,
                       review_state: str = "confirmed", evidence: str = None) -> dict:
    if not title or not title.strip():
        raise ValueError("A deliverable needs a title")
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"""
                INSERT INTO deliverables (project_id, number, title, owner, deadline, status, origin, source_task_id,
                                          review_state, evidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_DELIVERABLE_COLS}
            """, (project_id, (number or "").strip() or None, title.strip(), owner, deadline, status, origin, source_task_id,
                  review_state, evidence))
            row = dict(cur.fetchone())
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise ValueError(f"A deliverable numbered {number} already exists in this project")
    except psycopg2.errors.CheckViolation as e:
        conn.rollback()
        raise ValueError(f"Invalid value: {e.diag.constraint_name}")
    finally:
        conn.close()
    return row


def update_deliverable(deliverable_id: str, fields: dict):
    """Partial update; returns the updated row or None if it doesn't exist."""
    unknown = set(fields) - _DELIVERABLE_EDITABLE
    if unknown:
        raise ValueError(f"Cannot edit: {', '.join(sorted(unknown))}")
    if not fields:
        raise ValueError("Nothing to update")
    if "title" in fields and not (fields["title"] or "").strip():
        raise ValueError("A deliverable needs a title")
    if "number" in fields:
        fields = {**fields, "number": (fields["number"] or "").strip() or None}
    assignments = ", ".join(f"{column} = %s" for column in fields)  # column names come from _DELIVERABLE_EDITABLE only
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"UPDATE deliverables SET {assignments}, updated_at = now() WHERE deliverable_id = %s "
                        f"RETURNING {_DELIVERABLE_COLS}", list(fields.values()) + [deliverable_id])
            row = cur.fetchone()
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise ValueError(f"A deliverable numbered {fields.get('number')} already exists in this project")
    except psycopg2.errors.CheckViolation as e:
        conn.rollback()
        raise ValueError(f"Invalid value: {e.diag.constraint_name}")
    finally:
        conn.close()
    return dict(row) if row else None


def delete_deliverable(deliverable_id: str) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM deliverables WHERE deliverable_id = %s", (deliverable_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return deleted


# Similarity cutoff for the "this task looks like deliverable X" hint. Calibrated on one SOW run (35 extracted tasks vs 11
# deliverables): all 6 true restatements scored 0.71-0.85 and the next score down was 0.66; related-but-different tasks
# ("Conduct smoke test post-deployment" ~ 4.8) score 0.77-0.83 too, so this is a SIMILARITY hint a person judges, never a
# duplicate verdict (same limit as embedding-only task matching - see DECISIONS.md).
SIMILAR_DELIVERABLE_MIN_SCORE = 0.70
# A suggested title this close to an existing deliverable's title is the same thing reworded. MEASURED on the 11 deliverables
# of the synthetic SOW: the same deliverable with vs without its "(codebase + unit tests)" suffix scores 0.74-1.00, while two
# DISTINCT deliverables never exceed 0.56 - so 0.65 sits in the gap with margin on both sides. (An earlier 0.75 was set from an
# assumed ~0.9 for rewordings, never measured; it sat at the edge and let two restated deliverables through.)
SAME_DELIVERABLE_TITLE_MIN_SCORE = 0.65


def find_tasks_similar_to_deliverables(project_id: str, threshold: float = SIMILAR_DELIVERABLE_MIN_SCORE) -> dict:
    """{task_id: {deliverable_id, number, title, score}} for active, not-yet-promoted tasks whose best-matching confirmed
    deliverable scores >= threshold."""
    import numpy as _np
    deliverables = list_deliverables(project_id, states=("confirmed",))
    if not deliverables:
        return {}
    d_embs = get_embeddings_batch([d["title"] for d in deliverables])
    promoted = {str(d["source_task_id"]) for d in deliverables if d["source_task_id"]}
    out = {}
    for t in fetch_active_tasks(project_id):
        tid = str(t["task_id"])
        if t.get("task_embedding") is None or tid in promoted:
            continue
        v = t["task_embedding"].to_list() if hasattr(t["task_embedding"], "to_list") else t["task_embedding"]
        v = _np.array(v, dtype=float)
        best_score, best = 0.0, None
        for emb, d in zip(d_embs, deliverables):
            e = _np.array(emb, dtype=float)
            denom = _np.linalg.norm(v) * _np.linalg.norm(e)
            score = float(_np.dot(v, e) / denom) if denom else 0.0
            if score > best_score:
                best_score, best = score, d
        if best is not None and best_score >= threshold:
            out[tid] = {"deliverable_id": str(best["deliverable_id"]), "number": best["number"], "title": best["title"],
                        "score": round(best_score, 2)}
    return out


def assign_tasks_to_deliverables(project_id: str, assignments: list) -> int:
    """assignments: [{task_id, deliverable_id | None}]. All-or-nothing; both sides must belong to the project and the
    deliverable must be a confirmed one. A None deliverable clears the task's assignment. Returns tasks changed."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            changed = 0
            for a in assignments:
                did = a.get("deliverable_id")
                if did:
                    cur.execute("SELECT 1 FROM deliverables WHERE deliverable_id = %s AND project_id = %s "
                                "AND review_state = 'confirmed'", (did, project_id))
                    if not cur.fetchone():
                        raise LookupError(f"No confirmed deliverable {did} in this project")
                cur.execute("UPDATE tasks SET deliverable_id = %s WHERE task_id = %s AND project_id = %s AND is_active = TRUE",
                            (did, a["task_id"], project_id))
                if cur.rowcount == 0:
                    raise LookupError(f"No active task {a['task_id']} in this project")
                changed += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return changed


def fetch_latest_sow_text(project_id: str):
    """The text of the project's most recently stored SOW, or None."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT content FROM source_documents WHERE project_id = %s AND document_type = 'sow' "
                "ORDER BY created_at DESC LIMIT 1", (project_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def fetch_unassigned_tasks(project_id: str) -> list:
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT task_id, task_text, owner FROM tasks
            WHERE project_id = %s AND is_active = TRUE AND deliverable_id IS NULL ORDER BY created_at
        """, (project_id,))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def set_task_deliverable(task_id: str, deliverable_id) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT project_id FROM tasks WHERE task_id = %s AND is_active = TRUE", (task_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        raise LookupError(f"No active task with id {task_id}")
    assign_tasks_to_deliverables(row[0], [{"task_id": task_id, "deliverable_id": deliverable_id}])


def store_suggested_deliverables(project_id: str, suggestions: list) -> dict:
    """Save AI suggestions (review_state 'suggested', origin 'agent'), skipping any whose title is the same thing as an
    existing deliverable - in ANY state, so a suggestion the user already rejected is not re-proposed."""
    existing = list_deliverables(project_id, states=("confirmed", "suggested", "rejected"))
    known_titles = [d["title"] for d in existing]
    import numpy as _np
    known_embs = [_np.array(e, dtype=float) for e in get_embeddings_batch(known_titles)] if known_titles else []
    new_embs = [_np.array(e, dtype=float) for e in get_embeddings_batch([x["title"] for x in suggestions])] if suggestions else []

    def same_as_known(vec, title):
        if any(title.strip().lower() == k.strip().lower() for k in known_titles):
            return True
        for k in known_embs:
            denom = _np.linalg.norm(vec) * _np.linalg.norm(k)
            if denom and float(_np.dot(vec, k) / denom) >= SAME_DELIVERABLE_TITLE_MIN_SCORE:
                return True
        return False

    created, skipped = [], 0
    accepted_embs = []
    for vec, sug in zip(new_embs, suggestions):
        duplicate_of_batch = any(
            float(_np.dot(vec, a) / (_np.linalg.norm(vec) * _np.linalg.norm(a))) >= SAME_DELIVERABLE_TITLE_MIN_SCORE
            for a in accepted_embs)
        if duplicate_of_batch or same_as_known(vec, sug["title"]):
            skipped += 1
            continue
        accepted_embs.append(vec)
        created.append(create_deliverable(project_id, sug["title"], owner=sug.get("owner"), deadline=sug.get("deadline"),
                                          origin="agent", review_state="suggested", evidence=sug.get("evidence")))
    return {"created": created, "skipped_existing": skipped}


def create_deliverables_from_tasks(project_id: str, task_ids: list) -> dict:
    """Promote extracted tasks to deliverables (title, owner, deadline, status copied; the task stays a task).
    All-or-nothing on unknown tasks; a task that is already a deliverable is skipped, not duplicated."""
    created, skipped = [], []
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for tid in dict.fromkeys(task_ids):
                cur.execute("SELECT task_id, task_text, owner, deadline, status FROM tasks "
                            "WHERE task_id = %s AND project_id = %s AND is_active = TRUE", (tid, project_id))
                task = cur.fetchone()
                if not task:
                    raise LookupError(f"No active task {tid} in this project")
                cur.execute("SELECT 1 FROM deliverables WHERE source_task_id = %s", (tid,))
                if cur.fetchone():
                    skipped.append(tid)
                    continue
                status = task["status"] if task["status"] in DELIVERABLE_STATUSES else "not_started"
                cur.execute(f"""
                    INSERT INTO deliverables (project_id, title, owner, deadline, status, origin, source_task_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, 'task', %s, clock_timestamp()) RETURNING {_DELIVERABLE_COLS}
                """, (project_id, task["task_text"], task["owner"], task["deadline"], status, tid))
                created.append(dict(cur.fetchone()))
                cur.execute("UPDATE tasks SET deliverable_id = %s WHERE task_id = %s", (created[-1]["deliverable_id"], tid))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"created": created, "skipped": skipped}


def upsert_document_deliverables(project_id: str, items: list) -> list:
    """Insert deliverables parsed from a document's numbered section. Idempotent: an item whose number already
    exists in the project is left untouched (never overwrites a person's edits). Returns the rows created."""
    created = []
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for item in items:
                cur.execute(f"""
                    INSERT INTO deliverables (project_id, number, title, owner, deadline, origin, created_at)
                    VALUES (%s, %s, %s, %s, %s, 'document', clock_timestamp())
                    ON CONFLICT (project_id, number) WHERE number IS NOT NULL DO NOTHING
                    RETURNING {_DELIVERABLE_COLS}
                """, (project_id, item["number"], item["title"], item.get("owner"), item.get("deadline")))
                row = cur.fetchone()
                if row:
                    created.append(dict(row))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return created


# ---------------------------------------------------------------------------
# Project people (project_people table)
# ---------------------------------------------------------------------------

def ensure_project_people_email_nullable():
    # Additive migration - email used to be NOT NULL, back when the only way into this
    # table was a manual add with a known address. ensure_person_placeholder() below
    # now also inserts name-only rows for task owners as they're extracted, with no
    # email until someone resolves one - that placeholder state requires email to
    # allow NULL. Idempotent: DROP NOT NULL is safe to run even if already dropped.
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("ALTER TABLE project_people ALTER COLUMN email DROP NOT NULL")
    conn.commit()
    cur.close()
    conn.close()


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


def ensure_person_placeholder(project_id: str, name: str) -> None:
    # Called whenever a task's owner is set during extraction (SOW or MoM) - task
    # owners are almost always real people on the project, so the people roster
    # should build itself up rather than staying manual-only. Purely additive: a
    # case-insensitive existence check first (matches resolve_person_email's own
    # lookup) so this never overwrites an existing row's email/role, and does
    # nothing if the person is already known either way. Never raises - a failure
    # here must not break task extraction, which is the actual point of this call.
    name = (name or "").strip()
    if not name:
        return
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM project_people WHERE project_id = %s AND lower(name) = lower(%s)",
            (project_id, name),
        )
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO project_people (project_id, name, email) VALUES (%s, %s, NULL)",
                (project_id, name),
            )
            conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        import logging
        logging.getLogger("db").warning(f"ensure_person_placeholder failed for '{name}' in '{project_id}': {e}")


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
    # email IS NOT NULL matters now that ensure_person_placeholder() can insert a
    # name-only row: a placeholder with no email must still count as UNRESOLVED here,
    # or resolve_attendees would treat it as resolved and try to invite a null
    # address to a real Google Calendar event.
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT name, email, role FROM project_people
            WHERE project_id = %s AND lower(name) = lower(%s) AND email IS NOT NULL
        """, (project_id, name))
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_project_people(project_id: str):
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT name, email, role, weekly_capacity_hours, skills FROM project_people WHERE project_id = %s ORDER BY name
        """, (project_id,))
        rows = cur.fetchall()
    conn.close()
    people = [dict(row) for row in rows]
    for p in people:
        p["weekly_capacity_hours"] = None if p["weekly_capacity_hours"] is None else float(p["weekly_capacity_hours"])
        p["skills"] = p["skills"] or []
    return people


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
                t.project_id,
                COUNT(*) AS total_tasks,
                COUNT(*) FILTER (WHERE t.status = 'blocked' OR t.blocker IS NOT NULL) AS blocked_tasks,
                COUNT(*) FILTER (WHERE t.status = 'completed') AS completed_tasks,
                COUNT(*) FILTER (WHERE t.status = 'in_progress') AS in_progress_tasks,
                COUNT(*) FILTER (WHERE t.status IS NULL OR t.status = 'not_started') AS not_started_tasks,
                COUNT(*) FILTER (WHERE t.status NOT IN ('completed')) AS open_tasks,
                COUNT(*) FILTER (WHERE t.priority = 'high') AS high_priority_tasks,
                COUNT(DISTINCT t.owner) FILTER (WHERE t.owner IS NOT NULL) AS unique_owners,
                COUNT(*) FILTER (WHERE t.jira_issue_key IS NOT NULL) AS jira_synced_tasks,
                COUNT(*) FILTER (WHERE t.jira_issue_key IS NULL) AS jira_unsynced_tasks,
                MAX(t.updated_at) AS last_updated,
                pi.jira_project_key
            FROM tasks t
            LEFT JOIN project_integrations pi ON pi.project_id = t.project_id
            WHERE t.is_active = TRUE AND t.project_id IS NOT NULL
            GROUP BY t.project_id, pi.jira_project_key
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
            SELECT t.task_id, t.task_text, t.owner, t.status, t.deadline, t.blocker, t.priority, t.updated_at,
                   t.possible_duplicate_of, t.deliverable_id, t.estimate_hours, t.hours_still_needed,
                   t.plan_start_week, t.plan_finish_week, t.phase, t.baseline_estimate_hours, t.baseline_start_week,
                   t.baseline_finish_week,
                   (s.baseline_set_at IS NOT NULL AND t.created_at > s.baseline_set_at) AS added_after_baseline,
                   COALESCE((SELECT SUM(te.hours) FROM time_entries te WHERE te.task_id = t.task_id), 0) AS logged_hours
            FROM tasks t
            LEFT JOIN project_hours_settings s ON s.project_id = t.project_id
            WHERE t.project_id = %s AND t.is_active = TRUE
            ORDER BY
                CASE t.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                t.updated_at DESC
        """, (project_id,))
        tasks = [dict(row) for row in cur.fetchall()]
    for t in tasks:
        # The same rules as the Hours page (apps/utils/hours.py) so the two screens never disagree.
        est, logged = t["estimate_hours"], float(t.pop("logged_hours"))
        ets = t["hours_still_needed"]
        remaining = task_remaining(est, logged, t["status"], ets)
        t["estimate_hours"] = None if est is None else float(est)
        t["baseline_estimate_hours"] = None if t["baseline_estimate_hours"] is None else float(t["baseline_estimate_hours"])
        t["hours_still_needed"] = None if ets is None else float(ets)
        t["logged_hours"] = round(logged, 2)
        t["remaining_hours"] = None if remaining is None else round(remaining, 2)

    deps = fetch_dependencies_for_project(project_id)
    depends_on_map = {}
    blocks_map = {}
    for edge in deps:
        depends_on_map.setdefault(edge["task_id"], []).append(edge["depends_on_task_id"])
        blocks_map.setdefault(edge["depends_on_task_id"], []).append(edge["task_id"])

    task_text_by_id = {t["task_id"]: t["task_text"] for t in tasks}
    deliverable_by_id = {d["deliverable_id"]: d for d in list_deliverables(project_id, states=("confirmed",))}
    for t in tasks:
        d = deliverable_by_id.get(t.pop("deliverable_id", None))
        t["deliverable"] = {"deliverable_id": d["deliverable_id"], "number": d["number"], "title": d["title"]} if d else None
        other = t.pop("possible_duplicate_of", None)
        # Only meaningful while the task it was flagged against is still active.
        t["possible_duplicate_of"] = (
            {"task_id": other, "task_text": task_text_by_id[other]} if other in task_text_by_id else None
        )
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
    from apps.db.hours_db import delete_project_hours, ensure_hours_schema  # lazy: hours_db imports this module
    ensure_hours_schema()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # First: deleting a task turns its hour entries into "Other" entries, which can collide with existing ones.
            delete_project_hours(cur, project_id)
            cur.execute("DELETE FROM tasks WHERE project_id = %s", (project_id,))
            deleted_tasks = cur.rowcount
            cur.execute("DELETE FROM project_integrations WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM agent_jobs WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM duplicate_decisions WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM raid_items WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM deliverables WHERE project_id = %s", (project_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return deleted_tasks
