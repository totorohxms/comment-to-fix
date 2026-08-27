"""Approval safety (reviewed-sha check), merge ordering, SSE replay,
crash-recovery exactly-once."""

import asyncio

from backend.domain.models import ThreadStatus, new_id
from backend.agent.models import AgentTask, TaskState
from backend.pubsub import StatusBroker
from backend.tests.conftest import evan, make_stack, post, wait_for

def run(coro):
    asyncio.run(coro)

async def until_preview(stack, tid, timeout=5.0):
    await wait_for(lambda: stack.threads.get(tid).status == ThreadStatus.PREVIEW_READY,
                   timeout=timeout)

# ---- issue 1: approve must name the reviewed sha -----------------------------

def test_approve_rejects_stale_or_missing_sha():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "hide this", selector="#a")
        await until_preview(stack, thread.id)
        t = stack.threads.get(thread.id)

        stale = stack.runner.approve(t, evan(), "0ld5ha1")
        assert "error" in stale and "no longer the latest" in stale["error"]
        missing = stack.runner.approve(t, evan(), None)
        assert "error" in missing
        assert stack.threads.get(t.id).status == ThreadStatus.PREVIEW_READY  # untouched

        assert stack.runner.approve(t, evan(), t.preview_sha) == {"ok": True}
        await stack.queue.stop()
    run(main())

def test_approve_rejects_sha_superseded_by_new_iteration():
    """The exact TOCTOU: approver's panel shows preview 1, a follow-up ships
    preview 2, approve(preview 1) must fail."""
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "make it blue", selector="#a")
        await until_preview(stack, thread.id)
        sha1 = stack.threads.get(thread.id).preview_sha

        post(stack, "font size 18", thread_id=thread.id, capture_sha=sha1)
        await wait_for(lambda: len(stack.threads.get(thread.id).iterations) == 2)
        await until_preview(stack, thread.id)

        t = stack.threads.get(thread.id)
        assert t.preview_sha != sha1
        rejected = stack.runner.approve(t, evan(), sha1)   # reviewed the old one
        assert "error" in rejected
        assert stack.runner.approve(t, evan(), t.preview_sha) == {"ok": True}
        await stack.queue.stop()
    run(main())

# ---- issue 2: MERGED only after merge() returned -----------------------------

def test_merged_status_trails_the_merge_call():
    async def main():
        stack = make_stack()
        observed = {}

        real_prs = stack.agent.prs
        class OrderCheckingPRs:
            async def open_pr(self, branch, title):
                return await real_prs.open_pr(branch, title)
            async def await_ci_and_review(self, pr_url):
                await real_prs.await_ci_and_review(pr_url)
            async def merge(self, pr_url):
                observed["status_at_merge_call"] = stack.threads.get(observed["tid"]).status
                await real_prs.merge(pr_url)
        stack.agent.prs = OrderCheckingPRs()

        await stack.queue.start()
        thread, _ = post(stack, "hide this", selector="#a")
        await until_preview(stack, thread.id)
        observed["tid"] = thread.id
        t = stack.threads.get(thread.id)
        stack.runner.approve(t, evan(), t.preview_sha)
        await wait_for(lambda: stack.threads.get(thread.id).status == ThreadStatus.DONE)

        # when merge() was invoked, the thread must NOT have claimed MERGED yet
        assert observed["status_at_merge_call"] == ThreadStatus.PR_OPEN
        await stack.queue.stop()
    run(main())

# ---- issue 3: SSE event ids + replay ------------------------------------------

def test_broker_assigns_ids_and_replays_gap():
    broker = StatusBroker(history=10)
    for i in range(5):
        broker.publish({"n": i})
    replay = broker.replay_since(2)
    assert [e["n"] for e in replay] == [2, 3, 4]
    assert [e["eventId"] for e in replay] == [3, 4, 5]
    assert broker.replay_since(5) == []                # fully caught up

def test_broker_replay_unverifiable_gap_returns_none():
    broker = StatusBroker(history=3)
    for i in range(10):
        broker.publish({"n": i})                        # ids 1..10; ring holds 8..10
    assert broker.replay_since(4) is None               # evicted -> resync
    assert [e["eventId"] for e in broker.replay_since(7)] == [8, 9, 10]
    fresh = StatusBroker()
    assert fresh.replay_since(42) is None               # fresh process -> resync
    assert fresh.replay_since(0) == []                  # brand-new client is fine

# ---- issue 4: crash-recovery exactly-once -------------------------------------

def test_redelivery_repairs_status_without_redoing_work():
    """Crash after the iteration row landed but before preview_ready was
    published: redelivery must repair the status, not deploy again."""
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, comment = post(stack, "make it blue", selector="#a")
        await until_preview(stack, thread.id)
        n_iterations = len(stack.threads.get(thread.id).iterations)

        # simulate the crash window: status rolled back, task redelivered
        stack.threads.set_status(thread.id, ThreadStatus.DEPLOYING)
        redelivered = AgentTask(id=new_id("task"), thread_id=thread.id,
                                comment_ids=[comment.id])
        await stack.runner._execute(redelivered)

        t = stack.threads.get(thread.id)
        assert t.status == ThreadStatus.PREVIEW_READY       # repaired
        assert len(t.iterations) == n_iterations            # nothing redone
        await stack.queue.stop()
    run(main())

def test_recover_resumes_pr_flow_frozen_by_crash():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "hide this", selector="#a")
        await until_preview(stack, thread.id)
        t = stack.threads.get(thread.id)
        # simulate: approve CAS'd to PR_OPEN, then the process died before
        # the flow recorded a PR
        assert stack.threads.transition(t.id, ThreadStatus.PREVIEW_READY,
                                        ThreadStatus.PR_OPEN)

        assert stack.runner.recover() == 1
        await wait_for(lambda: stack.threads.get(t.id).status == ThreadStatus.DONE)
        done = stack.threads.get(t.id)
        assert done.pr_url                                  # PR recorded exactly once
        assert sum(1 for c in done.comments
                   if c.system and "PR merged" in c.text) == 1
        await stack.queue.stop()
    run(main())
