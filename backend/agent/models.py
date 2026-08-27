"""Agent-layer models: the task-queue unit of work and the launcher IO
contract. Domain entities live in backend/domain/models.py; these types exist
for the agent/queue/launcher layers only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from backend.domain.models import Comment, Patch, now_ms

class TaskState(str, Enum):
    """State of a queued agent task (distinct from thread status: a thread has
    many tasks over its life, one per iteration).

    Kafka-ish delivery: a failed attempt goes back to QUEUED until attempts
    reach max_attempts, then the task lands in DLQ for human inspection."""
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLED = "cancelled"
    DONE = "done"
    DLQ = "dlq"

# ---- task-launcher contract --------------------------------------------------
# The IO types between the Agent (orchestration) and a TaskLauncher (the actual
# reasoning worker — a Claude Code task, or any future launcher).

class TaskPhase(str, Enum):
    """Progress phases the executor reports; the AgentRunner maps them to
    ThreadStatus. ANALYZING/CODING come from the launcher; DEPLOYING from the
    Agent's own side-effect stage (push + preview deploy)."""
    ANALYZING = "analyzing"
    CODING = "coding"
    DEPLOYING = "deploying"

@dataclass
class FixTaskSpec:
    """Everything a launcher needs to produce a fix: the target, the user's
    comments (with captures), and the sha its worktree branches from."""
    thread_id: str
    target_selector: str
    target_label: str
    base_sha: str
    comments: list[Comment]
    attempt: int
    max_attempts: int

@dataclass
class FixTaskResult:
    """What a launcher hands back: the proposed code change plus its (possibly
    faked) reasoning. A real launcher would also carry a branch/diff ref."""
    patch: Patch
    analysis: str
    launcher: str

@dataclass
class IterationOutcome:
    """What one executed iteration produced. The Agent returns this; the
    AgentRunner owns turning it into records (iteration row), narration
    (system comments), and a status transition."""
    preview_sha: str
    parent_sha: str
    patch: Patch
    analysis: str
    launcher: str

@dataclass
class AgentTask:
    """A unit of work for the agent worker pool: run one fix iteration for a
    thread, addressing comment_ids. Persisted in the db-backed queue.

    Delivery bookkeeping: attempts counts claims (retries re-claim); a RUNNING
    task holds a lease (lease_expires_at) that its worker heartbeats — an
    expired lease means the worker died and the janitor may reclaim the task.
    """
    id: str
    thread_id: str
    comment_ids: list[str]
    state: TaskState = TaskState.QUEUED
    attempts: int = 0
    max_attempts: int = 3
    claimed_by: str | None = None
    lease_expires_at: int | None = None
    last_error: str | None = None
    created_at: int = field(default_factory=now_ms)

    @property
    def idempotency_key(self) -> str:
        # A task is identified by the newest comment it addresses. Delivery is
        # at-least-once; the handler dedupes on this key so the *effect* is
        # exactly-once (an iteration for this key is only ever created once).
        return self.comment_ids[-1]

    def to_api(self) -> dict:
        return {
            "id": self.id, "threadId": self.thread_id, "commentIds": self.comment_ids,
            "state": self.state.value, "attempts": self.attempts,
            "maxAttempts": self.max_attempts, "claimedBy": self.claimed_by,
            "leaseExpiresAt": self.lease_expires_at, "lastError": self.last_error,
            "createdAt": self.created_at,
        }
