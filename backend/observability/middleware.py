"""Request observability middleware — pure ASGI (not BaseHTTPMiddleware, which
buffers and misbehaves around long-lived SSE streams).

Per request, with zero code in any route:
  - request id: honors incoming X-Request-ID, else generates; bound to the
    logging contextvar and echoed in the response header
  - access log line: method, path, status, duration
  - metrics: api.request.<route>.<method> (qps), api.latency.<route> (ms),
    api.status.<code> (error rates by code), api.error (5xx)

Durations are measured to response start, so an SSE stream that stays open for
an hour doesn't distort latency or leak a pending metric.
"""

import json
import logging
import re
import time
import uuid

from backend.observability.logging import request_id_var
from backend.observability.statsd import metrics

access_log = logging.getLogger("ctf.access")

def _route_slug(scope) -> str:
    """Metric-safe route name from the matched route template (not the raw
    path — raw paths explode metric cardinality with every sha/id)."""
    route = scope.get("route")
    path = getattr(route, "path", None) or scope.get("path", "unknown")
    slug = re.sub(r"[{}]", "", path).strip("/").replace("/", "_") or "root"
    return re.sub(r"[^a-zA-Z0-9_]", "_", slug)

class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        req_id = (headers.get(b"x-request-id") or uuid.uuid4().hex[:12].encode()).decode()
        token = request_id_var.set(req_id)
        start = time.perf_counter()
        method = scope.get("method", "?")

        response_started = False

        async def send_with_observability(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                elapsed_ms = (time.perf_counter() - start) * 1000
                status = message["status"]
                slug = _route_slug(scope)
                message.setdefault("headers", []).append((b"x-request-id", req_id.encode()))
                metrics.incr(f"api.request.{slug}.{method}")
                metrics.timing(f"api.latency.{slug}", elapsed_ms)
                metrics.incr(f"api.status.{status}")
                if status >= 500:
                    metrics.incr("api.error")
                access_log.info("%s %s %s %.1fms", method, scope.get("path"), status, elapsed_ms)
            await send(message)

        try:
            await self.app(scope, receive, send_with_observability)
        except Exception:
            # Unhandled errors: log with full traceback + request id, count,
            # and answer with a traceable 500 instead of a connection reset.
            # (FastAPI's ExceptionMiddleware already turned HTTPExceptions
            # into responses before they reach here.)
            metrics.incr("api.unhandled_exception")
            access_log.exception("%s %s crashed", method, scope.get("path"))
            if response_started:
                raise  # too late to answer; let the server close the stream
            body = json.dumps({"detail": "internal server error",
                               "requestId": req_id}).encode()
            await send_with_observability({
                "type": "http.response.start", "status": 500,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
            await send_with_observability({"type": "http.response.body", "body": body})
        finally:
            request_id_var.reset(token)
