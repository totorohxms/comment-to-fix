"use client";

// Numbered pins next to each commented element (Google-Docs style).
// Positions recompute on scroll/resize; hidden/removed targets dock right.

import { useEffect, useState } from "react";
import type { Thread } from "@/lib/types";
import { STATUS_META } from "./status";

export function Markers({ threads, openThreadId, onOpen }: {
  threads: Thread[];
  openThreadId: string | null;
  onOpen: (id: string) => void;
}) {
  const [, setTick] = useState(0);

  useEffect(() => {
    const bump = () => setTick((t) => t + 1);
    window.addEventListener("scroll", bump, { passive: true });
    window.addEventListener("resize", bump);
    return () => {
      window.removeEventListener("scroll", bump);
      window.removeEventListener("resize", bump);
    };
  }, []);

  return (
    <>
      {threads.map((t, i) => {
        const el = document.querySelector(t.targetSelector);
        const rect = el?.getBoundingClientRect();
        const anchored = rect && (rect.width || rect.height);
        const style = anchored
          ? { left: rect.right + window.scrollX - 12, top: rect.top + window.scrollY - 12 }
          : { left: window.scrollX + window.innerWidth - 42, top: window.scrollY + 70 + i * 34 };
        const done = ["done", "merged"].includes(t.status);
        return (
          <button
            key={t.id}
            className={`ctf-marker${openThreadId === t.id ? " ctf-marker-open" : ""}${done ? " ctf-marker-done" : ""}`}
            style={style}
            title={`${t.targetLabel} — ${STATUS_META[t.status]?.[1] ?? t.status}`}
            onClick={() => onOpen(t.id)}
          >
            {i + 1}
          </button>
        );
      })}
    </>
  );
}
