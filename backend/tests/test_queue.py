"""Durable task queue: atomic claims, max inflight, retries, DLQ, janitor."""

import asyncio

from backend.agent.queue import WorkerVanished
from backend.domain.models import new_id
from backend.agent.models import AgentTask, TaskState
from backend.tests.conftest import make_stack, wait_for

def task(stack, max_attempts=3):
    # agent_tasks.thread_id has a FK: every task needs a real thread row
    thread = stack.threads.create(target_selector="#t", target_label="t",
                                  page_url="/demo/profile", base_sha="mainsha")
    return AgentTask(id=new_id("task"), thread_id=thread.id,
                     comment_ids=["cmt_1"], max_attempts=max_attempts)

def run(coro):
    asyncio.run(coro)

# ---- repo-level claim semantics ---------------------------------------------

def test_claim_is_atomic_and_ordered():
    stack = make_stack()
    t1, t2 = task(stack), task(stack)
    stack.tasks.enqueue(t1)
    stack.tasks.enqueue(t2)
    claimed = stack.tasks.claim_next("w0", lease_ms=1000)
    assert claimed.id == t1.id                       # FIFO
    assert claimed.state == TaskState.RUNNING
    assert claimed.attempts == 1 and claimed.claimed_by == "w0"
    assert stack.tasks.claim_next("w1", lease_ms=1000).id == t2.id
    assert stack.tasks.claim_next("w2", lease_ms=1000) is None   # queue drained

def test_heartbeat_only_extends_own_lease():
    stack = make_stack()
    t = task(stack)
    stack.tasks.enqueue(t)
    stack.tasks.claim_next("w0", lease_ms=1000)
    assert stack.tasks.heartbeat(t.id, "w0", lease_ms=1000)
    assert not stack.tasks.heartbeat(t.id, "intruder", lease_ms=1000)

def test_fail_requeues_until_max_attempts_then_dlq():
    stack = make_stack()
    t = task(stack, max_attempts=2)
    stack.tasks.enqueue(t)
    for expected in (TaskState.QUEUED, TaskState.DLQ):
        stack.tasks.claim_next("w0", lease_ms=1000)
        assert stack.tasks.fail(t.id, "boom") == expected
    dlq = stack.tasks.get(t.id)
    assert dlq.state == TaskState.DLQ and dlq.last_error == "boom" and dlq.attempts == 2

def test_requeue_from_dlq_resets_attempts():
    stack = make_stack()
    t = task(stack, max_attempts=1)
    stack.tasks.enqueue(t)
    stack.tasks.claim_next("w0", lease_ms=1000)
    stack.tasks.fail(t.id, "boom")
    assert stack.tasks.requeue_from_dlq(t.id)
    replayed = stack.tasks.get(t.id)
    assert replayed.state == TaskState.QUEUED and replayed.attempts == 0
    assert not stack.tasks.requeue_from_dlq(t.id)    # only valid from DLQ

def test_cancel_queued_task_is_skipped_by_workers():
    stack = make_stack()
    t = task(stack)
    stack.tasks.enqueue(t)
    stack.tasks.cancel(t.id)
    assert stack.tasks.claim_next("w0", lease_ms=1000) is None

# ---- worker pool -------------------------------------------------------------

def test_max_inflight_respected():
    async def main():
        stack = make_stack(max_inflight=2)
        gate = asyncio.Event()
        started = []

        async def handler(t):
            started.append(t.id)
            await gate.wait()

        stack.queue.set_handler(handler)
        await stack.queue.start()
        for _ in range(4):
            stack.queue.submit(task(stack))
        await wait_for(lambda: len(started) == 2)
        await asyncio.sleep(0.1)
        assert len(started) == 2                     # third and fourth wait
        assert stack.queue.backlog == 2
        gate.set()
        await wait_for(lambda: len(started) == 4)
        await stack.queue.stop()
    run(main())

def test_worker_failure_retries_then_succeeds():
    async def main():
        stack = make_stack()
        outcomes = []
        stack.queue.on_outcome(lambda t, o, e: outcomes.append(o))
        calls = []

        async def handler(t):
            calls.append(t.attempts)
            if t.attempts < 2:
                raise RuntimeError("transient")

        stack.queue.set_handler(handler)
        await stack.queue.start()
        t = task(stack)
        stack.queue.submit(t)
        await wait_for(lambda: stack.tasks.get(t.id).state == TaskState.DONE)
        assert calls == [1, 2]
        assert outcomes == ["retry"]
        await stack.queue.stop()
    run(main())

def test_permanent_failure_lands_in_dlq():
    async def main():
        stack = make_stack()
        outcomes = []
        stack.queue.on_outcome(lambda t, o, e: outcomes.append(o))

        async def handler(t):
            raise RuntimeError("always")

        stack.queue.set_handler(handler)
        await stack.queue.start()
        t = task(stack, max_attempts=3)
        stack.queue.submit(t)
        await wait_for(lambda: stack.tasks.get(t.id).state == TaskState.DLQ)
        assert outcomes == ["retry", "retry", "dlq"]
        await stack.queue.stop()
    run(main())

def test_janitor_reclaims_vanished_worker():
    async def main():
        stack = make_stack(lease_s=0.1, reaper_interval_s=0.05)
        outcomes = []
        stack.queue.on_outcome(lambda t, o, e: outcomes.append(o))
        calls = []

        async def handler(t):
            calls.append(t.attempts)
            if t.attempts < 2:
                raise WorkerVanished("gone")         # dies without reporting

        stack.queue.set_handler(handler)
        await stack.queue.start()
        t = task(stack)
        stack.queue.submit(t)
        # row stays RUNNING until the lease expires, then the janitor requeues
        await wait_for(lambda: stack.tasks.get(t.id).state == TaskState.DONE, timeout=5)
        assert calls == [1, 2]
        assert "orphan_retry" in outcomes
        await stack.queue.stop()
    run(main())

def test_hung_worker_hits_deadline_and_retries():
    """The lease only catches dead workers; a hung one heartbeats forever and
    must be killed by the per-attempt deadline instead."""
    async def main():
        stack = make_stack(task_timeout_s=0.15, max_attempts=2)
        outcomes = []
        stack.queue.on_outcome(lambda t, o, e: outcomes.append((o, e)))
        calls = []

        async def handler(t):
            calls.append(t.attempts)
            if t.attempts < 2:
                await asyncio.sleep(60)              # hung, but heartbeating

        stack.queue.set_handler(handler)
        await stack.queue.start()
        t = task(stack, max_attempts=2)
        stack.queue.submit(t)
        await wait_for(lambda: stack.tasks.get(t.id).state == TaskState.DONE, timeout=5)
        assert calls == [1, 2]
        assert outcomes[0][0] == "retry" and "deadline" in outcomes[0][1]
        await stack.queue.stop()
    run(main())

def test_cancel_running_task_no_retry():
    async def main():
        stack = make_stack()
        gate = asyncio.Event()

        async def handler(t):
            gate.set()
            await asyncio.sleep(60)

        stack.queue.set_handler(handler)
        await stack.queue.start()
        t = task(stack)
        stack.queue.submit(t)
        await gate.wait()
        stack.queue.cancel(stack.tasks.get(t.id))
        await wait_for(lambda: stack.tasks.get(t.id).state == TaskState.CANCELLED)
        await asyncio.sleep(0.15)
        assert stack.tasks.get(t.id).state == TaskState.CANCELLED  # stays cancelled
        await stack.queue.stop()
    run(main())
