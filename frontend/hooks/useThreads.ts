"use client";

// Threads for the current page, kept live via the backend's SSE pubsub.

import { useCallback, useEffect, useState } from "react";
import { listThreads, subscribeEvents } from "@/lib/api";
import type { Thread, ThreadUpdateEvent } from "@/lib/types";

export function useThreads(userId: string | null, pageUrl: string) {
  const [threads, setThreads] = useState<Thread[]>([]);

  const refresh = useCallback(async () => {
    if (!userId) return;
    setThreads(await listThreads(userId, pageUrl));
  }, [userId, pageUrl]);

  useEffect(() => { refresh().catch(() => {}); }, [refresh]);

  useEffect(() => {
    if (!userId) return;
    return subscribeEvents(userId, pageUrl, (raw) => {
      const ev = raw as ThreadUpdateEvent;
      if (ev?.type !== "thread.update" || !ev.thread) return;
      setThreads((prev) => {
        const i = prev.findIndex((t) => t.id === ev.thread.id);
        if (i >= 0) {
          const next = [...prev];
          next[i] = ev.thread;
          return next;
        }
        return [...prev, ev.thread];  // server already scoped events to this page
      });
    }, () => { refresh().catch(() => {}); });  // reset / reconnect: resync via REST
  }, [userId, pageUrl, refresh]);

  // Slow-poll safety net: even if SSE is silently broken (buffering proxy,
  // corporate middlebox), the UI can lag by at most one poll interval —
  // never wedge stale with a live-looking Approve button.
  useEffect(() => {
    if (!userId) return;
    const t = setInterval(() => { refresh().catch(() => {}); }, 15_000);
    return () => clearInterval(t);
  }, [userId, refresh]);

  return { threads, refresh };
}
