"""Integration seams the agent depends on, and their fakes.

Pushing a branch, deploying a preview, opening a PR are *side effects*, not
agent reasoning — so they are injected clients, not agent subclasses. In
production these become a real git/GitHub client, the preview-deploy pipeline,
and the PR/CI API; the Agent code does not change.
"""

import asyncio
from typing import Protocol

from backend.db.repos import PatchRepo
from backend.domain.models import Patch, sha

# Simulated durations (module-level so tests can shrink them).
PUSH_S = 1.0
DEPLOY_S = 2.0
OPEN_PR_S = 0.5
CI_REVIEW_S = 4.0
MERGE_S = 2.5

class GitClient(Protocol):
    async def push_fix_branch(self, thread_id: str, parent_sha: str, patch: Patch) -> str:
        """Create a worktree off parent_sha, apply the patch, push. Returns branch name."""
        ...

class PreviewDeployer(Protocol):
    async def deploy(self, parent_sha: str, patch: Patch, branch: str) -> tuple[str, str]:
        """Deploy the branch as a preview environment. Returns (preview_sha, preview_url)."""
        ...

class PRService(Protocol):
    async def open_pr(self, branch: str, title: str) -> str:
        """Open a pull request for the branch. Returns the PR url."""
        ...

    async def await_ci_and_review(self, pr_url: str) -> None: ...
    async def merge(self, pr_url: str) -> None: ...

# ---- fakes -------------------------------------------------------------------

class FakeGitClient:
    async def push_fix_branch(self, thread_id: str, parent_sha: str, patch: Patch) -> str:
        await asyncio.sleep(PUSH_S)  # git worktree add + apply + push
        return f"ctf/fix/{thread_id}"

class FakePreviewDeployer:
    """"Deploys" by registering the accumulated patch set under a fresh sha;
    the demo site's /preview/{sha} route renders it. Worktree lineage: the
    child preview carries all ancestor patches.

    Crash-safety note: deploy happens BEFORE the iteration row is written (the
    linearization point). A crash in between leaves an orphan preview that no
    iteration references — redelivery deploys a fresh one and dedup prevents a
    double iteration/PR. A real deployer needs GC for unreferenced previews."""

    def __init__(self, patches: PatchRepo):
        self.patches = patches

    async def deploy(self, parent_sha: str, patch: Patch, branch: str) -> tuple[str, str]:
        await asyncio.sleep(DEPLOY_S)  # build + deploy preview env
        new_sha = sha()
        lineage = (self.patches.get(parent_sha) or []) + [patch]
        self.patches.put(new_sha, lineage)
        return new_sha, f"/preview/{new_sha}"

class FakePRService:
    # The acme/webapp host is the SENTINEL for a simulated PR: the runner tags
    # the thread comment "(simulated)" and the UI renders a non-navigating
    # chip when it sees it. Real PRs (GitHubPRService) never use this host.
    async def open_pr(self, branch: str, title: str) -> str:
        await asyncio.sleep(OPEN_PR_S)
        import random
        return f"https://github.com/acme/webapp/pull/{random.randint(1000, 9999)}"

    async def await_ci_and_review(self, pr_url: str) -> None:
        await asyncio.sleep(CI_REVIEW_S)  # CI + human review

    async def merge(self, pr_url: str) -> None:
        await asyncio.sleep(MERGE_S)
