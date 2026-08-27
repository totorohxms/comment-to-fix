"""Comment input validation, mention parsing, and capture redaction.
Limits live in constants.py; the service calls these before anything touches
the store.

SAFETY NOTE — the capture bundle is runtime surveillance of a real page:
screenshots, DOM snapshots, network URLs, and console output can all carry
secrets and PII (tokens in query strings, emails in the DOM, credentials
logged by accident). Defense layers, in order:
  1. the SDK redacts client-side before sending (best effort),
  2. redact_capture() below scrubs known secret patterns server-side,
  3. per-field caps + MAX_CAPTURE_TOTAL_BYTES bound what can be stored.
This is POC-grade. Production additionally needs: response/request BODIES off
by default, allowlist-based DOM capture, retention TTLs + deletion, encryption
at rest, and capture access restricted to the thread's participants.
"""

import json
import re

from backend.comments.constants import (
    MAX_CAPTURE_FIELD, MAX_CAPTURE_TOTAL_BYTES, MAX_CONSOLE_ENTRIES,
    MAX_DOM_BYTES, MAX_LABEL, MAX_NETWORK_ENTRIES, MAX_SCREENSHOT_BYTES,
    MAX_SELECTOR, MAX_TEXT,
)

# Explicit agent invocation: only comments mentioning @agent launch tasks.
# Everything else is human collaboration ("@evan check the preview?") and must
# never spawn, interrupt, or join an agent run.
_AGENT_MENTION = re.compile(r"(?:^|\W)@agent\b", re.IGNORECASE)

def mentions_agent(text: str) -> bool:
    return bool(_AGENT_MENTION.search(text))

class CommentError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message

def validate_text(text) -> str:
    if not isinstance(text, str):
        raise CommentError(400, "comment text must be a string")
    t = text.strip()
    if not t:
        raise CommentError(400, "comment cannot be empty")
    if len(t) > MAX_TEXT:
        raise CommentError(400, f"comment too long (max {MAX_TEXT} characters)")
    return t

def validate_target(target) -> dict:
    if not isinstance(target, dict) or not target.get("selector"):
        raise CommentError(400, "new thread needs a target element")
    selector = target["selector"]
    if not isinstance(selector, str) or len(selector) > MAX_SELECTOR:
        raise CommentError(400, f"target selector must be a string (max {MAX_SELECTOR} chars)")
    label = target.get("label")
    if label is not None and (not isinstance(label, str) or len(label) > MAX_LABEL):
        raise CommentError(400, f"target label must be a string (max {MAX_LABEL} chars)")
    return target

def validate_capture(capture) -> dict | None:
    """Sanity-cap the runtime bundle. Lists are truncated (keep the newest);
    oversized blobs are rejected — the SDK truncates before sending."""
    if capture is None:
        return None
    if not isinstance(capture, dict):
        raise CommentError(400, "capture must be an object")
    dom = capture.get("domSnapshot")
    if dom is not None and (not isinstance(dom, str) or len(dom) > MAX_DOM_BYTES):
        raise CommentError(400, f"capture domSnapshot too large (max {MAX_DOM_BYTES} bytes)")
    shot = capture.get("screenshot")
    if shot is not None and (not isinstance(shot, str) or len(shot) > MAX_SCREENSHOT_BYTES):
        raise CommentError(400, "capture screenshot too large")
    for key, cap in (("network", MAX_NETWORK_ENTRIES), ("console", MAX_CONSOLE_ENTRIES)):
        val = capture.get(key)
        if val is not None:
            if not isinstance(val, list):
                raise CommentError(400, f"capture {key} must be a list")
            capture[key] = val[-cap:]
    for key in ("sha", "url", "traceId", "sessionId"):
        val = capture.get(key)
        if val is not None and (not isinstance(val, str) or len(val) > MAX_CAPTURE_FIELD):
            raise CommentError(400, f"capture {key} must be a short string")
    if len(json.dumps(capture)) > MAX_CAPTURE_TOTAL_BYTES:
        raise CommentError(400, f"capture too large (max {MAX_CAPTURE_TOTAL_BYTES} bytes total)")
    return redact_capture(capture)

# ---- server-side redaction ---------------------------------------------------
# The SDK redacts first; this is the server's own pass so a bypassed or buggy
# client can't store obvious secrets. Pattern-based and deliberately basic.

_SECRET_PARAM = re.compile(
    r"([?&](?:authorization|cookie|password|secret|token|api[-_]?key|bearer|session[^=&]*)=)[^&#\s]+",
    re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(
    r"((?:authorization|cookie|password|secret|token|api[-_]?key|bearer)[\"']?\s*[:=]\s*)[\"']?[^\s\"',;&]+",
    re.IGNORECASE)
_JWT_LIKE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{10,}\b")
_PASSWORD_INPUT = re.compile(r'(type="password"[^>]*?value=")[^"]*(")', re.IGNORECASE)

def _scrub(text: str) -> str:
    text = _SECRET_PARAM.sub(r"\1[redacted]", text)
    text = _SECRET_ASSIGNMENT.sub(r"\1[redacted]", text)
    return _JWT_LIKE.sub("[redacted-jwt]", text)

def redact_capture(capture: dict) -> dict:
    for entry in capture.get("network") or []:
        if isinstance(entry, dict) and isinstance(entry.get("url"), str):
            entry["url"] = _scrub(entry["url"])
    for entry in capture.get("console") or []:
        if isinstance(entry, dict) and isinstance(entry.get("msg"), str):
            entry["msg"] = _scrub(entry["msg"])
    dom = capture.get("domSnapshot")
    if isinstance(dom, str):
        capture["domSnapshot"] = _PASSWORD_INPUT.sub(r"\1[redacted]\2", _scrub(dom))
    return capture
