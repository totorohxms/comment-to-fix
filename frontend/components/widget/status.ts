import type { ThreadStatus } from "@/lib/types";

export const STATUS_META: Record<ThreadStatus, [string, string]> = {
  open: ["💬", "Open — discussion"],
  triggered: ["⏳", "Triggered"],
  analyzing: ["🔍", "Analyzing"],
  coding: ["⌨️", "Putting up code change"],
  deploying: ["🚀", "Deploying preview"],
  preview_ready: ["✅", "Preview ready — verify it"],
  pr_open: ["🔀", "PR open, in review"],
  merged: ["🎉", "Merged"],
  done: ["🏁", "Done"],
  failed: ["💥", "Failed — task in DLQ"],
  cancelled: ["🚫", "Cancelled"],
};

// Happy path only — failed/cancelled render as a pill, not a timeline step.
export const STATUS_ORDER: ThreadStatus[] = [
  "triggered", "analyzing", "coding", "deploying",
  "preview_ready", "pr_open", "merged", "done",
];
