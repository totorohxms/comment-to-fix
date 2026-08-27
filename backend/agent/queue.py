"""Durable agent task queue: worker pool + janitor over the agent_tasks table.

Kafka-ish delivery semantics:
  - at-least-once: a claimed task whose worker dies is redelivered (the
    handler dedupes by idempotency key, making the effect exactly-once)
  - atomic claim: workers CAS QUEUED -> RUNNING (TaskRepo.claim_next)
  - lease + heartbeat: a running worker extends its lease; silence past the
    lease means the worker is gone
  - retry with max attempts, then DLQ
  - janitor loop: periodically reclaims orphaned (lease-expired) tasks —
    every 30 min in production; seconds here so the demo can show it

The queue rows live in SQLite, so the backlog survives restarts: QUEUED tasks
are picked up on boot, and RUNNING tasks from a dead process expire their
lease and get reclaimed by the janitor.
"""

import asyncio
import logging

from backend.db.repos import TaskRepo
from backend.agent.models import AgentTask, TaskState
from backend.observability import metrics

log = logging.getLogger("ctf.queue")

class WorkerVanished(Exception):
    """Raised by the (fake) agent to simulate a worker dying without reporting
    failure: the queue abandons the task silently — no fail(), no state change
    — leaving an orphaned RUNNING row for the janitor to find."""

