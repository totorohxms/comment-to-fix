"""@agent invocation gating + approver permissions."""

import asyncio

import pytest

from backend.comments.utils import mentions_agent
from backend.domain.models import ThreadStatus
from backend.agent.models import TaskState
from backend.tests.conftest import dana, evan, make_stack, post, wait_for

def run(coro):
    asyncio.run(coro)

# ---- mention parsing ---------------------------------------------------------

def test_mentions_agent_matching():
    assert mentions_agent("@agent fix this")
    assert mentions_agent("please @Agent make it blue")   # case-insensitive
    assert mentions_agent("hey (@agent) look")
    assert not mentions_agent("the agent should fix this")  # no bare word match
    assert not mentions_agent("@agents are cool")           # word boundary
    assert not mentions_agent("mail me at x@agent.com")     # not a mention

# ---- collaboration comments never touch the agent ----------------------------

def test_plain_comment_creates_discussion_thread_without_task():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "does this button look right to you @evan?",
                         selector="#a", agent=False)
        await asyncio.sleep(0.2)
        t = stack.threads.get(thread.id)
        assert t.status == ThreadStatus.OPEN            # discussion, not triggered
        assert stack.tasks.active_for_thread(t.id) is None
        assert t.iterations == []
        await stack.queue.stop()
    run(main())

def test_agent_mention_on_open_thread_launches_task():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "thoughts on this?", selector="#a", agent=False)
        post(stack, "make it blue", thread_id=thread.id)      # @agent prefixed
        await wait_for(lambda: stack.threads.get(thread.id).status == ThreadStatus.PREVIEW_READY)
        t = stack.threads.get(thread.id)
        # only the @agent comment is addressed by the iteration
        assert len(t.iterations[0].comment_ids) == 1
        await stack.queue.stop()
    run(main())

def test_plain_comment_mid_flight_does_not_interrupt_or_queue():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "style is off", selector="#a")
        await wait_for(lambda: stack.threads.get(thread.id).status == ThreadStatus.ANALYZING)
        post(stack, "looks promising so far!", thread_id=thread.id, agent=False)
        await wait_for(lambda: stack.threads.get(thread.id).status == ThreadStatus.PREVIEW_READY)
        t = stack.threads.get(thread.id)
        assert len(t.iterations) == 1                       # no interrupt, no restart
        assert len(t.iterations[0].comment_ids) == 1        # not coalesced in
        assert not any("interrupted" in c.text for c in t.comments if c.system)
        # and no second iteration gets drained for it afterwards
        await asyncio.sleep(0.3)
        assert len(stack.threads.get(thread.id).iterations) == 1
        await stack.queue.stop()
    run(main())

def test_preview_comment_tags_approver_group():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "make it blue", selector="#a")
        await wait_for(lambda: stack.threads.get(thread.id).status == ThreadStatus.PREVIEW_READY)
        deployed = next(c.text for c in stack.threads.get(thread.id).comments
                        if c.system and "Fix deployed" in c.text)
        assert "@evan" in deployed and "approval needed" in deployed
        await stack.queue.stop()
    run(main())

# ---- approver permission -----------------------------------------------------

def test_commenter_cannot_approve_approver_can():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "hide this", selector="#a")
        await wait_for(lambda: stack.threads.get(thread.id).status == ThreadStatus.PREVIEW_READY)

        t = stack.threads.get(thread.id)
        denied = stack.runner.approve(t, dana(), t.preview_sha)  # designer: comment only
        assert "error" in denied and "approver" in denied["error"]
        assert stack.threads.get(t.id).status == ThreadStatus.PREVIEW_READY

        assert stack.runner.approve(t, evan(), t.preview_sha) == {"ok": True}
        await stack.queue.stop()
    run(main())
