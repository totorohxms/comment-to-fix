"""End-to-end agent flow: comment -> lifecycle -> preview -> iterate -> PR.
Runs the real stack (queue workers, launcher, fakes) at millisecond timings."""

import asyncio

from backend.domain.models import ThreadStatus
from backend.agent.models import TaskState
from backend.tests.conftest import MAIN_SHA, dana, evan, make_stack, post, wait_for

def run(coro):
    asyncio.run(coro)

def status(stack, tid):
    return stack.threads.get(tid).status

async def until_status(stack, tid, wanted, timeout=5.0):
    await wait_for(lambda: status(stack, tid) == wanted, timeout=timeout)

def test_happy_path_reaches_preview_with_patch_and_analysis():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "this button style is not right", selector="#btn-edit")
        await until_status(stack, thread.id, ThreadStatus.PREVIEW_READY)

        t = stack.threads.get(thread.id)
        assert len(t.iterations) == 1
        it = t.iterations[0]
        assert it.parent_sha == MAIN_SHA
        assert t.preview_sha == it.sha
        patches = stack.patches.get(it.sha)
        assert patches and "#btn-edit" in patches[0].css
        sys_texts = [c.text for c in t.comments if c.system]
        assert any("Analysis:" in s for s in sys_texts)
        # lifecycle order recorded
        seen = [h.status for h in t.status_history]
        for a, b in zip(seen, seen[1:]):
            assert (a, b) != (ThreadStatus.PREVIEW_READY, ThreadStatus.TRIGGERED)
        assert seen[0] == ThreadStatus.OPEN and seen[-1] == ThreadStatus.PREVIEW_READY
        await stack.queue.stop()
    run(main())

def test_iteration_branches_off_previous_preview():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "make it blue", selector="#a")
        await until_status(stack, thread.id, ThreadStatus.PREVIEW_READY)
        sha1 = stack.threads.get(thread.id).preview_sha

        post(stack, "font size 18", thread_id=thread.id, capture_sha=sha1)
        await wait_for(lambda: len(stack.threads.get(thread.id).iterations) == 2)
        await until_status(stack, thread.id, ThreadStatus.PREVIEW_READY)
        it2 = stack.threads.get(thread.id).iterations[1]
        assert it2.parent_sha == sha1
        # child preview carries ancestor patches
        assert len(stack.patches.get(it2.sha)) == 2
        await stack.queue.stop()
    run(main())

def test_comment_on_older_preview_forks_from_it():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "make it blue", selector="#a")
        await until_status(stack, thread.id, ThreadStatus.PREVIEW_READY)
        sha1 = stack.threads.get(thread.id).preview_sha
        post(stack, "font size 18", thread_id=thread.id, capture_sha=sha1)
        await wait_for(lambda: len(stack.threads.get(thread.id).iterations) == 2)
        await until_status(stack, thread.id, ThreadStatus.PREVIEW_READY)

        # user goes back to the FIRST preview and comments there
        post(stack, "make it rounded", thread_id=thread.id, capture_sha=sha1)
        await wait_for(lambda: len(stack.threads.get(thread.id).iterations) == 3)
        await until_status(stack, thread.id, ThreadStatus.PREVIEW_READY)
        t = stack.threads.get(thread.id)
        assert t.iterations[2].parent_sha == sha1            # forked, not tip
        assert any("branches off" in c.text for c in t.comments if c.system)
        # forked preview lacks iteration 2's patch (1 ancestor + 1 new)
        assert len(stack.patches.get(t.iterations[2].sha)) == 2
        await stack.queue.stop()
    run(main())

def test_production_capture_iterates_from_tip_not_base():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "make it blue", selector="#a")
        await until_status(stack, thread.id, ThreadStatus.PREVIEW_READY)
        tip = stack.threads.get(thread.id).preview_sha

        post(stack, "make it rounded", thread_id=thread.id, capture_sha=MAIN_SHA)
        await wait_for(lambda: len(stack.threads.get(thread.id).iterations) == 2)
        assert stack.threads.get(thread.id).iterations[1].parent_sha == tip
        await stack.queue.stop()
    run(main())

def test_interrupt_while_analyzing_combines_comments():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "style is off", selector="#a")
        await until_status(stack, thread.id, ThreadStatus.ANALYZING, timeout=2)
        post(stack, "also make it purple", thread_id=thread.id)
        await until_status(stack, thread.id, ThreadStatus.PREVIEW_READY)
        t = stack.threads.get(thread.id)
        assert len(t.iterations) == 1
        assert len(t.iterations[0].comment_ids) == 2          # combined into one task
        assert any("interrupted" in c.text for c in t.comments if c.system)
        await stack.queue.stop()
    run(main())

