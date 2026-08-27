"""FakeClaudeTaskLauncher — simulates launching a Claude Code task.

The real launcher would: create a fresh git worktree at spec.base_sha, hand the
capture bundles (screenshot, DOM, network trace) to a Claude Code process as
context, let it edit code, and return the diff. This fake keeps the same shape
and pacing but manufactures the output: keyword heuristics map comment intent
to a CSS patch, and the "analysis" is a plausible transcript summary.

It also owns the demo chaos injection (magic words simulating worker failures)
— failure modes are a property of the worker, so they live in the launcher.
"""

import asyncio
import re

from backend.agent.queue import WorkerVanished
from backend.domain.models import Comment, Patch
from backend.agent.models import FixTaskResult, FixTaskSpec, TaskPhase

ANALYZE_S = 3.5
CODE_S = 4.5

COLORS = {
    "red": "#e5484d", "blue": "#3b82f6", "green": "#30a46c", "purple": "#8e4ec6",
    "orange": "#f76b15", "pink": "#e93d82", "teal": "#12a594", "accent": "#6e56cf",
}

class FakeClaudeTaskLauncher:
    name = "fake-claude"

    async def launch(self, spec: FixTaskSpec, progress) -> FixTaskResult:
        retry = f" (attempt {spec.attempt}/{spec.max_attempts})" if spec.attempt > 1 else ""
        progress(TaskPhase.ANALYZING,
                 f"Claude task claimed a worktree off `{spec.base_sha}`{retry}; "
                 "reading capture bundle (screenshot, DOM, network trace).")
        self._maybe_inject_fault(spec)
        await asyncio.sleep(ANALYZE_S)  # real: model reads captures + code

        progress(TaskPhase.CODING,
                 "Editing code in the worktree. Past the cutoff: new comments "
                 "now queue instead of interrupting.")
        await asyncio.sleep(CODE_S)     # real: model writes + verifies the change

        patch = self._fake_change(spec.target_selector, spec.comments)
        analysis = (
            f"Matched intent from {len(spec.comments)} comment(s) to `{spec.target_selector}` "
            f"({spec.target_label}); confirmed the element in the DOM snapshot and traced its "
            f"render path from the capture. Change verified in the worktree.")
        return FixTaskResult(patch=patch, analysis=analysis, launcher=self.name)

    # ---- demo chaos: magic words simulate worker failures --------------------
    #   'flaky'  -> crashes on the first attempt, succeeds on retry
    #   'fatal'  -> crashes every attempt, ends in the DLQ
    #   'vanish' -> dies silently on the first attempt; the janitor reclaims it

    def _maybe_inject_fault(self, spec: FixTaskSpec) -> None:
        text = " ".join(c.text.lower() for c in spec.comments)
        if "fatal" in text:
            raise RuntimeError("simulated permanent failure ('fatal' in comment)")
        if "flaky" in text and spec.attempt < 2:
            raise RuntimeError("simulated transient failure ('flaky' in comment)")
        if "vanish" in text and spec.attempt < 2:
            raise WorkerVanished("simulated silent worker death ('vanish' in comment)")

    # ---- canned output: keyword heuristics -> CSS patch -----------------------

    def _fake_change(self, selector: str, comments: list[Comment]) -> Patch:
        text = "\n".join(c.text for c in comments).lower()
        css: list[str] = []
        notes: list[str] = []

        if re.search(r"(hide|remove|should not (show|appear)|shouldn'?t (show|appear)|take.*(off|out))", text):
            css.append(f"{selector} {{ display: none !important; }}")
            notes.append(f"Removed {selector} from the page (feature-flagged off).")

        color = next((c for c in COLORS if c in text), None)
        if color and not re.search(r"(hide|remove)", text):
            v = COLORS[color]
            css.append(f"{selector} {{ background: {v} !important; border-color: {v} !important; color: #fff !important; }}")
            notes.append(f"Applied {color} ({v}) to {selector}.")

        fs = re.search(r"font[\s-]?size[^0-9]{0,10}(\d{2})", text)
        if fs:
            css.append(f"{selector} {{ font-size: {fs.group(1)}px !important; }}")
            notes.append(f"Set font-size to {fs.group(1)}px on {selector}.")
        elif re.search(r"(bigger|larger) (font|text)|font (too )?small", text):
            css.append(f"{selector} {{ font-size: 18px !important; }}")
            notes.append(f"Bumped font-size to 18px on {selector}.")

        if re.search(r"round(ed)?|radius", text):
            css.append(f"{selector} {{ border-radius: 999px !important; }}")
            notes.append(f"Rounded corners on {selector}.")

        if not css and re.search(r"style|look|design|ugly|not right|off|weird", text):
            css.append(
                f"{selector} {{ background: linear-gradient(135deg,#6e56cf,#3b82f6) !important; color:#fff !important; "
                f"border: none !important; border-radius: 10px !important; padding: 10px 20px !important; "
                f"font-weight: 600 !important; box-shadow: 0 4px 14px rgba(110,86,207,.35) !important; }}")
            notes.append(f"Restyled {selector} to match the design system (gradient, radius 10, weight 600).")

        if not css:
            css.append(f"{selector} {{ outline: 2px solid #6e56cf !important; outline-offset: 2px !important; }}")
            notes.append(
                f"Could not infer a concrete change; applied a visible placeholder tweak to {selector}. "
                "(A real launcher would ask a clarifying question.)")

        return Patch(css="\n".join(css), summary=" ".join(notes))
