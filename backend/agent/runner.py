"""AgentRunner: decides and narrates the thread lifecycle.

Ownership contract (see also agent.py and queue.py):
  - AgentRunner (this) DECIDES and NARRATES: when to start a task, base-sha
    resolution, interrupt-vs-coalesce-vs-queue, records (iteration rows),
    system comments, status transitions + pubsub, DLQ replay, recovery.
  - AgentTaskQueue DELIVERS: claims, leases, retries, DLQ, the active task —
    the runner never touches TaskRepo directly.
  - Agent EXECUTES: launcher + side effects; returns IterationOutcome.

Policy summary:
  - one active task per thread (asked of the queue, so it survives restarts)
  - interrupt policy on a new @agent comment:
      task QUEUED (not started)        -> coalesce the comment into the task
      task RUNNING, before the cutoff  -> cancel + combine + resubmit
      task RUNNING, past the cutoff    -> stored comment drains into the next
                                          iteration off the fresh preview
  - exactly-once effect over at-least-once delivery: dedupe on the task's
    idempotency key; redelivery repairs derived state instead of redoing work
"""

import asyncio
import logging

from backend.agent.agent import Agent
from backend.agent.queue import AgentTaskQueue
from backend.comments.utils import mentions_agent
from backend.db.repos import CommentRepo, ThreadRepo, UserRepo
from backend.domain.models import (
    CLOSED_STATUSES, Comment, INTERRUPTIBLE_STATUSES, IN_FLIGHT_STATUSES,
    Iteration, Thread, ThreadStatus, User, new_id,
)
from backend.agent.models import (
    AgentTask, FixTaskSpec, IterationOutcome, TaskPhase, TaskState,
)
from backend.observability import track
from backend.pubsub import StatusBroker

log = logging.getLogger("ctf.runner")

# How executor progress phases surface on the thread.
PHASE_STATUS = {
    TaskPhase.ANALYZING: ThreadStatus.ANALYZING,
    TaskPhase.CODING: ThreadStatus.CODING,
    TaskPhase.DEPLOYING: ThreadStatus.DEPLOYING,
}

