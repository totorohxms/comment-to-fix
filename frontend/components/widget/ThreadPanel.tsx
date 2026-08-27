"use client";

// Thread detail: status timeline, iteration lineage, append-only comments,
// capture inspection, approve action, validated follow-up composer.

import { useEffect, useRef, useState } from "react";
import { approveThread, getCapture } from "@/lib/api";
import { CLOSED_STATUSES, type Thread, type ThreadComment, type User } from "@/lib/types";
import { COMMENT_MAX, validateComment } from "@/lib/validation";
import { STATUS_META, STATUS_ORDER } from "./status";

function Timeline({ thread }: { thread: Thread }) {
  const idx = STATUS_ORDER.indexOf(thread.status);
  return (
    <div className="ctf-timeline">
      {STATUS_ORDER.map((s, i) => (
        <div key={s} style={{ display: "contents" }}>
          {i > 0 && <div className="ctf-step-line" />}
          <div className={`ctf-step ${i < idx ? "ctf-step-past" : i === idx ? "ctf-step-now" : "ctf-step-future"}`}>
            <span>{STATUS_META[s][0]}</span>
            <small>{STATUS_META[s][1]}</small>
          </div>
        </div>
      ))}
    </div>
  );
}

function CaptureChip({ comment, userId }: { comment: ThreadComment; userId: string }) {
  const m = comment.captureMeta;
  if (!m) return null;
  const inspect = async () => {
    const cap = await getCapture(userId, comment.id);
    const w = window.open("", "_blank");
    if (!w) return;
    // Capture content is client-supplied data: build the page with DOM APIs
    // (textContent escapes) — never interpolate it into an HTML string.
    const doc = w.document;
    doc.title = `capture ${comment.id}`;
    doc.body.style.cssText = "font:13px ui-monospace,monospace;background:#0d1117;color:#c9d1d9;padding:16px";
    const h2 = doc.createElement("h2");
    h2.textContent = "📦 Capture bundle → agent input";
    h2.style.color = "#fff";
    doc.body.append(h2);
    const shot = cap.screenshot;
    if (typeof shot === "string" && shot.startsWith("data:image/")) {
      const img = doc.createElement("img");
      img.src = shot;
      img.style.cssText = "max-width:480px;border:1px solid #333;border-radius:8px";
      doc.body.append(img);
    }
    const pre = doc.createElement("pre");
    pre.style.whiteSpace = "pre-wrap";
    pre.textContent = JSON.stringify({ ...cap,
      screenshot: shot ? "[jpeg data url]" : null,
      domSnapshot: `[${String(cap.domSnapshot ?? "").length} bytes]` }, null, 2);
    doc.body.append(pre);
  };
  return (
    <div className="ctf-capture" onClick={inspect}
         title="click to inspect the capture bundle sent to the agent">
      📦 sha {m.sha} · {m.networkCount} reqs · {m.consoleCount} logs
      · dom {(m.domBytes / 1024).toFixed(0)}kb{m.hasScreenshot ? " · 📸" : ""} · trace {m.traceId ?? "–"}
    </div>
  );
}

function renderText(text: string) {
  const parts = text.split(/(`[^`]+`|@[a-zA-Z_][\w-]*)/g);
  return parts.map((p, i) => {
    if (p.startsWith("`") && p.endsWith("`")) return <code key={i}>{p.slice(1, -1)}</code>;
    if (p.startsWith("@")) return <span key={i} className="ctf-mention">{p}</span>;
    return p;
  });
}

export function ThreadPanel({ thread, users, user, onClose, onFollowUp, onRefresh }: {
  thread: Thread;
  users: User[];
  user: User;
  onClose: () => void;
  onFollowUp: (text: string) => Promise<void>;
  onRefresh: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo(0, listRef.current.scrollHeight);
  }, [thread.comments.length]);

  const canAct = user.permission !== "view";
  const closed = CLOSED_STATUSES.includes(thread.status);
  const [icon, label] = STATUS_META[thread.status] ?? ["", thread.status];
  const validation = validateComment(text);

  const send = async () => {
    if (validation) { setError(validation); return; }
    setBusy(true); setError(null);
    try {
      await onFollowUp(text.trim());
      setText("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    // Approve names the sha this panel is showing — if a newer preview landed
    // meanwhile, the backend rejects and the approver reviews the latest.
    try {
      await approveThread(user.id, thread.id, thread.previewSha);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      // Self-heal: a rejection usually means this panel was stale (the thread
      // moved on without an SSE update reaching us) — refetch the truth.
      onRefresh();
    }
  };

  return (
    <div className="ctf-panel">
      <div className="ctf-panel-head">
        <b>{thread.targetLabel}</b>
        <span className={`ctf-status-pill ctf-st-${thread.status}`}>{icon} {label}</span>
        <button className="ctf-x" onClick={onClose}>✕</button>
      </div>
      <Timeline thread={thread} />
      <div className="ctf-iters">
        {thread.iterations.map((it) => (
          <a key={it.sha} href={`/preview/${it.sha}`} target="_blank" className="ctf-iter">
            🌿 {it.sha} <small>← {it.parentSha}</small>
          </a>
        ))}
      </div>
      <div className="ctf-comments" ref={listRef}>
        {thread.comments.map((c) => (
          <div key={c.id} className={`ctf-comment${c.system ? " ctf-sys" : ""}`}>
            <div className="ctf-comment-head">
              {c.system ? "🤖 agent" : users.find((u) => u.id === c.userId)?.name ?? c.userId}
              <small>{new Date(c.createdAt).toLocaleTimeString()}</small>
            </div>
            <div style={{ whiteSpace: "pre-wrap" }}>{renderText(c.text)}</div>
            <CaptureChip comment={c} userId={user.id} />
          </div>
        ))}
      </div>
      <div className="ctf-actions">
        {thread.previewUrl && !["merged", "done"].includes(thread.status) && (
          <a className="ctf-btn" href={thread.previewUrl} target="_blank">🌐 Open preview</a>
        )}
        {thread.status === "preview_ready" && (
          user.permission === "approve" ? (
            <button className="ctf-btn ctf-approve" onClick={approve}>👍 Approve → open PR</button>
          ) : (
            <span className="ctf-approver-note" title="Only the engineering (approver) group can open a PR">
              ⏳ waiting for {users.filter((u) => u.permission === "approve")
                .map((u) => `@${u.id}`).join(", ") || "an approver"} to approve
            </span>
          )
        )}
        {thread.prUrl && (
          <a className="ctf-btn" href={thread.prUrl} onClick={(e) => e.preventDefault()}
             title="fake PR url (demo)">🔀 {thread.prUrl.split("/").slice(-2).join("/")}</a>
        )}
      </div>
      {canAct && !closed ? (
        <div className="ctf-composer">
          <div style={{ flex: 1 }}>
            <textarea className="ctf-input" rows={2} maxLength={COMMENT_MAX + 100}
              placeholder="Follow up… @agent to launch a fix; plain text to discuss" value={text}
              onChange={(e) => { setText(e.target.value); setError(null); }} />
            {error && <div className="ctf-error">{error}</div>}
          </div>
          <button className="ctf-btn" disabled={!!validation || busy} onClick={send}>
            {busy ? "…" : "Send"}
          </button>
        </div>
      ) : (
        <div className="ctf-readonly">
          {canAct
            ? "🔒 Thread closed — this preview is stale. Refresh the live site and start a new thread for further changes."
            : "You have view-only permission."}
        </div>
      )}
    </div>
  );
}
