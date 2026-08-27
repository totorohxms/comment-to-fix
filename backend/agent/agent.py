"""The Agent: pure executor of one unit of work.

Ownership contract (see also runner.py and queue.py):
  - AgentRunner DECIDES and NARRATES: what to run, base-sha resolution,
    records, system comments, status transitions, recovery.
  - AgentTaskQueue DELIVERS: claims, leases, retries, DLQ, the active task.
  - Agent EXECUTES: runs the launcher and applies side effects through the
    injected integration clients (git push, preview deploy, PR service).

The Agent therefore holds NO repos and NO broker — it reports progress through
the callback it's given and returns results; it never writes a record,
comment, or status itself.
"""

import logging

from backend.agent.integrations import GitClient, PRService, PreviewDeployer
from backend.agent.launchers.base import ProgressFn, TaskLauncher
from backend.agent.models import FixTaskSpec, IterationOutcome, TaskPhase
from backend.observability import track

log = logging.getLogger("ctf.agent")

class Agent:
    def __init__(self, launcher: TaskLauncher, git: GitClient,
                 deployer: PreviewDeployer, prs: PRService):
        self.launcher = launcher
        self.git = git
        self.deployer = deployer
        self.prs = prs

    @track("agent.iteration")
    async def run_iteration(self, spec: FixTaskSpec, progress: ProgressFn) -> IterationOutcome:
        """Execute one fix iteration: reason (launcher), push, deploy.
        Raising signals a failed attempt; the queue's retry/DLQ semantics
        take over."""
        result = await self.launcher.launch(spec, progress)

        progress(TaskPhase.DEPLOYING,
                 f"Pushing fix branch off `{spec.base_sha}`; deploying preview environment.")
        branch = await self.git.push_fix_branch(spec.thread_id, spec.base_sha, result.patch)
        preview_sha, _ = await self.deployer.deploy(spec.base_sha, result.patch, branch)

        log.info("iteration executed thread=%s preview=%s parent=%s",
                 spec.thread_id, preview_sha, spec.base_sha)
        return IterationOutcome(preview_sha=preview_sha, parent_sha=spec.base_sha,
                                patch=result.patch, analysis=result.analysis,
                                launcher=result.launcher)

    # ---- PR side effects (sequence + narration owned by the runner) ----------

    async def open_pr(self, thread_id: str) -> str:
        return await self.prs.open_pr(f"ctf/fix/{thread_id}", "fix from commentToFix thread")

    async def await_ci_and_review(self, pr_url: str) -> None:
        await self.prs.await_ci_and_review(pr_url)

    async def merge_pr(self, pr_url: str) -> None:
        await self.prs.merge(pr_url)
