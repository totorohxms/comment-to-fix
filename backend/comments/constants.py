"""Input limits for the comment API.

Mirrored by the frontend (frontend/lib/validation.ts) for instant feedback,
but the backend is the authority: these must hold even against a bypassed
client. Change a value here and update the frontend mirror in the same PR.
"""

# Comment body: long enough for a paragraph of feedback, short enough that a
# thread stays readable and the agent prompt stays bounded.
MAX_TEXT = 2000

# Target element: a CSS selector path plus a human label. Selectors longer
# than this are almost certainly malformed or adversarial.
MAX_SELECTOR = 500
MAX_LABEL = 200

# Capture bundle caps. The SDK truncates before sending (DOM at 300k,
# network ring buffer at 50, console at 100); the server caps sit above those
# so a well-behaved client never trips them, while a hostile one cannot make
# a comment row arbitrarily large.
MAX_DOM_BYTES = 400_000
MAX_SCREENSHOT_BYTES = 8_000_000     # ~half-res JPEG data URL of a large page
MAX_NETWORK_ENTRIES = 100            # lists are truncated (newest kept), not rejected
MAX_CONSOLE_ENTRIES = 200

# Short string fields inside the capture (sha, url, traceId, sessionId).
MAX_CAPTURE_FIELD = 500

# Hard ceiling on one capture bundle, serialized (screenshot + DOM + logs
# combined). Individual caps above bound each part; this bounds the sum.
MAX_CAPTURE_TOTAL_BYTES = 12_000_000
