"""The TaskLauncher abstraction: how the Agent runs "the actual work".

A launcher takes a FixTaskSpec (target element, user comments + captures, base
sha) and returns a FixTaskResult (proposed change + analysis), reporting
progress phases along the way. The Agent stays launcher-agnostic: it builds the
spec, maps progress to thread statuses, and applies the result's side effects
(push branch, deploy preview).

Implementations:
  fake_claude.FakeClaudeTaskLauncher  — demo: simulated Claude Code task with
                                        canned output and chaos injection
  (future) ClaudeTaskLauncher         — spawn a real Claude Code process in a
                                        fresh git worktree, capture bundle as
                                        prompt context, diff as the result
  (future) any other agent/LLM runner — same contract

Pick one via the AGENT_LAUNCHER env knob (see launchers/__init__.py).
"""

from typing import Callable, Protocol

from backend.agent.models import FixTaskResult, FixTaskSpec, TaskPhase

# progress(phase, human-readable note) — called as the launcher moves through
# its work; the Agent publishes these to the thread.
ProgressFn = Callable[[TaskPhase, str], None]

class TaskLauncher(Protocol):
    name: str

    async def launch(self, spec: FixTaskSpec, progress: ProgressFn) -> FixTaskResult:
        """Run one fix task to completion. Raise to signal a failed attempt
        (the queue's retry/DLQ semantics take over)."""
        ...