class AgentTaskQueue:
    def __init__(self, tasks: TaskRepo, *, max_inflight: int = 2,
                 lease_s: float = 15, poll_s: float = 1.0,
                 reaper_interval_s: float = 30, task_timeout_s: float = 300):
        self.tasks = tasks
        self.max_inflight = max_inflight
        self.lease_ms = int(lease_s * 1000)
        self.poll_s = poll_s
        self.reaper_interval_s = reaper_interval_s
        # Hard deadline per attempt. The lease only catches *dead* workers —
        # a hung one heartbeats forever, so it needs an explicit timeout.
        self.task_timeout_s = task_timeout_s
        self._handler = None      # async (AgentTask) -> None
        self._on_outcome = None   # (AgentTask, outcome: str, error: str) -> None
        self._stopping = False    # disambiguates worker-shutdown from task-cancel
        self._loops: list[asyncio.Task] = []
        self._running: dict[str, asyncio.Task] = {}  # task.id -> asyncio task
        self._wake = asyncio.Event()

    def set_handler(self, handler) -> None:
        self._handler = handler

    def on_outcome(self, cb) -> None:
        """cb(task, outcome, error) with outcome in {'retry','dlq','orphan_retry','orphan_dlq'}."""
        self._on_outcome = cb

    async def start(self) -> None:
        self._stopping = False
        self._loops = [asyncio.create_task(self._worker(i)) for i in range(self.max_inflight)]
        self._loops.append(asyncio.create_task(self._janitor()))
        log.info("queue: %d workers, lease %.0fs, janitor every %.0fs",
                 self.max_inflight, self.lease_ms / 1000, self.reaper_interval_s)

    async def stop(self) -> None:
        # The flag disambiguates a racing pair of CancelledErrors: a worker
        # whose current run was cancelled at the same moment as shutdown would
        # otherwise swallow its own cancellation and loop forever.
        self._stopping = True
        for t in self._loops:
            t.cancel()
        await asyncio.gather(*self._loops, return_exceptions=True)
        self._loops = []

    def submit(self, task: AgentTask) -> None:
        self.tasks.enqueue(task)
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    # ---- task-state API — the queue owns task rows; callers (runner, routes)
    # go through these instead of touching TaskRepo directly.

    def active_task(self, thread_id: str) -> AgentTask | None:
        """The thread's live (queued or running) task, if any."""
        return self.tasks.active_for_thread(thread_id)

    def get_task(self, task_id: str) -> AgentTask | None:
        return self.tasks.get(task_id)

    def coalesce(self, task_id: str, comment_id: str) -> bool:
        """Fold a comment into a not-yet-started task (CAS on QUEUED)."""
        return self.tasks.coalesce_comment(task_id, comment_id)

    def requeue_from_dlq(self, task_id: str) -> AgentTask | None:
        """Human-initiated DLQ replay. Returns the requeued task, or None if
        the task isn't in the DLQ."""
        if not self.tasks.requeue_from_dlq(task_id):
            return None
        self._wake.set()
        return self.tasks.get(task_id)

    def covered_comment_ids(self, thread_id: str) -> set[str]:
        """Comment ids ever covered by any task of this thread (see TaskRepo)."""
        return self.tasks.covered_comment_ids(thread_id)

    def counts(self) -> dict:
        return self.tasks.counts()

    def list_dlq(self) -> list[AgentTask]:
        return self.tasks.list_by_state(TaskState.DLQ)

    def cancel(self, task: AgentTask) -> None:
        """Queued: tombstoned via CAS. Running: asyncio-cancel the handler
        (the worker then marks the row cancelled)."""
        self.tasks.cancel(task.id)
        run = self._running.get(task.id)
        if run:
            run.cancel()

    @property
    def running(self) -> bool:
        return bool(self._loops)

    @property
    def inflight(self) -> int:
        return len(self._running)

    @property
    def backlog(self) -> int:
        return self.tasks.counts().get("queued", 0)

    # ---- loops ---------------------------------------------------------------

    async def _worker(self, index: int) -> None:
        worker_id = f"worker-{index}"
        while True:
            task = self.tasks.claim_next(worker_id, self.lease_ms)
            if task is None:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.poll_s)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                continue

            run = asyncio.create_task(self._handler(task))
            hb = asyncio.create_task(self._heartbeat(task.id, worker_id))
            self._running[task.id] = run

            timed_out = False
            def _deadline():
                nonlocal timed_out
                timed_out = True
                run.cancel()
            killer = asyncio.get_running_loop().call_later(self.task_timeout_s, _deadline)

            metrics.gauge("queue.inflight", len(self._running))
            try:
                await run
                self.tasks.complete(task.id)
                metrics.incr("queue.task.done")
            except asyncio.CancelledError:
                if timed_out:
                    # Hung worker: treat like any failed attempt (retry or DLQ).
                    log.warning("task %s exceeded the %.0fs deadline", task.id, self.task_timeout_s)
                    metrics.incr("queue.task.deadline")
                    new_state = self.tasks.fail(
                        task.id, f"task exceeded the {self.task_timeout_s:.0f}s deadline")
                    outcome = "retry" if new_state == TaskState.QUEUED else "dlq"
                    metrics.incr(f"queue.task.{outcome}")
                    if self._on_outcome:
                        self._on_outcome(self.tasks.get(task.id), outcome, "task deadline exceeded")
                    if outcome == "retry":
                        self._wake.set()
                elif run.cancelled() and not self._stopping:
                    self.tasks.cancel(task.id)  # interrupted on purpose; no retry
                    metrics.incr("queue.task.cancelled")
                else:  # the worker itself was cancelled (shutdown)
                    run.cancel()
                    raise
            except WorkerVanished:
                # Simulated crash: walk away without reporting. The row stays
                # RUNNING with a decaying lease until the janitor reclaims it.
                log.warning("worker %s vanished mid-task %s", worker_id, task.id)
            except Exception as e:
                log.warning("task %s attempt failed: %s", task.id, e)
                new_state = self.tasks.fail(task.id, str(e))
                outcome = "retry" if new_state == TaskState.QUEUED else "dlq"
                metrics.incr(f"queue.task.{outcome}")
                if self._on_outcome:
                    self._on_outcome(self.tasks.get(task.id), outcome, str(e))
                if outcome == "retry":
                    self._wake.set()
            finally:
                killer.cancel()
                hb.cancel()
                self._running.pop(task.id, None)
                metrics.gauge("queue.inflight", len(self._running))
                metrics.gauge("queue.backlog", self.backlog)

    async def _heartbeat(self, task_id: str, worker_id: str) -> None:
        interval = max(self.lease_ms / 3000, 0.5)
        while True:
            await asyncio.sleep(interval)
            if not self.tasks.heartbeat(task_id, worker_id, self.lease_ms):
                return  # no longer ours (reclaimed or finished)

    async def _janitor(self) -> None:
        """Reclaim orphaned tasks. Production cadence ~30 min; configured in
        seconds here so the demo can show recovery live."""
        while True:
            await asyncio.sleep(self.reaper_interval_s)
            for task, new_state in self.tasks.reap_expired():
                outcome = "orphan_retry" if new_state == TaskState.QUEUED else "orphan_dlq"
                metrics.incr(f"queue.task.{outcome}")
                log.warning("janitor: task %s lease expired -> %s", task.id, new_state.value)
                if self._on_outcome:
                    self._on_outcome(task, outcome, "lease expired — worker lost")
                if new_state == TaskState.QUEUED:
                    self._wake.set()
