"use client";

// Floating new-comment composer, pinned under the clicked element.
// Validates locally (mirrors backend limits) before allowing submit.

import { useEffect, useRef, useState } from "react";
import { COMMENT_MAX, validateComment } from "@/lib/validation";

export interface ComposerTarget {
  selector: string;
  label: string;
  rect: { left: number; bottom: number };
}

export function Composer({ target, busy, onSubmit, onClose }: {
  target: ComposerTarget;
  busy: boolean;
  onSubmit: (text: string) => void;
  onClose: () => void;
}) {
  // Prefilled with @agent — the explicit invocation that launches a fix task.
  // Deleting it turns the comment into plain discussion on the element.
  const [text, setText] = useState("@agent ");
  const [touched, setTouched] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
  }, []);

  const error = validateComment(text);
  const showError = touched && error;

  return (
    <div className="ctf-composer-float" style={{
      left: Math.min(target.rect.left + window.scrollX, window.innerWidth - 340),
      top: target.rect.bottom + window.scrollY + 8,
    }}>
      <div className="ctf-composer-head">
        <div className="ctf-composer-target">📍 {target.selector}</div>
        <button className="ctf-x" title="Cancel (Esc)" onClick={onClose}>✕</button>
      </div>
      <textarea
        ref={ref}
        className="ctf-input"
        rows={3}
        maxLength={COMMENT_MAX + 100}
        placeholder="What's wrong here? e.g. “this button style is not right” or “this button should not show up”"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => setTouched(true)}
      />
      {showError && <div className="ctf-error">{error}</div>}
      {text.length > COMMENT_MAX - 200 && (
        <div className="ctf-counter">{text.trim().length}/{COMMENT_MAX}</div>
      )}
      <div className="ctf-composer-foot">
        <small>@agent launches a fix; without it this is just discussion ·
          📸 screenshot + 🌐 requests + 🧬 DOM + sha attached · Esc to cancel</small>
        <button
          className="ctf-btn"
          disabled={!!error || busy}
          onClick={() => { setTouched(true); if (!error) onSubmit(text.trim()); }}
        >
          {busy ? "Capturing…" : "Comment"}
        </button>
      </div>
    </div>
  );
}
