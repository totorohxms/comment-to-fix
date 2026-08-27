// Runtime capture engine: instruments fetch + console into ring buffers and
// builds the bundle handed to the agent (screenshot, last 50 network requests
// with trace ids, console tail, DOM snapshot, sha, viewport, session).

import type { CaptureBundle } from "./types";

const NETWORK_LIMIT = 50;
const CONSOLE_LIMIT = 100;
const DOM_LIMIT = 300_000;
const REDACT = /(authorization|cookie|token|password|secret|api[-_]?key)/i;

interface NetEntry {
  url: string; method: string; status: number | string;
  ms: number; traceId: string; at: number; error?: string;
}
interface ConEntry { lvl: string; msg: string; at: number }

const netBuf: NetEntry[] = [];
const conBuf: ConEntry[] = [];
export const sessionId = "sess_" + Math.random().toString(36).slice(2, 10);

let installed = false;

/** Patch fetch + console once per page. Idempotent; client-only. */
export function installCapture(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;

  const origFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const traceId = "trc_" + Math.random().toString(36).slice(2, 12);
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    // Inject trace headers only on same-origin requests: custom headers turn
    // cross-origin requests into CORS preflights and can break third-party
    // calls on a host site. The buffer still records everything.
    let sameOrigin = false;
    try { sameOrigin = new URL(url, location.href).origin === location.origin; } catch { /* opaque url */ }
    if (sameOrigin) {
      init.headers = { ...(init.headers as object ?? {}), "x-trace-id": traceId, "x-session-id": sessionId };
    }
    const start = performance.now();
    const push = (entry: NetEntry) => {
      netBuf.push(entry);
      if (netBuf.length > NETWORK_LIMIT) netBuf.shift();
    };
    try {
      const res = await origFetch(input, init);
      push({ url, method: init.method ?? "GET", status: res.status,
             ms: Math.round(performance.now() - start), traceId, at: Date.now() });
      return res;
    } catch (err) {
      push({ url, method: init.method ?? "GET", status: "ERR", error: String(err),
             ms: Math.round(performance.now() - start), traceId, at: Date.now() });
      throw err;
    }
  };

  (["log", "warn", "error", "info"] as const).forEach((lvl) => {
    const orig = console[lvl].bind(console);
    console[lvl] = (...args: unknown[]) => {
      const msg = args.map((a) => {
        try { return typeof a === "string" ? a : JSON.stringify(a); } catch { return String(a); }
      }).join(" ").slice(0, 500);
      conBuf.push({ lvl, msg, at: Date.now() });
      if (conBuf.length > CONSOLE_LIMIT) conBuf.shift();
      orig(...args);
    };
  });
}

// CSS.escape with a fallback for environments without the CSS global (jsdom, old browsers).
const cssEscape = (s: string): string =>
  typeof CSS !== "undefined" && CSS.escape ? CSS.escape(s) : s.replace(/[^a-zA-Z0-9_-]/g, "\\$&");

/** Stable-ish CSS selector path for an element (ids win; ctf classes skipped). */
export function cssPath(el: Element): string {
  if (el.id) return "#" + cssEscape(el.id);
  const parts: string[] = [];
  let node: Element | null = el;
  while (node && node.nodeType === 1 && node !== document.body) {
    if (node.id) { parts.unshift("#" + cssEscape(node.id)); break; }
    let sel = node.tagName.toLowerCase();
    const cls = [...node.classList].filter((c) => !c.startsWith("ctf-")).slice(0, 2);
    if (cls.length) sel += "." + cls.map(cssEscape).join(".");
    const parent: Element | null = node.parentElement;
    if (parent) {
      const sibs = [...parent.children].filter((s) => s.tagName === node!.tagName);
      if (sibs.length > 1) sel += `:nth-of-type(${sibs.indexOf(node) + 1})`;
    }
    parts.unshift(sel);
    node = parent;
  }
  return parts.join(" > ");
}

export async function buildCapture(
  targetEl: Element | null,
  page: { sha: string; env: string },
): Promise<CaptureBundle> {
  let screenshot: string | null = null;
  try {
    const html2canvas = (await import("html2canvas")).default;
    const canvas = await html2canvas(document.body, {
      scale: 0.5,
      logging: false,
      ignoreElements: (el) => [...el.classList].some((c) => c.startsWith("ctf-")),
    });
    screenshot = canvas.toDataURL("image/jpeg", 0.6);
  } catch { /* screenshot is best-effort */ }

  const clone = document.documentElement.cloneNode(true) as HTMLElement;
  clone.querySelectorAll('[class*="ctf-"], script').forEach((n) => n.remove());
  let dom = clone.outerHTML;
  if (dom.length > DOM_LIMIT) dom = dom.slice(0, DOM_LIMIT) + "\n<!-- truncated -->";

  return {
    sha: page.sha,
    env: page.env,
    url: location.pathname,
    sessionId,
    traceId: netBuf.length ? netBuf[netBuf.length - 1].traceId : null,
    time: new Date().toISOString(),
    viewport: { w: innerWidth, h: innerHeight, dpr: devicePixelRatio },
    userAgent: navigator.userAgent,
    target: targetEl
      ? { selector: cssPath(targetEl),
          rect: targetEl.getBoundingClientRect().toJSON(),
          text: (targetEl as HTMLElement).innerText?.slice(0, 120) }
      : null,
    network: netBuf.map((e) => ({ ...e, url: REDACT.test(e.url) ? "[redacted]" : e.url })),
    console: [...conBuf],
    domSnapshot: dom,
    screenshot,
  };
}
