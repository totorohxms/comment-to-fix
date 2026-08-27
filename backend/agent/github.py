"""Real PRService against a GitHub repo (REST API, token auth).

What "real" means here: approve materializes the thread's fix as an actual
branch + commit + pull request on the configured repo, and merge really
squash-merges it. The per-iteration "worktree push" stays simulated (the fake
GitClient); the branch is materialized lazily at open_pr time with one commit
carrying the thread's accumulated CSS patch under agent-fixes/.

Idempotency (matters for crash-resume): branch creation tolerates an existing
ref, the file commit updates in place, and open_pr falls back to the existing
open PR for the branch — so resume_pr_flow never creates a duplicate.

The merge commit message carries "[skip render]" so demo merges don't trigger
a Render redeploy of the live site on every approval.
"""

import asyncio
import base64
import logging
from urllib.parse import quote

import httpx

from backend.db.repos import PatchRepo, ThreadRepo

log = logging.getLogger("ctf.github")

API = "https://api.github.com"
CI_WAIT_S = 3.0  # stand-in for real CI/review webhooks

class GitHubPRService:
    def __init__(self, repo: str, token: str, threads: ThreadRepo, patches: PatchRepo,
                 auto_merge: bool = True, app_base_url: str = "",
                 client: httpx.AsyncClient | None = None):
        self.repo = repo
        self.threads = threads
        self.patches = patches
        self.auto_merge = auto_merge
        self.app_base_url = app_base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(
            base_url=API, timeout=20,
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})

    async def _req(self, method: str, path: str, ok: tuple, **kw) -> dict | list:
        r = await self._client.request(method, path, **kw)
        if r.status_code not in ok:
            raise RuntimeError(f"GitHub {method} {path} -> {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else {}

    # ---- PRService interface -------------------------------------------------

    async def open_pr(self, branch: str, title: str) -> str:
        thread_id = branch.rsplit("/", 1)[-1]
        thread = self.threads.get(thread_id)

        repo_info = await self._req("GET", f"/repos/{self.repo}", ok=(200,))
        base = repo_info["default_branch"]
        base_sha = (await self._req("GET", f"/repos/{self.repo}/git/ref/heads/{quote(base)}",
                                    ok=(200,)))["object"]["sha"]

        # branch off the default branch (tolerate an existing ref: resume path)
        r = await self._client.post(f"/repos/{self.repo}/git/refs",
                                    json={"ref": f"refs/heads/{branch}", "sha": base_sha})
        if r.status_code not in (201, 422):
            raise RuntimeError(f"GitHub create ref -> {r.status_code}: {r.text[:200]}")

        # one commit: the thread's accumulated patch as a css file
        path = f"agent-fixes/{thread_id}.css"
        css = self._patch_file(thread)
        payload = {"message": f"agent fix: {thread.target_label} (thread {thread_id})",
                   "content": base64.b64encode(css.encode()).decode(), "branch": branch}
        existing = await self._client.get(f"/repos/{self.repo}/contents/{quote(path)}",
                                          params={"ref": branch})
        if existing.status_code == 200:
            payload["sha"] = existing.json()["sha"]
        await self._req("PUT", f"/repos/{self.repo}/contents/{quote(path)}",
                        ok=(200, 201), json=payload)

        # open the PR (fall back to the already-open one for this branch)
        pr_title = f"[agent fix] {thread.target_label}"
        r = await self._client.post(f"/repos/{self.repo}/pulls",
                                    json={"title": pr_title, "head": branch, "base": base,
                                          "body": self._pr_body(thread)})
        if r.status_code == 201:
            return r.json()["html_url"]
        if r.status_code == 422:
            owner = self.repo.split("/")[0]
            open_prs = await self._req("GET", f"/repos/{self.repo}/pulls", ok=(200,),
                                       params={"head": f"{owner}:{branch}", "state": "open"})
            if open_prs:
                return open_prs[0]["html_url"]
        raise RuntimeError(f"GitHub open PR -> {r.status_code}: {r.text[:200]}")

    async def await_ci_and_review(self, pr_url: str) -> None:
        # Real system: wait on check-suite + review webhooks. Demo: short beat.
        await asyncio.sleep(CI_WAIT_S)

    async def merge(self, pr_url: str) -> None:
        if not self.auto_merge:
            log.info("auto-merge disabled; leaving %s open for a human", pr_url)
            return
        number = pr_url.rstrip("/").rsplit("/", 1)[-1]
        pr = await self._req("GET", f"/repos/{self.repo}/pulls/{number}", ok=(200,))
        await self._req("PUT", f"/repos/{self.repo}/pulls/{number}/merge", ok=(200,), json={
            "merge_method": "squash",
            # [skip render] keeps demo merges from redeploying the live site
            "commit_title": f"{pr['title']} (#{number}) [skip render]",
        })
        # best-effort branch cleanup
        await self._client.delete(f"/repos/{self.repo}/git/refs/heads/{quote(pr['head']['ref'])}")

    # ---- content builders ----------------------------------------------------

    def _patch_file(self, thread) -> str:
        patches = self.patches.get(thread.preview_sha) or []
        head = (f"/* agent fix — thread {thread.id}\n"
                f" * target: {thread.target_selector} ({thread.target_label})\n"
                f" * lineage: {thread.base_sha} -> "
                + " -> ".join(i.sha for i in thread.iterations) + "\n */\n\n")
        return head + "\n\n".join(p.css for p in patches) + "\n"

    def _pr_body(self, thread) -> str:
        asks = "\n".join(f"> {c.text}" for c in thread.comments if not c.system)
        lineage = " → ".join([f"`{thread.base_sha}`", *(f"`{i.sha}`" for i in thread.iterations)])
        summaries = "\n".join(f"- `{i.sha}`: {i.summary}" for i in thread.iterations)
        preview = (f"{self.app_base_url}/preview/{thread.preview_sha}"
                   if self.app_base_url else f"/preview/{thread.preview_sha}")
        return (f"Automated fix from a [commentToFix]({self.app_base_url or ''}) thread "
                f"on `{thread.target_selector}`.\n\n"
                f"**User feedback**\n{asks}\n\n"
                f"**Iterations**\n{summaries}\n\n"
                f"**Preview lineage**: {lineage}\n"
                f"**Approved preview**: {preview}\n")
