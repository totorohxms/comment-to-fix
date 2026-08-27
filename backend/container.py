"""Dependency wiring. create_container() builds the whole object graph —
every component gets its collaborators injected there and nowhere else, so
swapping a fake for a real integration is a one-line change. All knobs come
from backend/config.py (env / .env).

The module-level `container` is the app's singleton; tests build their own
isolated graphs (backend/tests/conftest.make_stack) instead of sharing it.
"""

from dataclasses import dataclass

from backend.agent.agent import Agent
from backend.agent.github import GitHubPRService
from backend.agent.integrations import FakeGitClient, FakePRService, FakePreviewDeployer
from backend.agent.launchers import make_launcher
from backend.agent.launchers.base import TaskLauncher
from backend.agent.queue import AgentTaskQueue
from backend.agent.runner import AgentRunner
from backend.comments.service import CommentService, SlidingWindowRateLimiter
from backend.config import Settings, settings
from backend.db.database import Database
from backend.db.repos import CommentRepo, MetaRepo, PatchRepo, TaskRepo, ThreadRepo, UserRepo
from backend.domain.models import sha
from backend.pubsub import StatusBroker

@dataclass(frozen=True)
class AppContainer:
    db: Database
    meta: MetaRepo
    users: UserRepo
    comments: CommentRepo
    threads: ThreadRepo
    patches: PatchRepo
    tasks: TaskRepo
    broker: StatusBroker
    launcher: TaskLauncher
    agent: Agent
    queue: AgentTaskQueue
    runner: AgentRunner
    comment_service: CommentService
    main_sha: str

def create_container(cfg: Settings) -> AppContainer:
    db = Database(cfg.db_path)
    db.migrate()

    meta = MetaRepo(db)
    users = UserRepo(db)
    comments = CommentRepo(db)
    threads = ThreadRepo(db, comments)
    patches = PatchRepo(db)
    tasks = TaskRepo(db)

    # the demo site's stable "production" sha, minted once per database
    if not meta.get("main_sha"):
        meta.set("main_sha", sha())

    broker = StatusBroker()
    launcher = make_launcher(cfg.agent_launcher)
    # Real PRs when a token+repo are configured (or forced); fake otherwise.
    use_github = (cfg.pr_service == "github"
                  or (cfg.pr_service == "auto" and cfg.github_token and cfg.github_repo))
    prs = (GitHubPRService(repo=cfg.github_repo, token=cfg.github_token,
                           threads=threads, patches=patches,
                           auto_merge=cfg.github_auto_merge,
                           app_base_url=cfg.app_base_url)
           if use_github else FakePRService())
    agent = Agent(launcher=launcher,
                  git=FakeGitClient(),
                  deployer=FakePreviewDeployer(patches),
                  prs=prs)
    queue = AgentTaskQueue(
        tasks,
        max_inflight=cfg.agent_max_inflight,
        lease_s=cfg.task_lease_s,
        reaper_interval_s=cfg.reaper_interval_s,
        task_timeout_s=cfg.task_timeout_s)
    runner = AgentRunner(agent=agent, queue=queue, threads=threads,
                         comments=comments, users=users, broker=broker,
                         max_attempts=cfg.task_max_attempts)
    comment_service = CommentService(
        threads=threads, comments=comments, meta=meta,
        limiter=SlidingWindowRateLimiter(max_events=cfg.rate_limit_max,
                                         window_s=cfg.rate_limit_window_s))

    return AppContainer(
        db=db, meta=meta, users=users, comments=comments, threads=threads,
        patches=patches, tasks=tasks, broker=broker, launcher=launcher,
        agent=agent, queue=queue, runner=runner,
        comment_service=comment_service, main_sha=meta.get("main_sha"))

container = create_container(settings)
