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
    }, () => { refresh().catch(() => {}); });  // reset: replay gap unverifiable
  }, [userId, pageUrl, refresh]);

  return { threads, refresh };
}
