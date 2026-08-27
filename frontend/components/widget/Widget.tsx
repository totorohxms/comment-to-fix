"use client";

// commentToFix widget — the embeddable overlay. Orchestrates:
//   toolbar (user switcher, comment mode) · element picking + highlight ·
//   floating composer · thread markers · thread panel · SSE-live statuses
// Drop <Widget sha env> on any page; everything it renders is tagged ctf-*
// so the capture engine can exclude it.

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { listUsers, postComment } from "@/lib/api";
import { buildCapture, cssPath, installCapture } from "@/lib/capture";
import { CLOSED_STATUSES, type Thread, type User } from "@/lib/types";
import { useThreads } from "@/hooks/useThreads";
import { Composer, type ComposerTarget } from "./Composer";
import { Markers } from "./Markers";
import { ThreadPanel } from "./ThreadPanel";

installCapture(); // patch fetch/console before the host page's requests fire

export function Widget({ sha, env }: { sha: string; env: "production" | "preview" }) {
  const pathname = usePathname();
  const [users, setUsers] = useState<User[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const { threads, refresh } = useThreads(user?.id ?? null, pathname);
  const [commentMode, setCommentMode] = useState(false);
  const [hoverRect, setHoverRect] = useState<DOMRect | null>(null);
  const hoverElRef = useRef<Element | null>(null);
  const [composerTarget, setComposerTarget] = useState<ComposerTarget | null>(null);
  const composerElRef = useRef<Element | null>(null);
  const [openThreadId, setOpenThreadId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { listUsers().then((u) => { setUsers(u); setUser(u[0]); }); }, []);

  // A preview whose owning thread is closed is stale: no commenting at all.
  const stale = env === "preview" && threads.some((t) =>
    CLOSED_STATUSES.includes(t.status) && t.iterations.some((it) => it.sha === sha));

  const exitCommentMode = useCallback(() => {
    setCommentMode(false);
    setHoverRect(null);
    hoverElRef.current = null;
    document.body.classList.remove("ctf-commenting");
  }, []);

  const closeComposer = useCallback(() => {
    setComposerTarget(null);
    composerElRef.current = null;
  }, []);

  // Page can go stale mid-session: abort any in-progress comment flow.
  useEffect(() => {
    if (stale) { closeComposer(); exitCommentMode(); }
  }, [stale, closeComposer, exitCommentMode]);

  // Element picking while in comment mode.
  useEffect(() => {
    if (!commentMode) return;
    const onMove = (e: MouseEvent) => {
      const el = document.elementFromPoint(e.clientX, e.clientY);
      if (!el || el.closest("[data-ctf-root]") || el === document.body || el === document.documentElement) {
        setHoverRect(null);
        hoverElRef.current = null;
        return;
      }
      hoverElRef.current = el;
      setHoverRect(el.getBoundingClientRect());
    };
    const onClick = (e: MouseEvent) => {
      if ((e.target as Element).closest("[data-ctf-root]")) return;
      e.preventDefault();
      e.stopPropagation();
      const el = hoverElRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      composerElRef.current = el;
      setComposerTarget({
        selector: cssPath(el),
        label: `<${el.tagName.toLowerCase()}> ${(el as HTMLElement).innerText?.slice(0, 40) ?? ""}`.trim(),
        rect: { left: r.left, bottom: r.bottom },
      });
      exitCommentMode();
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("click", onClick, true);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("click", onClick, true);
    };
  }, [commentMode, exitCommentMode]);

  // Esc backs out one layer at a time: composer -> comment mode -> panel.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (composerTarget) closeComposer();
      else if (commentMode) exitCommentMode();
      else if (openThreadId) setOpenThreadId(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [composerTarget, commentMode, openThreadId, closeComposer, exitCommentMode]);

  const toggleCommentMode = () => {
    if (commentMode) { exitCommentMode(); return; }
    setOpenThreadId(null);  // the panel would cover click targets
    closeComposer();
    setCommentMode(true);
    document.body.classList.add("ctf-commenting");
  };

  const submit = async (text: string, threadId: string | null) => {
    if (!user) return;
    setBusy(true);
    try {
      const targetEl = threadId
        ? document.querySelector(threads.find((t) => t.id === threadId)?.targetSelector ?? "")
        : composerElRef.current;
      const capture = await buildCapture(targetEl, { sha, env });
      const r = await postComment(user.id, {
        threadId: threadId ?? undefined,
        text,
        capture,
        target: threadId ? undefined : {
          selector: composerTarget!.selector,
          label: composerTarget!.label,
        },
      });
      closeComposer();
      setOpenThreadId(r.thread.id);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const openThread = threads.find((t) => t.id === openThreadId) ?? null;

  if (!user) return null;
  return (
    <div className="ctf-root" data-ctf-root>
      <div className="ctf-bar">
        <span className="ctf-logo">💬🔧 commentToFix</span>
        <span className={`ctf-env ctf-env-${env}`}>{env} · {sha}</span>
        {stale && (
          <span className="ctf-stale"
                title="This thread is closed — refresh the live site and start a new thread">
            ⚠️ stale preview
          </span>
        )}
        <select className="ctf-user" value={user.id}
                onChange={(e) => { setUser(users.find((u) => u.id === e.target.value)!); exitCommentMode(); }}>
          {users.map((u) => <option key={u.id} value={u.id}>{u.emoji} {u.name}</option>)}
        </select>
        <button
          className={`ctf-btn ctf-mode${commentMode ? " ctf-on" : ""}`}
          disabled={user.permission === "view" || stale}
          title={user.permission === "view" ? "view-only user"
               : stale ? "Stale preview — refresh the live site to comment" : undefined}
          onClick={toggleCommentMode}
        >
          {commentMode ? "✕ Exit comment mode" : "💬 Comment"}
        </button>
        <span className="ctf-count">{threads.length} thread{threads.length === 1 ? "" : "s"}</span>
      </div>

      {hoverRect && commentMode && (
        <div className="ctf-highlight" style={{
          left: hoverRect.left + window.scrollX - 3,
          top: hoverRect.top + window.scrollY - 3,
          width: hoverRect.width + 6,
          height: hoverRect.height + 6,
        }} />
      )}

      <Markers threads={threads} openThreadId={openThreadId}
               onOpen={(id) => { closeComposer(); setOpenThreadId(id); }} />

      {composerTarget && (
        <Composer target={composerTarget} busy={busy}
                  onSubmit={(text) => submit(text, null).catch((e) => alert(e.message))}
                  onClose={closeComposer} />
      )}

      {openThread && (
        <ThreadPanel thread={openThread} users={users} user={user}
                     onClose={() => setOpenThreadId(null)}
                     onFollowUp={(text) => submit(text, openThread.id)}
                     onRefresh={() => { refresh().catch(() => {}); }} />
      )}
    </div>
  );
}
