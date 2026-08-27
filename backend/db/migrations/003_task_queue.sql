-- Durable agent task queue (Kafka-ish semantics on a table).
-- state: queued -> running -> done | queued (retry) | dlq | cancelled
-- Claims are atomic (CAS on state); a running task holds a heartbeated lease.
-- An expired lease = the worker died / tracking was lost; the janitor
-- reclaims it (retry or DLQ). Queue survives process restarts.

CREATE TABLE agent_tasks (
    id               TEXT PRIMARY KEY,
    thread_id        TEXT NOT NULL REFERENCES threads(id),
    comment_ids_json TEXT NOT NULL,
    state            TEXT NOT NULL CHECK (state IN ('queued','running','done','cancelled','dlq')),
    attempts         INTEGER NOT NULL DEFAULT 0,   -- number of claims
    max_attempts     INTEGER NOT NULL DEFAULT 3,
    claimed_by       TEXT,                          -- worker id holding the lease
    lease_expires_at INTEGER,                       -- ms epoch; heartbeat extends it
    last_error       TEXT,
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL
);
CREATE INDEX idx_tasks_state ON agent_tasks(state);
CREATE INDEX idx_tasks_thread ON agent_tasks(thread_id);