class AgentRunner:
    def __init__(self, agent: Agent, queue: AgentTaskQueue, threads: ThreadRepo,
                 comments: CommentRepo, users: UserRepo, broker: StatusBroker,
                 max_attempts: int = 3):
        self.agent = agent
        self.queue = queue
        self.threads = threads
        self.comments = comments
        self.users = users
        self.broker = broker
        self.max_attempts = max_attempts
        queue.set_handler(self._execute)
        queue.on_outcome(self._on_task_outcome)

    # ---- state publishing ----------------------------------------------------

    def publish_state(self, thread_id: str, status: ThreadStatus | None = None,
                      note: str | None = None) -> None:
        if status is not None:
            self.threads.set_status(thread_id, status)
        thread = self.threads.get(thread_id)
        self.broker.publish({
            "type": "thread.update", "threadId": thread_id,
            "status": thread.status.value, "note": note, "thread": thread.to_api(),
        })

    # ---- entry point: a user comment landed ----------------------------------

    def on_comment(self, thread: Thread, comment: Comment) -> None:
        if not mentions_agent(comment.text):
            # Human collaboration ("@evan does this look right?"): store and
            # broadcast, but never spawn/interrupt/join an agent run.
            self.publish_state(thread.id)
            return

        if thread.status in CLOSED_STATUSES:
            self._system(thread.id, "🤖 This thread already has a PR. "
                                    "Open a new comment thread for further changes.")
            self.publish_state(thread.id)
            return

        # Only honor the active task while the thread itself is in flight: a
        # task can linger in RUNNING for a moment after the preview landed
        # (completion is recorded when its worker unwinds) — a comment in that
        # window must start the next iteration, not vanish into follow-ups.
        task = self.queue.active_task(thread.id)
        if task and thread.status not in IN_FLIGHT_STATUSES:
            task = None

        if task and task.state == TaskState.QUEUED:
            # Cheapest interrupt there is: the task hasn't started, edit it.
            # CAS-guarded — if it started racing us, fall through to RUNNING.
            if self.queue.coalesce(task.id, comment.id):
                self._system(thread.id, "🤖 Task is still waiting for a free agent — "
                                        "added your comment to it.")
                self.publish_state(thread.id)
                return
            task = self.queue.get_task(task.id)

        if task and task.state == TaskState.RUNNING:
            if thread.status in INTERRUPTIBLE_STATUSES:
                self.queue.cancel(task)
                combined = list(dict.fromkeys([*task.comment_ids, comment.id]))
                self._system(thread.id, "🤖 Task was still analyzing — interrupted it "
                                        "and combined your new comment into one task.")
                self._submit(thread.id, combined)
            else:
                # Past the cutoff (code change in flight): never interrupt.
                # The comment is already stored; the post-iteration drain picks
                # up every comment no iteration has addressed (db-derived, so
                # the intent survives a process restart).
                self._system(thread.id, "🤖 Code change already in flight — not "
                                        "interrupting. Your comment is queued for the next "
                                        "iteration (it will branch off the upcoming preview).")
                self.publish_state(thread.id)
            return

        # No active task: fresh thread, iterating on a preview, or retry-after-DLQ.
        self._submit(thread.id, [comment.id])

    # ---- approval -> PR lifecycle --------------------------------------------

    def approve(self, thread: Thread, user: User, reviewed_sha: str | None) -> dict:
        # Only the approver group (engineers) can turn a preview into a PR —
        # the route enforces this too; this is defense in depth.
        if not user.can_approve:
            return {"error": f"{user.name} cannot approve — only the engineering "
                             "(approver) group can open a PR"}
        # The approval must name the preview the approver actually reviewed:
        # a queued follow-up can deploy a newer preview while their panel is
        # open, and approving blind would ship a sha nobody looked at.
        current = self.threads.get(thread.id)
        if not reviewed_sha or reviewed_sha != current.preview_sha:
            return {"error": (
                f"The preview you reviewed (`{reviewed_sha or 'unknown'}`) is no longer "
                f"the latest (`{current.preview_sha}`) — check the newest preview, "
                "then approve again.")}
        # CAS the status so a double-click (or two users approving at once)
        # can never launch two PR flows.
        if not self.threads.transition(thread.id, ThreadStatus.PREVIEW_READY,
                                       ThreadStatus.PR_OPEN):
            return {"error": f"Cannot approve while status is "
                             f"{self.threads.status(thread.id).value}"}
        asyncio.get_running_loop().create_task(self._pr_flow(thread.id, user))
        return {"ok": True}

    @track("agent.pr_flow")
    async def _pr_flow(self, thread_id: str, approved_by: User) -> None:
        # Status is already PR_OPEN — approve CAS'd it, which is what makes a
        # double approve impossible.
        pr_url = await self.agent.open_pr(thread_id)
        self.threads.set_pr_url(thread_id, pr_url)
        thread = self.threads.get(thread_id)
        # The PR diff is original base -> approved preview; the intermediate
        # preview shas are history, not part of the diff.
        lineage = " → ".join([f"`{thread.base_sha}`", *(f"`{i.sha}`" for i in thread.iterations)])
        self._system(thread_id, (
            f"🤖 {approved_by.name} approved preview `{thread.preview_sha}`. "
            f"PR opened: {pr_url}\n"
            f"Diff: `{thread.base_sha}...{thread.preview_sha}` "
            f"({len(thread.iterations)} iteration{'s' if len(thread.iterations) != 1 else ''}: {lineage})\n"
            "Running CI + requesting review."))
        self.publish_state(thread_id)
        await self._ci_merge_rollout(thread_id, pr_url)

    async def _ci_merge_rollout(self, thread_id: str, pr_url: str) -> None:
        # Status trails the side effect, never leads it: MERGED is only
        # published after merge_pr() actually returned.
        await self.agent.await_ci_and_review(pr_url)
        self._system(thread_id, "🤖 CI green, review approved — merging.")
        self.publish_state(thread_id)

        await self.agent.merge_pr(pr_url)
        self._system(thread_id, "🤖 PR merged.")
        self.publish_state(thread_id, ThreadStatus.MERGED)

        self._system(thread_id, "🤖 Fix rolled out to production. Thread resolved. 🎉")
        self.publish_state(thread_id, ThreadStatus.DONE)

    async def resume_pr_flow(self, thread_id: str) -> None:
        """Boot recovery for a PR flow interrupted by a crash — the flow is an
        in-process await chain, so a dead process leaves the thread frozen at
        pr_open/merged. The approve CAS guarantees no *second* flow ever
        started; this resumes the one that died. (The real system replaces the
        await chain with CI/merge webhooks, making this a no-op.)"""
        thread = self.threads.get(thread_id)
        if thread.status == ThreadStatus.PR_OPEN:
            pr_url = thread.pr_url
            if not pr_url:
                # Died before the PR was recorded. The branch name is
                # deterministic per thread, so a real PR service would treat
                # this as idempotent re-creation of the same PR.
                pr_url = await self.agent.open_pr(thread_id)
                self.threads.set_pr_url(thread_id, pr_url)
            self._system(thread_id, "🤖 Resumed after a restart — continuing CI + review.")
            self.publish_state(thread_id)
            await self._ci_merge_rollout(thread_id, pr_url)
        elif thread.status == ThreadStatus.MERGED:
            self._system(thread_id, "🤖 Fix rolled out to production. Thread resolved. 🎉")
            self.publish_state(thread_id, ThreadStatus.DONE)

    # ---- DLQ replay ----------------------------------------------------------

    def requeue_from_dlq(self, task_id: str) -> bool:
        task = self.queue.requeue_from_dlq(task_id)
        if not task:
            return False
        self._system(task.thread_id, "🤖 Task replayed from the dead-letter queue.")
        self.threads.set_status(task.thread_id, ThreadStatus.TRIGGERED)
        self.publish_state(task.thread_id)
        return True

    # ---- executing one task --------------------------------------------------

    async def _execute(self, task: AgentTask) -> None:
        """Worker-pool handler. Delivery is at-least-once; the dedup below
        makes the effect exactly-once."""
        thread = self.threads.get(task.thread_id)
        already_applied = any(task.idempotency_key in it.comment_ids
                              for it in thread.iterations)
        if already_applied:
            # Redelivery after a crash: the iteration row (the linearization
            # point) exists, so the work landed — repair derived state that
            # the crash may have left behind instead of redoing anything.
            if thread.status in IN_FLIGHT_STATUSES:
                self.publish_state(task.thread_id, ThreadStatus.PREVIEW_READY,
                                   "recovered after redelivery")
        else:
            parent_sha = self._resolve_parent_sha(thread, task)
            log.info("iteration start thread=%s task=%s attempt=%d base=%s",
                     thread.id, task.id, task.attempts, parent_sha)
            spec = FixTaskSpec(
                thread_id=thread.id,
                target_selector=thread.target_selector,
                target_label=thread.target_label,
                base_sha=parent_sha,
                comments=[c for c in thread.comments if c.id in task.comment_ids],
                attempt=task.attempts,
                max_attempts=task.max_attempts,
            )
            outcome = await self.agent.run_iteration(
                spec,
                progress=lambda phase, note: self.publish_state(
                    thread.id, PHASE_STATUS[phase], note))
            self._record_iteration(thread, task, outcome)
        self._drain_pending(task.thread_id)

    def _resolve_parent_sha(self, thread: Thread, task: AgentTask) -> str:
        """Which sha does this iteration's worktree branch off?

        The version the user actually verified wins: if the newest captured
        comment in the task was made on one of this thread's preview
        deployments, that preview is the base — even if newer previews exist
        (commenting on an older preview is how a user rolls back a bad
        iteration; the fork is announced in the thread). A capture from
        anywhere else (the production page, another page) means the user
        didn't pick a version, so iterate from the thread's tip.
        """
        tip = thread.preview_sha or thread.base_sha
        known = {it.sha for it in thread.iterations}
        user_comments = [c for c in thread.comments
                         if c.id in task.comment_ids and not c.system]
        for c in reversed(user_comments):
            cap_sha = (c.capture or {}).get("sha")
            if not cap_sha:
                continue
            if cap_sha in known and cap_sha != tip:
                self._system(thread.id, (
                    f"🤖 You commented on preview `{cap_sha}`, so this fix branches off "
                    f"that version — superseding `{tip}` as the tip. Earlier previews "
                    "stay in the history."))
                return cap_sha
            break  # newest captured sha is the tip or production: iterate from tip
        return tip

    def _record_iteration(self, thread: Thread, task: AgentTask,
                          outcome: IterationOutcome) -> None:
        self.threads.add_iteration(thread.id, Iteration(
            sha=outcome.preview_sha, parent_sha=outcome.parent_sha,
            summary=outcome.patch.summary, comment_ids=task.comment_ids))
        # Auto-tag the approver group — only they can turn this into a PR.
        approvers = " ".join(f"@{u.id}" for u in self.users.list() if u.can_approve)
        self._system(thread.id, (
            f"🤖 Fix deployed to preview `{outcome.preview_sha}` "
            f"(branch off `{outcome.parent_sha}`).\n"
            f"{outcome.patch.summary}\n"
            f"Analysis: {outcome.analysis}\n"
            f"Check it and keep commenting with @agent to iterate. "
            f"cc {approvers or '(no approvers configured)'} — approval needed to open a PR."))
        self.publish_state(thread.id, ThreadStatus.PREVIEW_READY,
                           f"Preview deployed at /preview/{outcome.preview_sha}")

    # ---- pending-comment drains ----------------------------------------------

    def _pending_comment_ids(self, thread: Thread) -> list[str]:
        """@agent comments no iteration has addressed and no live task covers —
        derived from the db, so it survives restarts and lost tracking. Plain
        (collaboration) comments never enter agent bookkeeping."""
        addressed = {cid for it in thread.iterations for cid in it.comment_ids}
        active = self.queue.active_task(thread.id)
        covered = set(active.comment_ids) if active else set()
        return [c.id for c in thread.comments
                if not c.system and mentions_agent(c.text)
                and c.id not in addressed and c.id not in covered]

    def _drain_pending(self, thread_id: str) -> None:
        thread = self.threads.get(thread_id)
        if thread.status in CLOSED_STATUSES or thread.status == ThreadStatus.FAILED:
            return
        pending = self._pending_comment_ids(thread)
        if pending:
            self._system(thread_id, "🤖 Starting next iteration for comments "
                                    "queued during the previous run.")
            self._submit(thread_id, pending)

    def recover(self) -> int:
        """Boot sweep: restore in-process intent lost to a restart. Queue rows
        survive on their own; this (a) resubmits drains for threads whose
        pending comments never got a task, and (b) resumes PR flows that were
        awaited in the dead process (frozen at pr_open/merged). Returns the
        number of threads acted on."""
        recovered = 0
        for thread in self.threads.list():
            if thread.status in (ThreadStatus.PR_OPEN, ThreadStatus.MERGED):
                log.info("recover: resuming PR flow for thread=%s (%s)",
                         thread.id, thread.status.value)
                asyncio.get_running_loop().create_task(self.resume_pr_flow(thread.id))
                recovered += 1
                continue
            if thread.status in CLOSED_STATUSES or thread.status == ThreadStatus.FAILED:
                continue
            if self.queue.active_task(thread.id):
                continue  # its queued/running task will drain on completion
            if self._pending_comment_ids(thread):
                self._drain_pending(thread.id)
                recovered += 1
        return recovered

    # ---- internals -----------------------------------------------------------

    def _submit(self, thread_id: str, comment_ids: list[str]) -> None:
        existing = self.queue.active_task(thread_id)
        if existing and existing.idempotency_key == comment_ids[-1]:
            return  # duplicate trigger for the same newest comment
        task = AgentTask(id=new_id("task"), thread_id=thread_id,
                         comment_ids=comment_ids, max_attempts=self.max_attempts)
        log.info("submit task=%s thread=%s comments=%d", task.id, thread_id, len(comment_ids))
        self.threads.set_status(thread_id, ThreadStatus.TRIGGERED)
        self.publish_state(
            thread_id,
            note=f"Task queued ({self.queue.inflight}/{self.queue.max_inflight} agents busy, "
                 f"{self.queue.backlog} waiting).")
        self.queue.submit(task)

    def _on_task_outcome(self, task: AgentTask, outcome: str, error: str) -> None:
        """Queue callback for retry/DLQ/orphan events — surface them on the thread."""
        attempt = f"attempt {task.attempts}/{task.max_attempts}"
        if outcome == "retry":
            self._system(task.thread_id, f"🤖 Agent worker failed ({attempt}): {error}. Retrying.")
            self.publish_state(task.thread_id, ThreadStatus.TRIGGERED)
        elif outcome == "orphan_retry":
            self._system(task.thread_id, f"🤖 Janitor: worker went silent ({attempt}) — "
                                         "lease expired, task reclaimed and requeued.")
            self.publish_state(task.thread_id, ThreadStatus.TRIGGERED)
        else:  # dlq / orphan_dlq
            log.error("task %s dead-lettered after %d attempts: %s",
                      task.id, task.attempts, error)
            why = "worker went silent" if outcome == "orphan_dlq" else error
            self._system(task.thread_id, f"🤖 Task moved to the dead-letter queue after "
                                         f"{task.attempts} attempts ({why}). "
                                         "A human should take a look; it can be replayed from the DLQ.")
            self.publish_state(task.thread_id, ThreadStatus.FAILED)

    def _system(self, thread_id: str, text: str) -> None:
        self.comments.append(thread_id, user_id="agent", text=text, system=True)
