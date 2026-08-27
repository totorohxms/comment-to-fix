-- commentToFix core schema.
-- SQLite for the prototype; types/constraints written so this ports to
-- Postgres with minimal edits (INTEGER ms timestamps, TEXT ids, JSON as TEXT).

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE users (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    emoji      TEXT NOT NULL,
    permission TEXT NOT NULL CHECK (permission IN ('view', 'comment'))
);

CREATE TABLE threads (
    id              TEXT PRIMARY KEY,
    created_at      INTEGER NOT NULL,
    target_selector TEXT NOT NULL,
    target_label    TEXT NOT NULL,
    page_url        TEXT NOT NULL,
    base_sha        TEXT NOT NULL,          -- deployment sha the first comment was made on
    status          TEXT NOT NULL,
    preview_sha     TEXT,                   -- latest preview deployment
    preview_url     TEXT,
    pr_url          TEXT
);

CREATE TABLE thread_status_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL REFERENCES threads(id),
    status    TEXT NOT NULL,
    at        INTEGER NOT NULL
);
CREATE INDEX idx_history_thread ON thread_status_history(thread_id);

-- Append-only: the application layer exposes no UPDATE/DELETE for comments.
CREATE TABLE comments (
    id           TEXT PRIMARY KEY,
    thread_id    TEXT NOT NULL REFERENCES threads(id),
    user_id      TEXT NOT NULL,
    text         TEXT NOT NULL,
    system       INTEGER NOT NULL DEFAULT 0,
    capture_json TEXT,                      -- runtime capture bundle (screenshot/network/DOM/sha/trace)
    created_at   INTEGER NOT NULL
);
CREATE INDEX idx_comments_thread ON comments(thread_id);

-- One row per fix iteration (= one preview deployment, one worktree).
CREATE TABLE iterations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id        TEXT NOT NULL REFERENCES threads(id),
    sha              TEXT NOT NULL,          -- preview deployment sha
    parent_sha       TEXT NOT NULL,          -- worktree branched from this sha
    summary          TEXT NOT NULL,
    comment_ids_json TEXT NOT NULL,          -- comments addressed by this iteration
    created_at       INTEGER NOT NULL
);
CREATE INDEX idx_iterations_thread ON iterations(thread_id);

-- Accumulated patch set per preview sha (child previews carry ancestors' patches).
CREATE TABLE preview_patches (
    preview_sha TEXT NOT NULL,
    position    INTEGER NOT NULL,
    css         TEXT NOT NULL,
    summary     TEXT NOT NULL,
    PRIMARY KEY (preview_sha, position)
);
