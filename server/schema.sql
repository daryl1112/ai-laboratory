CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS experiments (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    prompt        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'queued',
    plan          JSONB,
    revision      INT  NOT NULL DEFAULT 0,
    container_id  TEXT,
    image         TEXT,
    options       JSONB NOT NULL DEFAULT '{}',
    progress_pct  INT,
    progress_msg  TEXT,
    exit_code     INT,
    conclusion    TEXT,
    conclusion_embedding vector(768),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS experiment_events (
    id            BIGSERIAL PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    type          TEXT NOT NULL,
    payload       JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_exp ON experiment_events(experiment_id, id);

CREATE TABLE IF NOT EXISTS messages (
    id            BIGSERIAL PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    seq           INT  NOT NULL,
    phase         TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL,
    content       TEXT NOT NULL DEFAULT '',
    tool_calls    JSONB,
    model         TEXT,
    think         BOOLEAN,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_exp ON messages(experiment_id, seq);

CREATE TABLE IF NOT EXISTS artifacts (
    id            BIGSERIAL PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    path          TEXT NOT NULL,
    size_bytes    BIGINT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, path)
);

CREATE TABLE IF NOT EXISTS services (
    id            BIGSERIAL PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,
    container_name TEXT NOT NULL,
    volume        TEXT,
    persist       BOOLEAN NOT NULL DEFAULT false,
    state         TEXT NOT NULL DEFAULT 'created',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tools_registry (
    name       TEXT PRIMARY KEY,
    path       TEXT NOT NULL,
    schema     JSONB,
    status     TEXT NOT NULL DEFAULT 'loaded',
    error      TEXT,
    loaded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
