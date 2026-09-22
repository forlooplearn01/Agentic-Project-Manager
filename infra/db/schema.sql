CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS vector;

--Task table: Stores current latest state of each task
CREATE TABLE tasks (
    task_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_text TEXT NOT NULL,
    normalized_task_text TEXT NOT NULL,
    owner TEXT,
    status TEXT DEFAULT 'not_started',
    deadline TEXT,
    blocker TEXT,
    priority TEXT DEFAULT 'medium',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    task_embedding vector(384),
    project_id VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Source documents table: stores each unique (content_hash, project_id) input exactly
-- once. task_events references this via source_doc_id instead of storing the full text
-- per row. content_hash (SHA256) enables fast exact-dedup without full-text comparison,
-- and is scoped by project_id so identical text submitted to two different projects
-- gets its own row/cache entry instead of colliding.
CREATE TABLE IF NOT EXISTS source_documents (
    doc_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    project_id VARCHAR,
    document_type TEXT,             -- 'sow' | 'mom', set by the Supervisor
    pipeline_result JSONB,          -- cached formatted API result; NULL until first successful run
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (content_hash, project_id)
);

CREATE INDEX IF NOT EXISTS idx_source_documents_hash ON source_documents(content_hash);

--Extracted Events table: Stores every extracted event from input
CREATE TABLE task_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID REFERENCES tasks(task_id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    task_text TEXT NOT NULL,
    owner TEXT,
    status TEXT,
    deadline TEXT,
    blocker TEXT,
    dependency TEXT,
    match_method TEXT,
    match_confidence TEXT,
    source_doc_id UUID REFERENCES source_documents(doc_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Unresolved Events table: Human review queue for update events that matched no existing task.
-- These are NOT auto-created as tasks - they require human review to decide:
-- link to an existing task, approve as new, or discard.
CREATE TABLE unresolved_events (
    unresolved_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,
    task_text TEXT NOT NULL,
    owner TEXT,
    status TEXT,
    deadline TEXT,
    blocker TEXT,
    dependency TEXT,
    reason TEXT NOT NULL DEFAULT 'no_match_found',
    source_input TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Agent jobs table: persists async /run job state across restarts.
-- Rows older than 24 hours are purged by the cleanup helper in db.py.
CREATE TABLE IF NOT EXISTS agent_jobs (
    job_id UUID PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'running',   -- running | completed | failed
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_jobs_created_at ON agent_jobs(created_at);

-- Task dependencies: a real join table instead of the free-text `dependency` field on task_events.
-- Makes blocker propagation and dependency-aware prioritization queryable (e.g. "what blocks this
-- task" / "what does this task block") rather than only descriptive. Written by the Planning agent
-- when it identifies a genuine dependency between two tasks in the same project.
CREATE TABLE IF NOT EXISTS task_dependencies (
    dependency_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    depends_on_task_id UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (task_id, depends_on_task_id),
    CHECK (task_id != depends_on_task_id)
);

CREATE INDEX IF NOT EXISTS idx_task_dependencies_task_id ON task_dependencies(task_id);
CREATE INDEX IF NOT EXISTS idx_task_dependencies_depends_on ON task_dependencies(depends_on_task_id);

-- Project people: maps an owner/attendee name to a real email address, scoped per project.
-- Nothing else in the schema does this - "owner" on `tasks` is a free-text name. Needed by the
-- Meeting Coordinator agent to actually invite anyone to a scheduled follow-up meeting.
-- Populated by parsing stakeholder names/emails out of the SOW where present, otherwise seeded
-- manually. A name that can't be resolved to an email is not guessed - the calling agent surfaces
-- it as a flagged item instead, the same pattern unresolved_events already uses for match failures.
CREATE TABLE IF NOT EXISTS project_people (
    person_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id VARCHAR NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, name)
);

CREATE INDEX IF NOT EXISTS idx_project_people_project_id ON project_people(project_id);

-- Project integrations: per-project external-system mapping. Auth (Jira URL/token/email,
-- Google Calendar service account/domain/organizer) is one global connection for the whole
-- deployment - but WHICH Jira project a given task-manager project's tasks sync into must be
-- per-project, since different task-manager projects legitimately map to different Jira
-- projects. A single global JIRA_PROJECT_KEY env var would force everything into one Jira
-- project regardless of which task-manager project it came from - wrong once more than one
-- project is in use. Nullable: a project with no row here (or a NULL key) just means Jira
-- sync is "not configured for this project" even if Jira auth itself is connected.
CREATE TABLE IF NOT EXISTS project_integrations (
    project_id VARCHAR PRIMARY KEY,
    jira_project_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_tasks_normalized_task_text ON tasks(normalized_task_text);
CREATE INDEX idx_tasks_owner ON tasks(owner);
CREATE INDEX idx_tasks_project_id ON tasks(project_id);
CREATE INDEX idx_task_events_task_id ON task_events(task_id);
CREATE INDEX idx_task_events_event_type ON task_events(event_type);
CREATE INDEX idx_task_events_created_at ON task_events(created_at);
CREATE INDEX idx_unresolved_events_event_type ON unresolved_events(event_type);
CREATE INDEX idx_unresolved_events_created_at ON unresolved_events(created_at);

-- Chat message history - one row per turn, keyed by a client-generated session_id
-- (no user accounts exist yet, so a session is the only identity chat has).
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id VARCHAR NOT NULL,
    role VARCHAR NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_chat_messages_session ON chat_messages(session_id, created_at);

-- pgvector HNSW index for fast approximate nearest-neighbour semantic search.
-- hnsw is preferred over ivfflat: works on empty tables, no reindex needed as data grows,
-- and delivers faster + more accurate queries at the cost of slightly more memory.
-- vector_cosine_ops matches the cosine similarity used in fetch_semantic_task_match().
CREATE INDEX idx_tasks_embedding_hnsw ON tasks
    USING hnsw (task_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Migration (run against an existing DB that predates this schema revision):
-- ALTER TABLE task_events ADD COLUMN IF NOT EXISTS source_doc_id UUID REFERENCES source_documents(doc_id);
-- ALTER TABLE task_events DROP COLUMN IF EXISTS source_input;
-- ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS document_type TEXT;
-- CREATE TABLE IF NOT EXISTS task_dependencies (
--     dependency_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--     task_id UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
--     depends_on_task_id UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
--     created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
--     UNIQUE (task_id, depends_on_task_id),
--     CHECK (task_id != depends_on_task_id)
-- );
-- CREATE TABLE IF NOT EXISTS project_integrations (
--     project_id VARCHAR PRIMARY KEY,
--     jira_project_key TEXT,
--     created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
--     updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
-- );
-- CREATE TABLE IF NOT EXISTS project_people (
--     person_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--     project_id VARCHAR NOT NULL,
--     name TEXT NOT NULL,
--     email TEXT NOT NULL,
--     role TEXT,
--     created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
--     UNIQUE (project_id, name)
-- );
-- CREATE TABLE IF NOT EXISTS chat_messages (
--     message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--     session_id VARCHAR NOT NULL,
--     role VARCHAR NOT NULL CHECK (role IN ('user', 'assistant')),
--     content TEXT NOT NULL,
--     created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
-- );
-- CREATE INDEX idx_chat_messages_session ON chat_messages(session_id, created_at);
-- ALTER TABLE tasks ADD COLUMN IF NOT EXISTS jira_issue_key TEXT;
-- ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS project_id VARCHAR;
-- ALTER TABLE source_documents DROP CONSTRAINT IF EXISTS source_documents_content_hash_key;
-- ALTER TABLE source_documents ADD CONSTRAINT source_documents_content_hash_project_id_key UNIQUE (content_hash, project_id);
-- CREATE TABLE IF NOT EXISTS meetings (
--     meeting_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--     project_id VARCHAR NOT NULL,
--     title TEXT NOT NULL,
--     description TEXT,
--     attendee_emails JSONB NOT NULL DEFAULT '[]',
--     unresolved_attendees JSONB NOT NULL DEFAULT '[]',
--     start_iso TEXT NOT NULL,
--     end_iso TEXT NOT NULL,
--     status TEXT NOT NULL DEFAULT 'pending_approval',
--     reason TEXT,
--     google_event_id TEXT,
--     meeting_link TEXT,
--     created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
--     updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
-- );
-- CREATE INDEX idx_meetings_project_id ON meetings(project_id);
-- CREATE INDEX idx_meetings_status ON meetings(status);
