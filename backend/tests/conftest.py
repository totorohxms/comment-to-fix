"""Shared test fixtures.

Tests build an isolated stack (in-memory SQLite + fresh queue/agent/runner)
instead of touching the module-level container, and shrink the fakes' timers
so full pipelines run in tens of milliseconds.
"""

import asyncio
import os
from types import SimpleNamespace

# Must be set before anything imports backend.container (api tests do).
os.environ.setdefault("CTF_DB", ":memory:")
os.environ.setdefault("REAPER_INTERVAL_S", "0.05")
os.environ.setdefault("TASK_LEASE_S", "0.3")

import pytest

from backend.agent import integrations
from backend.agent.agent import Agent
from backend.agent.integrations import FakeGitClient, FakePRService, FakePreviewDeployer
from backend.agent.launchers import fake_claude
from backend.agent.launchers.fake_claude import FakeClaudeTaskLauncher
from backend.agent.queue import AgentTaskQueue
from backend.agent.runner import AgentRunner
from backend.comments.service import CommentService, SlidingWindowRateLimiter
from backend.db.database import Database
from backend.db.repos import CommentRepo, MetaRepo, PatchRepo, TaskRepo, ThreadRepo, UserRepo
from backend.pubsub import StatusBroker

@pytest.fixture(autouse=True)
def fast_fakes(monkeypatch):
    """All simulated durations -> milliseconds."""
    monkeypatch.setattr(fake_claude, "ANALYZE_S", 0.02)
    monkeypatch.setattr(fake_claude, "CODE_S", 0.02)
    monkeypatch.setattr(integrations, "PUSH_S", 0.01)
    monkeypatch.setattr(integrations, "DEPLOY_S", 0.01)
    monkeypatch.setattr(integrations, "OPEN_PR_S", 0.01)
    monkeypatch.setattr(integrations, "CI_REVIEW_S", 0.02)
    monkeypatch.setattr(integrations, "MERGE_S", 0.02)

MAIN_SHA = "mainsha"

def make_stack(*, max_inflight=2, max_attempts=3, lease_s=0.3,
               reaper_interval_s=0.05, task_timeout_s=10.0, rate_limit=(100, 30.0)):
    db = Database(":memory:")
    db.migrate()
    meta = MetaRepo(db)
    meta.set("main_sha", MAIN_SHA)
    comments = CommentRepo(db)
    threads = ThreadRepo(db, comments)
    patches = PatchRepo(db)
    tasks = TaskRepo(db)
    users = UserRepo(db)
    broker = StatusBroker()
    agent = Agent(launcher=FakeClaudeTaskLauncher(), git=FakeGitClient(),
                  deployer=FakePreviewDeployer(patches), prs=FakePRService())
    queue = AgentTaskQueue(tasks, max_inflight=max_inflight, lease_s=lease_s,
                           poll_s=0.02, reaper_interval_s=reaper_interval_s,
                           task_timeout_s=task_timeout_s)
    runner = AgentRunner(agent=agent, queue=queue, threads=threads,
                         comments=comments, users=users, broker=broker,
                         max_attempts=max_attempts)
    service = CommentService(threads=threads, comments=comments, meta=meta,
                             limiter=SlidingWindowRateLimiter(*rate_limit))
    return SimpleNamespace(db=db, meta=meta, users=users, comments=comments,
                           threads=threads, patches=patches, tasks=tasks,
                           broker=broker, agent=agent, queue=queue, runner=runner,
                           service=service)

def dana():
    from backend.domain.models import Permission, User
    return User(id="dana", name="Dana", emoji="🎨", permission=Permission.COMMENT)

def evan():
    from backend.domain.models import Permission, User
    return User(id="evan", name="Evan", emoji="🛠️", permission=Permission.APPROVE)

async def wait_for(cond, timeout=3.0, interval=0.01):
    """Poll until cond() is truthy or fail the test."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if cond():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met within timeout")

def post(stack, text, *, thread_id=None, selector="#btn", capture_sha=MAIN_SHA,
         url="/demo/profile", agent=True):
    """Post a comment through service + runner (what the route does).
    agent=True prefixes @agent (the explicit invocation that launches tasks);
    agent=False posts a plain collaboration comment."""
    body = {
        "text": (f"@agent {text}" if agent else text),
        "capture": {"sha": capture_sha, "url": url},
    }
    if thread_id:
        body["threadId"] = thread_id
    else:
        body["target"] = {"selector": selector, "label": selector}
    thread, comment = stack.service.post_comment(dana(), body)
    stack.runner.on_comment(thread, comment)
    return thread, comment
