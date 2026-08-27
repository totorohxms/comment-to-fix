// API wire types — mirror backend/models.py to_api() shapes.

export type ThreadStatus =
  | "open" | "triggered" | "analyzing" | "coding" | "deploying" | "preview_ready"
  | "pr_open" | "merged" | "done" | "failed" | "cancelled";

export const CLOSED_STATUSES: ThreadStatus[] = ["pr_open", "merged", "done", "cancelled"];

export interface User {
  id: string;
  name: string;
  emoji: string;
  permission: "view" | "comment" | "approve";
}

export interface CaptureMeta {
  sha: string | null;
  url: string | null;
  networkCount: number;
  consoleCount: number;
  domBytes: number;
  hasScreenshot: boolean;
  viewport: { w: number; h: number; dpr: number } | null;
  traceId: string | null;
}

export interface ThreadComment {
  id: string;
  threadId: string;
  userId: string;
  text: string;
  system: boolean;
  createdAt: number;
  hasCapture: boolean;
  captureMeta: CaptureMeta | null;
}

export interface Iteration {
  sha: string;
  parentSha: string;
  summary: string;
  commentIds: string[];
}

export interface Thread {
  id: string;
  createdAt: number;
  targetSelector: string;
  targetLabel: string;
  pageUrl: string;
  baseSha: string;
  status: ThreadStatus;
  statusHistory: { status: ThreadStatus; at: number }[];
  comments: ThreadComment[];
  iterations: Iteration[];
  previewSha: string | null;
  previewUrl: string | null;
  prUrl: string | null;
}

export interface CaptureBundle {
  sha: string;
  env: string;
  url: string;
  sessionId: string;
  traceId: string | null;
  time: string;
  viewport: { w: number; h: number; dpr: number };
  userAgent: string;
  target: { selector: string; rect: DOMRect | object; text?: string } | null;
  network: object[];
  console: object[];
  domSnapshot: string;
  screenshot: string | null;
}

export interface ThreadUpdateEvent {
  type: "thread.update";
  threadId: string;
  status: ThreadStatus;
  note?: string | null;
  thread: Thread;
}
