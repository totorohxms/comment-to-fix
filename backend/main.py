"""commentToFix backend — pure API server (FastAPI).

The web frontend is the Next.js app in frontend/ (routes, components, widget);
it proxies /api/* here. This process owns:

  models.py       typed data models (statuses, threads, comments, tasks)
  container.py    dependency wiring (fakes injected here; swap for real clients)
  routes/         API layer: comments, threads, previews, SSE events, queue
                  observability, health/readiness
  comments/       comment domain logic (append-only rules, validation, rate limit)
  agent/          Agent orchestration, pluggable task launchers, durable task
                  queue (leases, retries, DLQ, janitor), runner (interrupt policy)
  db/             SQLite database, schema migrations, repositories
  observability/  structured logging (request-id correlated), statsd metrics,
                  request middleware, @track decorator
  demo/           DEMO-ONLY fake app APIs backing the demo site's network traffic

Run: uvicorn backend.main:app --port 4173
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from backend.config import settings
from backend.observability import RequestContextMiddleware, setup_logging

setup_logging()
log = logging.getLogger("ctf.app")

from backend.container import container  # noqa: E402  (logging must be configured first)
from backend.routes import (   # noqa: E402
    comments, events, health, previews, queue as queue_routes, threads,
)
from backend.demo import api as demo_api  # noqa: E402

@asynccontextmanager
async def lifespan(app: FastAPI):
    await container.queue.start()             # agent worker pool + janitor
    recovered = container.runner.recover()    # resubmit drains lost to a restart
    log.info("startup complete: launcher=%s workers=%d recovered_threads=%d",
             container.launcher.name, container.queue.max_inflight, recovered)
    yield
    await container.queue.stop()
    log.info("shutdown complete")

app = FastAPI(title="commentToFix", lifespan=lifespan)

# ---- routes ------------------------------------------------------------------
app.include_router(health.router)
app.include_router(threads.router)
app.include_router(comments.router)
app.include_router(events.router)
app.include_router(queue_routes.router)
app.include_router(previews.router)
app.include_router(demo_api.router)  # demo fake app APIs (not core product)

@app.get("/")
def root():
    return {"service": "commentToFix API", "frontend": "run `npm run dev` in frontend/"}

# ---- middleware (last added = outermost) -------------------------------------
# Exception handling: HTTPException/validation errors keep FastAPI's structured
# responses; everything else is caught by RequestContextMiddleware, which logs
# the traceback and answers a traceable 500 carrying the request id.

class SelectiveGZip:
    """GZip everything except SSE: compressing an event stream buffers events
    inside the compressor and the client stops seeing live updates."""

    def __init__(self, app, exclude_paths=("/api/events",)):
        self.plain = app
        self.gzipped = GZipMiddleware(app, minimum_size=1024)
        self.exclude_paths = exclude_paths

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") in self.exclude_paths:
            await self.plain(scope, receive, send)
        else:
            await self.gzipped(scope, receive, send)

app.add_middleware(SelectiveGZip)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
# Outermost: request id + access log + qps/latency/status metrics for every
# route, with zero instrumentation code in the routes themselves.
app.add_middleware(RequestContextMiddleware)
