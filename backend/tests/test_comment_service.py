"""Comment domain logic: thread creation, closed/stale policy, rate limiting."""

import pytest

from backend.comments.service import SlidingWindowRateLimiter
from backend.comments.utils import CommentError
from backend.domain.models import Iteration, Patch, ThreadStatus
from backend.tests.conftest import MAIN_SHA, dana, make_stack

def svc_post(stack, body):
    return stack.service.post_comment(dana(), body)

def test_new_thread_created_with_capture_base_sha():
    stack = make_stack()
    thread, comment = svc_post(stack, {
        "text": "style is off",
        "target": {"selector": "#btn", "label": "Btn"},
        "capture": {"sha": MAIN_SHA, "url": "/demo/profile"},
    })
    assert thread.base_sha == MAIN_SHA
    assert thread.status == ThreadStatus.OPEN  # discussion until @agent summons
    assert comment.text == "style is off"
    assert stack.threads.get(thread.id).comments[0].id == comment.id

def test_follow_up_appends_to_existing_thread():
    stack = make_stack()
    thread, _ = svc_post(stack, {"text": "first", "target": {"selector": "#a"}})
    t2, c2 = svc_post(stack, {"text": "second", "threadId": thread.id})
    assert t2.id == thread.id
    assert [c.text for c in stack.threads.get(thread.id).comments] == ["first", "second"]

def test_unknown_thread_404():
    stack = make_stack()
    with pytest.raises(CommentError) as e:
        svc_post(stack, {"text": "hi", "threadId": "thr_nope"})
    assert e.value.status_code == 404

def test_new_thread_without_target_400():
    stack = make_stack()
    with pytest.raises(CommentError) as e:
        svc_post(stack, {"text": "hi"})
    assert e.value.status_code == 400

@pytest.mark.parametrize("status", [
    ThreadStatus.PR_OPEN, ThreadStatus.MERGED, ThreadStatus.DONE, ThreadStatus.CANCELLED,
])
def test_follow_up_on_closed_thread_409(status):
    stack = make_stack()
    thread, _ = svc_post(stack, {"text": "first", "target": {"selector": "#a"}})
    stack.threads.set_status(thread.id, status)
    with pytest.raises(CommentError) as e:
        svc_post(stack, {"text": "more", "threadId": thread.id})
    assert e.value.status_code == 409
    assert "stale" in e.value.message

def test_follow_up_on_failed_thread_allowed():
    """FAILED is deliberately not closed: commenting retries."""
    stack = make_stack()
    thread, _ = svc_post(stack, {"text": "first", "target": {"selector": "#a"}})
    stack.threads.set_status(thread.id, ThreadStatus.FAILED)
    t, _ = svc_post(stack, {"text": "try again", "threadId": thread.id})
    assert t.id == thread.id

def test_new_thread_from_stale_preview_409():
    stack = make_stack()
    thread, _ = svc_post(stack, {"text": "first", "target": {"selector": "#a"}})
    stack.threads.add_iteration(thread.id, Iteration(
        sha="prev1234", parent_sha=MAIN_SHA, summary="s", comment_ids=[]))
    stack.threads.set_status(thread.id, ThreadStatus.DONE)
    with pytest.raises(CommentError) as e:
        svc_post(stack, {"text": "new issue", "target": {"selector": "#b"},
                         "capture": {"sha": "prev1234", "url": "/preview/prev1234"}})
    assert e.value.status_code == 409

def test_new_thread_from_open_threads_preview_allowed():
    stack = make_stack()
    thread, _ = svc_post(stack, {"text": "first", "target": {"selector": "#a"}})
    stack.threads.add_iteration(thread.id, Iteration(
        sha="prev1234", parent_sha=MAIN_SHA, summary="s", comment_ids=[]))
    stack.threads.set_status(thread.id, ThreadStatus.PREVIEW_READY)
    t, _ = svc_post(stack, {"text": "another element", "target": {"selector": "#b"},
                            "capture": {"sha": "prev1234", "url": "/preview/prev1234"}})
    assert t.base_sha == "prev1234"

def test_rate_limiter_window():
    lim = SlidingWindowRateLimiter(max_events=2, window_s=60)
    assert lim.allow("u") and lim.allow("u")
    assert not lim.allow("u")          # third within window rejected
    assert lim.allow("someone-else")   # per-user, not global

def test_rate_limit_surfaces_as_429():
    stack = make_stack(rate_limit=(1, 60))
    svc_post(stack, {"text": "one", "target": {"selector": "#a"}})
    with pytest.raises(CommentError) as e:
        svc_post(stack, {"text": "two", "target": {"selector": "#b"}})
    assert e.value.status_code == 429