def test_comments_locked_past_the_cutoff():
    """Once the code phase starts, the thread is locked (product flow: wait
    for the preview or start a NEW thread) — nothing enters the append-only
    log, and the 409 message is the reminder."""
    import pytest
    from backend.comments.utils import CommentError

    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "style is off", selector="#a")
        await until_status(stack, thread.id, ThreadStatus.CODING, timeout=2)

        n_comments = len(stack.threads.get(thread.id).comments)
        for text, agent in (("and font size 18", True), ("just chatting", False)):
            with pytest.raises(CommentError) as e:
                post(stack, text, thread_id=thread.id, agent=agent)
            assert e.value.status_code == 409
            assert "Start a new comment thread" in e.value.message
        assert len(stack.threads.get(thread.id).comments) == n_comments  # nothing stored

        # a NEW thread during another thread's code phase is fine
        other, _ = post(stack, "hide this", selector="#b")
        assert other.id != thread.id

        # and the locked thread reopens once its preview lands
        await until_status(stack, thread.id, ThreadStatus.PREVIEW_READY)
        post(stack, "font size 18", thread_id=thread.id)
        await wait_for(lambda: len(stack.threads.get(thread.id).iterations) == 2)
        assert (stack.threads.get(thread.id).iterations[1].parent_sha
                == stack.threads.get(thread.id).iterations[0].sha)
        await stack.queue.stop()
    run(main())

def test_approve_opens_pr_and_reaches_done_with_lineage():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "hide this", selector="#a")
        await until_status(stack, thread.id, ThreadStatus.PREVIEW_READY)

        t = stack.threads.get(thread.id)
        result = stack.runner.approve(t, evan(), t.preview_sha)
        assert result == {"ok": True}
        await until_status(stack, thread.id, ThreadStatus.DONE)
        t = stack.threads.get(thread.id)
        assert t.pr_url and "github.com" in t.pr_url
        pr_note = next(c.text for c in t.comments if c.system and "Diff:" in c.text)
        assert f"{MAIN_SHA}...{t.preview_sha}" in pr_note
        await stack.queue.stop()
    run(main())

def test_double_approve_launches_exactly_one_pr_flow():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "hide this", selector="#a")
        await until_status(stack, thread.id, ThreadStatus.PREVIEW_READY)

        t = stack.threads.get(thread.id)
        first = stack.runner.approve(t, evan(), t.preview_sha)
        second = stack.runner.approve(t, evan(), t.preview_sha)   # double-click / racing user
        assert first == {"ok": True}
        assert "error" in second
        await until_status(stack, thread.id, ThreadStatus.DONE)
        pr_notes = [c for c in stack.threads.get(thread.id).comments
                    if c.system and "PR opened" in c.text]
        assert len(pr_notes) == 1
        await stack.queue.stop()
    run(main())

def test_recover_resubmits_pending_comments_after_restart():
    """A follow-up whose drain intent was lost (process died between the
    comment landing and the drain) is picked up by the boot sweep."""
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "make it blue", selector="#a")
        await until_status(stack, thread.id, ThreadStatus.PREVIEW_READY)
        await wait_for(lambda: stack.tasks.list_by_state(TaskState.DONE))
        await stack.queue.stop()

        # comment lands but the runner never hears about it (simulated crash)
        stack.service.post_comment(dana(), {"text": "@agent font size 18", "threadId": thread.id})

        assert stack.runner.recover() == 1          # boot sweep finds it
        await stack.queue.start()
        await wait_for(lambda: len(stack.threads.get(thread.id).iterations) == 2)
        await stack.queue.stop()
    run(main())

def test_transition_cas_only_fires_once():
    stack = make_stack()
    thread = stack.threads.create(target_selector="#a", target_label="a",
                                  page_url="/demo/profile", base_sha="mainsha")
    stack.threads.set_status(thread.id, ThreadStatus.PREVIEW_READY)
    assert stack.threads.transition(thread.id, ThreadStatus.PREVIEW_READY, ThreadStatus.PR_OPEN)
    assert not stack.threads.transition(thread.id, ThreadStatus.PREVIEW_READY, ThreadStatus.PR_OPEN)
    assert stack.threads.status(thread.id) == ThreadStatus.PR_OPEN

def test_approve_rejected_unless_preview_ready():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "hide this", selector="#a")
        assert "error" in stack.runner.approve(stack.threads.get(thread.id), evan(), None)
        await stack.queue.stop()
    run(main())

def test_duplicate_delivery_is_effect_exactly_once():
    """At-least-once + idempotency-key dedup: re-running a delivered task
    must not create a second iteration."""
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "make it blue", selector="#a")
        await until_status(stack, thread.id, ThreadStatus.PREVIEW_READY)
        # completion is recorded when the worker unwinds — wait for the row
        await wait_for(lambda: stack.tasks.list_by_state(TaskState.DONE))
        done_task = stack.tasks.list_by_state(TaskState.DONE)[0]
        await stack.runner._execute(done_task)                # simulate redelivery
        assert len(stack.threads.get(thread.id).iterations) == 1
        await stack.queue.stop()
    run(main())

def test_chaos_flaky_retries_then_ships():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "flaky style fix", selector="#a")
        await until_status(stack, thread.id, ThreadStatus.PREVIEW_READY)
        t = stack.threads.get(thread.id)
        assert any("Retrying" in c.text for c in t.comments if c.system)
        await stack.queue.stop()
    run(main())

def test_chaos_fatal_lands_in_dlq_and_thread_failed():
    async def main():
        stack = make_stack()
        await stack.queue.start()
        thread, _ = post(stack, "fatal problem, hide it", selector="#a")
        await until_status(stack, thread.id, ThreadStatus.FAILED)
        assert len(stack.tasks.list_by_state(TaskState.DLQ)) == 1
        t = stack.threads.get(thread.id)
        assert any("dead-letter" in c.text for c in t.comments if c.system)
        await stack.queue.stop()
    run(main())
