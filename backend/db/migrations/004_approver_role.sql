-- Widen permissions to view < comment < approve. Approvers (the engineering
-- group) are the only users who can turn a verified preview into a PR.
-- SQLite can't alter CHECK constraints, so recreate the table.

CREATE TABLE users_new (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    emoji      TEXT NOT NULL,
    permission TEXT NOT NULL CHECK (permission IN ('view', 'comment', 'approve'))
);
INSERT INTO users_new SELECT id, name, emoji, permission FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;

-- Engineers approve; designers comment; viewers watch.
UPDATE users SET permission = 'approve' WHERE id = 'evan';
