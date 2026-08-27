"""Launcher registry. Pick with AGENT_LAUNCHER (default: fake-claude).

Adding a launcher = implement base.TaskLauncher, register it here.
"""

from backend.agent.launchers.base import TaskLauncher
from backend.agent.launchers.fake_claude import FakeClaudeTaskLauncher

_LAUNCHERS: dict[str, type] = {
    FakeClaudeTaskLauncher.name: FakeClaudeTaskLauncher,
    # "claude": ClaudeTaskLauncher,      (future: real Claude Code in a worktree)
    # "<other>": OtherTaskLauncher,      (future: any other agent runner)
}

def make_launcher(name: str) -> TaskLauncher:
    try:
        return _LAUNCHERS[name]()
    except KeyError:
        raise ValueError(f"unknown launcher {name!r}; available: {sorted(_LAUNCHERS)}")
