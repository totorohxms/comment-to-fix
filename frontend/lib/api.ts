// Typed API client. All calls carry the fake-auth header; errors surface the
// backend's detail message.

import type { CaptureBundle, Thread, User } from "./types";

async function api<T>(path: string, user: string | null, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-CTF-User": user ?? "",
      ...(init?.headers ?? {}),
    },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || body.error || `HTTP ${res.status}`);
  return body as T;
}

export const listUsers = () => api<User[]>("/api/users", null);

export const listThreads = (user: string, pageUrl: string) =>
  api<Thread[]>(`/api/threads?pageUrl=${encodeURIComponent(pageUrl)}`, user);

export interface PostCommentBody {
  threadId?: string;
  text: string;
  capture: CaptureBundle | null;
  target?: { selector: string; label: string };
}

export const postComment = (user: string, body: PostCommentBody) =>
  api<{ thread: Thread; commentId: string }>("/api/comments", user, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const approveThread = (user: string, threadId: string, previewSha: string | null) =>
  api<Thread>(`/api/threads/${threadId}/approve`, user, {
    method: "POST",
    body: JSON.stringify({ previewSha }),  // the sha the approver reviewed
  });

export const getCapture = (user: string, commentId: string) =>
  api<Record<string, unknown>>(`/api/comments/${commentId}/capture`, user);

export function subscribeEvents(
  user: string,
  pageUrl: string,
  onEvent: (data: unknown) => void,
  onResync: () => void,
): () => void {
  // Authenticated + page-scoped stream with id-based replay: on reconnect the
  // browser resends Last-Event-ID and missed events replay; a "reset" event
  // means the gap was unverifiable and state must be refetched via REST.
  const es = new EventSource(
    `/api/events?user=${encodeURIComponent(user)}&pageUrl=${encodeURIComponent(pageUrl)}`);
  es.onmessage = (e) => {
    try { onEvent(JSON.parse(e.data)); } catch { /* keepalive */ }
  };
  es.addEventListener("reset", onResync);
  // Resync whenever the stream (re)connects: anything a broken proxy or a
  // dropped connection swallowed is picked up from the REST API.
  es.onopen = onResync;
  return () => es.close();
}
