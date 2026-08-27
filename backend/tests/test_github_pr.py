"""GitHubPRService against a mock transport, and the runner's PR-failure
fallback (a GitHub error must never strand a thread)."""

import asyncio
import base64
import json

import httpx

from backend.agent.github import GitHubPRService
from backend.domain.models import Iteration, Patch, ThreadStatus
from backend.tests.conftest import evan, make_stack, post, wait_for

def run(coro):
    asyncio.run(coro)

def make_thread(stack):
    thread = stack.threads.create(target_selector="#btn-edit", target_label="Edit Profile",
                                  page_url="/demo/profile", base_sha="mainsha")
    stack.comments.append(thread.id, user_id="dana", text="@agent style is not right")
    stack.threads.add_iteration(thread.id, Iteration(
        sha="prev0001", parent_sha="mainsha", summary="restyled", comment_ids=[]))
    stack.patches.put("prev0001", [Patch(css="#btn-edit { color: red; }", summary="restyled")])
    return stack.threads.get(thread.id)

class FakeGitHub:
    """Minimal GitHub API double behind httpx.MockTransport."""

    def __init__(self):
        self.requests: list[tuple[str, str, dict | None]] = []
        self.pr_exists = False

    def handler(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        self.requests.append((req.method, req.url.path, body))
        path, method = req.url.path, req.method
        if path == "/repos/o/r" and method == "GET":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/repos/o/r/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "abc123"}})
        if path == "/repos/o/r/git/refs" and method == "POST":
            return httpx.Response(201, json={})
        if path.startswith("/repos/o/r/contents/") and method == "GET":
            return httpx.Response(404)
        if path.startswith("/repos/o/r/contents/") and method == "PUT":
            return httpx.Response(201, json={})
        if path == "/repos/o/r/pulls" and method == "POST":
            if self.pr_exists:
                return httpx.Response(422, json={"message": "A pull request already exists"})
            return httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/7"})
        if path == "/repos/o/r/pulls" and method == "GET":
            return httpx.Response(200, json=[{"html_url": "https://github.com/o/r/pull/7"}])
        if path == "/repos/o/r/pulls/7" and method == "GET":
            return httpx.Response(200, json={"title": "t", "head": {"ref": "ctf/fix/x"}})
        if path == "/repos/o/r/pulls/7/merge":
            return httpx.Response(200, json={})
        if method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(500, json={"unexpected": path})

def make_service(stack, gh: FakeGitHub, **kw) -> GitHubPRService:
    client = httpx.AsyncClient(base_url="https://api.github.com",
                               transport=httpx.MockTransport(gh.handler))
    return GitHubPRService(repo="o/r", token="unused", threads=stack.threads,
                           patches=stack.patches, client=client,
                           app_base_url="https://demo.example", **kw)

def test_open_pr_creates_branch_commit_and_pr():
    async def main():
        stack = make_stack()
        thread = make_thread(stack)
        gh = FakeGitHub()
        svc = make_service(stack, gh)
        url = await svc.open_pr(f"ctf/fix/{thread.id}", "ignored")
        assert url == "https://github.com/o/r/pull/7"

        ref = next(b for m, p, b in gh.requests if p == "/repos/o/r/git/refs")
        assert ref["ref"] == f"refs/heads/ctf/fix/{thread.id}"
        put = next(b for m, p, b in gh.requests if m == "PUT" and "/contents/" in p)
        css = base64.b64decode(put["content"]).decode()
        assert "#btn-edit { color: red; }" in css and thread.id in css
        pr = next(b for m, p, b in gh.requests if p == "/repos/o/r/pulls" and m == "POST")
        assert "Edit Profile" in pr["title"]
        assert "@agent style is not right" in pr["body"]
        assert "https://demo.example/preview/prev0001" in pr["body"]
    run(main())

def test_open_pr_idempotent_when_pr_already_exists():
    async def main():
        stack = make_stack()
        thread = make_thread(stack)
        gh = FakeGitHub()
        gh.pr_exists = True
        url = await make_service(stack, gh).open_pr(f"ctf/fix/{thread.id}", "t")
        assert url == "https://github.com/o/r/pull/7"   # reused, not duplicated
    run(main())

def test_merge_squashes_with_skip_render_and_cleans_branch():
    async def main():
        stack = make_stack()
        gh = FakeGitHub()
        await make_service(stack, gh).merge("https://github.com/o/r/pull/7")
        merge = next(b for m, p, b in gh.requests if p.endswith("/merge"))
        assert merge["merge_method"] == "squash"
        assert "[skip render]" in merge["commit_title"]
        assert any(m == "DELETE" for m, p, b in gh.requests)   # branch cleanup
    run(main())

def test_auto_merge_off_leaves_pr_open():
    async def main():
        stack = make_stack()
        gh = FakeGitHub()
        await make_service(stack, gh, auto_merge=False).merge("https://github.com/o/r/pull/7")
        assert gh.requests == []                                # untouched
    run(main())

def test_runner_reverts_to_preview_ready_when_open_pr_fails():
    async def main():
        stack = make_stack()

        class FailingPRs:
            async def open_pr(self, branch, title):
                raise RuntimeError("github is down")
            async def await_ci_and_review(self, pr_url): ...
            async def merge(self, pr_url): ...
        stack.agent.prs = FailingPRs()

        await stack.queue.start()
        thread, _ = post(stack, "hide this", selector="#a")
        await wait_for(lambda: stack.threads.get(thread.id).status == ThreadStatus.PREVIEW_READY)
        t = stack.threads.get(thread.id)
        assert stack.runner.approve(t, evan(), t.preview_sha) == {"ok": True}
        # flow fails -> thread comes back to preview_ready, approvable again
        await wait_for(lambda: stack.threads.get(thread.id).status == ThreadStatus.PREVIEW_READY)
        assert any("Could not open the PR" in c.text
                   for c in stack.threads.get(thread.id).comments if c.system)
        await stack.queue.stop()
    run(main())
