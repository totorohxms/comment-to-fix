"""Repositories: the only code that touches SQL. Everything above this layer
speaks the typed models from backend/domain/models.py + backend/agent/models.py."""

import json

from backend.db.database import Database
from backend.domain.models import (
    Comment, Iteration, Patch, Permission, StatusChange, Thread, ThreadStatus, User, new_id, now_ms,
)
from backend.agent.models import AgentTask, TaskState

class MetaRepo:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str) -> str | None:
        row = self.db.query_one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

class UserRepo:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(r) -> User:
        return User(id=r["id"], name=r["name"], emoji=r["emoji"],
                    permission=Permission(r["permission"]))

    def get(self, user_id: str) -> User | None:
        r = self.db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
        return self._row(r) if r else None

    def list(self) -> list[User]:
        return [self._row(r) for r in self.db.query("SELECT * FROM users ORDER BY id")]

class CommentRepo:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(r) -> Comment:
        return Comment(
            id=r["id"], thread_id=r["thread_id"], user_id=r["user_id"], text=r["text"],
            system=bool(r["system"]),
            capture=json.loads(r["capture_json"]) if r["capture_json"] else None,
            created_at=r["created_at"],
        )

    def append(self, thread_id: str, *, user_id: str, text: str,
               capture: dict | None = None, system: bool = False) -> Comment:
        c = Comment(id=new_id("cmt"), thread_id=thread_id, user_id=user_id, text=text,
                    system=system, capture=capture, created_at=now_ms())
        self.db.execute(
            "INSERT INTO comments (id, thread_id, user_id, text, system, capture_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (c.id, c.thread_id, c.user_id, c.text, int(c.system),
             json.dumps(c.capture) if c.capture else None, c.created_at))
        return c
    # Append-only: this repo intentionally has no update or delete.

    def get(self, comment_id: str) -> Comment | None:
        r = self.db.query_one("SELECT * FROM comments WHERE id = ?", (comment_id,))
        return self._row(r) if r else None

    def for_thread(self, thread_id: str) -> list[Comment]:
        return [self._row(r) for r in self.db.query(
            "SELECT * FROM comments WHERE thread_id = ? ORDER BY created_at, rowid", (thread_id,))]

class ThreadRepo:
    def __init__(self, db: Database, comments: CommentRepo):
        self.db = db
        self.comments = comments

    def create(self, *, target_selector: str, target_label: str,
               page_url: str, base_sha: str) -> Thread:
        t = Thread(id=new_id("thr"), created_at=now_ms(), target_selector=target_selector,
                   target_label=target_label, page_url=page_url, base_sha=base_sha,
                   status=ThreadStatus.OPEN)  # a discussion until @agent is summoned
        self.db.execute(
            "INSERT INTO threads (id, created_at, target_selector, target_label, page_url, "
            "base_sha, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (t.id, t.created_at, t.target_selector, t.target_label, t.page_url,
             t.base_sha, t.status.value))
        self._record_status(t.id, t.status)
        return self.get(t.id)

    def get(self, thread_id: str) -> Thread | None:
        r = self.db.query_one("SELECT * FROM threads WHERE id = ?", (thread_id,))
        if not r:
            return None
        history = [StatusChange(status=ThreadStatus(h["status"]), at=h["at"])
                   for h in self.db.query(
                       "SELECT status, at FROM thread_status_history WHERE thread_id = ? ORDER BY id",
                       (thread_id,))]
        iterations = [Iteration(sha=i["sha"], parent_sha=i["parent_sha"], summary=i["summary"],
                                comment_ids=json.loads(i["comment_ids_json"]))
                      for i in self.db.query(
                          "SELECT * FROM iterations WHERE thread_id = ? ORDER BY id", (thread_id,))]
        return Thread(
            id=r["id"], created_at=r["created_at"], target_selector=r["target_selector"],
            target_label=r["target_label"], page_url=r["page_url"], base_sha=r["base_sha"],
            status=ThreadStatus(r["status"]), status_history=history,
            comments=self.comments.for_thread(thread_id), iterations=iterations,
            preview_sha=r["preview_sha"], preview_url=r["preview_url"], pr_url=r["pr_url"])

    def list(self, page_url: str | None = None) -> list[Thread]:
        threads = [self.get(r["id"]) for r in
                   self.db.query("SELECT id FROM threads ORDER BY created_at")]
        if page_url:
            threads = [t for t in threads
                       if t.page_url == page_url
                       or any(page_url == f"/preview/{i.sha}" for i in t.iterations)]
        return threads

    def thread_for_preview_sha(self, sha: str) -> Thread | None:
        """The thread whose iteration deployed this preview sha, if any."""
        r = self.db.query_one(
            "SELECT thread_id FROM iterations WHERE sha = ? ORDER BY rowid DESC LIMIT 1", (sha,))
        return self.get(r["thread_id"]) if r else None

    def status(self, thread_id: str) -> ThreadStatus:
        r = self.db.query_one("SELECT status FROM threads WHERE id = ?", (thread_id,))
        return ThreadStatus(r["status"])

    def set_status(self, thread_id: str, status: ThreadStatus) -> None:
        self.db.execute("UPDATE threads SET status = ? WHERE id = ?", (status.value, thread_id))
        self._record_status(thread_id, status)

    def transition(self, thread_id: str, from_status: ThreadStatus,
                   to_status: ThreadStatus) -> bool:
        """CAS status change: False if the thread already moved on. Use for
        one-shot transitions (e.g. approve) that must not fire twice."""
        cur = self.db.execute(
            "UPDATE threads SET status = ? WHERE id = ? AND status = ?",
            (to_status.value, thread_id, from_status.value))
        if cur.rowcount != 1:
            return False
        self._record_status(thread_id, to_status)
        return True

    def _record_status(self, thread_id: str, status: ThreadStatus) -> None:
        self.db.execute(
            "INSERT INTO thread_status_history (thread_id, status, at) VALUES (?, ?, ?)",
            (thread_id, status.value, now_ms()))

    def add_iteration(self, thread_id: str, it: Iteration) -> None:
        self.db.execute(
            "INSERT INTO iterations (thread_id, sha, parent_sha, summary, comment_ids_json, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (thread_id, it.sha, it.parent_sha, it.summary,
             json.dumps(it.comment_ids), now_ms()))
        self.db.execute(
            "UPDATE threads SET preview_sha = ?, preview_url = ? WHERE id = ?",
            (it.sha, f"/preview/{it.sha}", thread_id))

    def set_pr_url(self, thread_id: str, pr_url: str) -> None:
        self.db.execute("UPDATE threads SET pr_url = ? WHERE id = ?", (pr_url, thread_id))

