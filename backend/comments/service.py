"""Comment domain logic: append-only writes, thread creation, and edge rate
limiting. Input validation lives in utils.py (limits in constants.py); the API
layer (routes/) calls into here."""

import time
from collections import defaultdict, deque

from backend.comments.utils import (
    CommentError, validate_capture, validate_target, validate_text,
)
from backend.db.repos import CommentRepo, MetaRepo, ThreadRepo
from backend.domain.models import CLOSED_STATUSES, Comment, Thread, User

class SlidingWindowRateLimiter:
    """Per-user flood protection at the edge (the queue's max_inflight protects
    the workers; this protects the API from one user spamming comments)."""

    def __init__(self, max_events: int, window_s: float):
        self.max_events = max_events
        self.window_s = window_s
        self._events: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._events[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if len(q) >= self.max_events:
            return False
        q.append(now)
        return True

class CommentService:
    def __init__(self, threads: ThreadRepo, comments: CommentRepo, meta: MetaRepo,
                 limiter: SlidingWindowRateLimiter):
        self.threads = threads
        self.comments = comments
        self.meta = meta
        self.limiter = limiter

    def post_comment(self, user: User, body: dict) -> tuple[Thread, Comment]:
        """Validate + append a user comment; creates the thread when body has
        no threadId (a new thread needs a target element)."""
        if not self.limiter.allow(user.id):
            raise CommentError(429, "Too many comments — give the agent a moment to catch up.")

        text = validate_text(body.get("text"))
        capture = validate_capture(body.get("capture"))

        thread_id = body.get("threadId")
        if thread_id is not None and not isinstance(thread_id, str):
            raise CommentError(400, "threadId must be a string")
        thread = self.threads.get(thread_id) if thread_id else None
        if thread_id and not thread:
            raise CommentError(404, "thread not found")
        if thread and thread.status in CLOSED_STATUSES:
            # Reject rather than accept-and-scold: an append-only log should
            # not accumulate comments nothing will ever act on. The usual
            # cause is a stale preview tab from before the merge.
            raise CommentError(409, (
                f"This thread is closed ({thread.status.value}) — the preview you're "
                "looking at is stale. Refresh the live site and start a new comment "
                "thread for further changes."))

        if not thread:
            target = validate_target(body.get("target"))
            base_sha = (capture or {}).get("sha") or self.meta.get("main_sha")
            # No new threads off a stale preview: a fix branched from an
            # already-merged (or cancelled) preview would target a base that
            # no longer exists. Force a refresh to production instead.
            owner = self.threads.thread_for_preview_sha(base_sha)
            if owner and owner.status in CLOSED_STATUSES:
                raise CommentError(409, (
                    f"This preview is stale — its thread is closed ({owner.status.value}) "
                    "and the fix has already shipped or been dropped. Refresh the live "
                    "site and comment there."))
            thread = self.threads.create(
                target_selector=target["selector"],
                target_label=target.get("label") or target["selector"],
                page_url=(capture or {}).get("url") or "/demo/profile.html",
                base_sha=base_sha,
            )

        comment = self.comments.append(thread.id, user_id=user.id, text=text, capture=capture)
        thread.comments.append(comment)
        return thread, comment
