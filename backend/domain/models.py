"""Domain models — the single source of truth for core entities and states.

Everything that crosses a layer boundary (routes <-> services <-> agent <-> db)
is one of these types. The API wire format (camelCase, matching the SDK) is
produced only by the to_api() methods here. Agent/queue-specific types live in
backend/agent/models.py; HTTP request shapes in backend/routes/schemas.py.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from itertools import count

# ---- id / time helpers -------------------------------------------------------

def sha(n: int = 7) -> str:
    return secrets.token_hex(8)[:n]

_seq = count(1)

def new_id(prefix: str) -> str:
    return f"{prefix}_{next(_seq)}_{sha(4)}"

def now_ms() -> int:
    return int(time.time() * 1000)

# ---- enums -------------------------------------------------------------------

class Permission(str, Enum):
    """view < comment < approve. Approvers (the engineering group) are the
    only ones who can turn a verified preview into a PR."""
    VIEW = "view"
    COMMENT = "comment"
    APPROVE = "approve"

class ThreadStatus(str, Enum):
    """Thread lifecycle. A thread is a plain discussion (OPEN) until someone
    summons @agent. TRIGGERED doubles as 'queued for an agent worker' — with
    the bounded worker pool a thread can sit here while the queue drains."""
    OPEN = "open"
    TRIGGERED = "triggered"
    ANALYZING = "analyzing"
    CODING = "coding"
    DEPLOYING = "deploying"
    PREVIEW_READY = "preview_ready"
    PR_OPEN = "pr_open"
    MERGED = "merged"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

# Interrupt policy boundaries (see agent/runner.py):
INTERRUPTIBLE_STATUSES = frozenset({ThreadStatus.TRIGGERED, ThreadStatus.ANALYZING})
IN_FLIGHT_STATUSES = frozenset({
    ThreadStatus.TRIGGERED, ThreadStatus.ANALYZING, ThreadStatus.CODING, ThreadStatus.DEPLOYING,
})
# FAILED is deliberately not closed: commenting on a failed thread retries it.
CLOSED_STATUSES = frozenset({
    ThreadStatus.PR_OPEN, ThreadStatus.MERGED, ThreadStatus.DONE, ThreadStatus.CANCELLED,
})

# ---- records -----------------------------------------------------------------

@dataclass
class User:
    id: str
    name: str
    emoji: str
    permission: Permission

    @property
    def can_comment(self) -> bool:
        return self.permission in (Permission.COMMENT, Permission.APPROVE)

    @property
    def can_approve(self) -> bool:
        return self.permission == Permission.APPROVE

    def to_api(self) -> dict:
        return {"id": self.id, "name": self.name, "emoji": self.emoji,
                "permission": self.permission.value}

@dataclass
class Patch:
    """One synthesized code change. In the real product this is a git diff on a
    branch; here it's CSS applied to the preview render."""
    css: str
    summary: str

@dataclass
class StatusChange:
    status: ThreadStatus
    at: int

@dataclass
class Comment:
    id: str
    thread_id: str
    user_id: str
    text: str
    system: bool
    capture: dict | None  # opaque runtime bundle (screenshot/network/DOM/...); stored as JSON
    created_at: int

    def to_api(self) -> dict:
        cap = self.capture
        return {
            "id": self.id, "threadId": self.thread_id, "userId": self.user_id,
            "text": self.text, "system": self.system, "createdAt": self.created_at,
            "hasCapture": bool(cap),
            "captureMeta": {
                "sha": cap.get("sha"), "url": cap.get("url"),
                "networkCount": len(cap.get("network") or []),
                "consoleCount": len(cap.get("console") or []),
                "domBytes": len(cap.get("domSnapshot") or ""),
                "hasScreenshot": bool(cap.get("screenshot")),
                "viewport": cap.get("viewport"), "traceId": cap.get("traceId"),
            } if cap else None,
        }

@dataclass
class Iteration:
    """One preview deployment: a worktree branched off parent_sha carrying the
    fix for comment_ids, deployed as preview sha."""
    sha: str
    parent_sha: str
    summary: str
    comment_ids: list[str]

    def to_api(self) -> dict:
        return {"sha": self.sha, "parentSha": self.parent_sha,
                "summary": self.summary, "commentIds": self.comment_ids}

@dataclass
class Thread:
    id: str
    created_at: int
    target_selector: str
    target_label: str
    page_url: str
    base_sha: str
    status: ThreadStatus
    status_history: list[StatusChange] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    iterations: list[Iteration] = field(default_factory=list)
    preview_sha: str | None = None
    preview_url: str | None = None
    pr_url: str | None = None

    def to_api(self) -> dict:
        return {
            "id": self.id, "createdAt": self.created_at,
            "targetSelector": self.target_selector, "targetLabel": self.target_label,
            "pageUrl": self.page_url, "baseSha": self.base_sha,
            "status": self.status.value,
            "statusHistory": [{"status": s.status.value, "at": s.at} for s in self.status_history],
            "comments": [c.to_api() for c in self.comments],
            "iterations": [i.to_api() for i in self.iterations],
            "previewSha": self.preview_sha, "previewUrl": self.preview_url,
            "prUrl": self.pr_url,
        }