class TaskRepo:
    """Durable task queue. Every state transition is a compare-and-swap on the
    current state, so claims and cancellations are atomic even with many
    workers (the WHERE-state clause is the claim lock)."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(r) -> AgentTask:
        return AgentTask(
            id=r["id"], thread_id=r["thread_id"],
            comment_ids=json.loads(r["comment_ids_json"]),
            state=TaskState(r["state"]), attempts=r["attempts"],
            max_attempts=r["max_attempts"], claimed_by=r["claimed_by"],
            lease_expires_at=r["lease_expires_at"], last_error=r["last_error"],
            created_at=r["created_at"])

    def get(self, task_id: str) -> AgentTask | None:
        r = self.db.query_one("SELECT * FROM agent_tasks WHERE id = ?", (task_id,))
        return self._row(r) if r else None

    def enqueue(self, task: AgentTask) -> None:
        self.db.execute(
            "INSERT INTO agent_tasks (id, thread_id, comment_ids_json, state, attempts, "
            "max_attempts, created_at, updated_at) VALUES (?, ?, ?, 'queued', 0, ?, ?, ?)",
            (task.id, task.thread_id, json.dumps(task.comment_ids),
             task.max_attempts, task.created_at, now_ms()))

    def active_for_thread(self, thread_id: str) -> AgentTask | None:
        """The thread's live task (queued or running), newest first."""
        r = self.db.query_one(
            "SELECT * FROM agent_tasks WHERE thread_id = ? AND state IN ('queued','running') "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1", (thread_id,))
        return self._row(r) if r else None

    def coalesce_comment(self, task_id: str, comment_id: str) -> bool:
        """Fold one more comment into a not-yet-started task. CAS on QUEUED:
        returns False if the task started in the meantime."""
        t = self.get(task_id)
        cur = self.db.execute(
            "UPDATE agent_tasks SET comment_ids_json = ?, updated_at = ? "
            "WHERE id = ? AND state = 'queued'",
            (json.dumps([*t.comment_ids, comment_id]), now_ms(), task_id))
        return cur.rowcount == 1

    def claim_next(self, worker_id: str, lease_ms: int) -> AgentTask | None:
        """Atomic claim: CAS oldest QUEUED -> RUNNING with a lease. The
        attempts counter increments on claim, so it counts deliveries."""
        while True:
            r = self.db.query_one(
                "SELECT id FROM agent_tasks WHERE state = 'queued' "
                "ORDER BY created_at, rowid LIMIT 1")
            if not r:
                return None
            cur = self.db.execute(
                "UPDATE agent_tasks SET state = 'running', claimed_by = ?, "
                "attempts = attempts + 1, lease_expires_at = ?, updated_at = ? "
                "WHERE id = ? AND state = 'queued'",
                (worker_id, now_ms() + lease_ms, now_ms(), r["id"]))
            if cur.rowcount == 1:
                return self.get(r["id"])
            # lost the race to another worker; pick the next one

    def heartbeat(self, task_id: str, worker_id: str, lease_ms: int) -> bool:
        """Extend the lease — only if this worker still owns the task."""
        cur = self.db.execute(
            "UPDATE agent_tasks SET lease_expires_at = ?, updated_at = ? "
            "WHERE id = ? AND state = 'running' AND claimed_by = ?",
            (now_ms() + lease_ms, now_ms(), task_id, worker_id))
        return cur.rowcount == 1

    def complete(self, task_id: str) -> bool:
        cur = self.db.execute(
            "UPDATE agent_tasks SET state = 'done', updated_at = ? "
            "WHERE id = ? AND state = 'running'", (now_ms(), task_id))
        return cur.rowcount == 1

    def cancel(self, task_id: str) -> None:
        self.db.execute(
            "UPDATE agent_tasks SET state = 'cancelled', updated_at = ? "
            "WHERE id = ? AND state IN ('queued','running')", (now_ms(), task_id))

    def fail(self, task_id: str, error: str) -> TaskState:
        """Record a failed attempt: back to QUEUED for retry, or DLQ once
        attempts have reached max_attempts. Returns the resulting state."""
        t = self.get(task_id)
        new_state = TaskState.DLQ if t.attempts >= t.max_attempts else TaskState.QUEUED
        self.db.execute(
            "UPDATE agent_tasks SET state = ?, claimed_by = NULL, lease_expires_at = NULL, "
            "last_error = ?, updated_at = ? WHERE id = ? AND state = 'running'",
            (new_state.value, error, now_ms(), task_id))
        return new_state

    def reap_expired(self) -> list[tuple[AgentTask, TaskState]]:
        """Janitor sweep: RUNNING tasks whose lease expired were orphaned by a
        dead worker (or a process that lost tracking). Retry-or-DLQ each."""
        rows = self.db.query(
            "SELECT id FROM agent_tasks WHERE state = 'running' AND lease_expires_at < ?",
            (now_ms(),))
        out = []
        for r in rows:
            new_state = self.fail(r["id"], "lease expired — worker lost")
            out.append((self.get(r["id"]), new_state))
        return out

    def requeue_from_dlq(self, task_id: str) -> bool:
        """Human-initiated DLQ replay: attempts reset, back on the queue."""
        cur = self.db.execute(
            "UPDATE agent_tasks SET state = 'queued', attempts = 0, claimed_by = NULL, "
            "lease_expires_at = NULL, updated_at = ? WHERE id = ? AND state = 'dlq'",
            (now_ms(), task_id))
        return cur.rowcount == 1

    def covered_comment_ids(self, thread_id: str) -> set[str]:
        """Comment ids any task (in any state, cancelled included) has ever
        covered. A cancelled ask must not be silently resurrected by a later
        drain — re-asking is an explicit new comment."""
        out: set[str] = set()
        for r in self.db.query(
                "SELECT comment_ids_json FROM agent_tasks WHERE thread_id = ?", (thread_id,)):
            out.update(json.loads(r["comment_ids_json"]))
        return out

    def list_by_state(self, state: TaskState) -> list[AgentTask]:
        return [self._row(r) for r in self.db.query(
            "SELECT * FROM agent_tasks WHERE state = ? ORDER BY created_at", (state.value,))]

    def counts(self) -> dict:
        rows = self.db.query("SELECT state, COUNT(*) AS n FROM agent_tasks GROUP BY state")
        return {r["state"]: r["n"] for r in rows}

class PatchRepo:
    def __init__(self, db: Database):
        self.db = db

    def get(self, preview_sha: str) -> list[Patch] | None:
        """None = unknown sha (404); previews always have >= 1 patch."""
        rows = self.db.query(
            "SELECT css, summary FROM preview_patches WHERE preview_sha = ? ORDER BY position",
            (preview_sha,))
        return [Patch(css=r["css"], summary=r["summary"]) for r in rows] if rows else None

    def put(self, preview_sha: str, patches: list[Patch]) -> None:
        for pos, p in enumerate(patches):
            self.db.execute(
                "INSERT INTO preview_patches (preview_sha, position, css, summary) "
                "VALUES (?, ?, ?, ?)", (preview_sha, pos, p.css, p.summary))
