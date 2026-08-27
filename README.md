# commentToFix

Comment on a live (pre-prod) site like a Google Doc — an agent captures the runtime
context, ships a fix to a preview deployment, and you iterate in the same thread
until the PR merges.

## Run locally

Two processes: the FastAPI backend and the Next.js frontend.

```bash
# backend (API on :4173)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn backend.main:app --port 4173

# frontend (app on :3000, proxies /api/* to the backend)
cd frontend && npm install && npm run dev
```

Open http://localhost:3000 — the landing page has a scripted demo walkthrough.

Or with Docker: `docker compose up --build` (frontend on :3000, backend on :4173).

Tests: `.venv/bin/pytest backend/tests` (validation, comment service, queue
semantics, full agent flows, API layer — runs in ~2s on shrunk timers) and
`cd frontend && npm test` (validation + capture selector paths).

## Demo script

1. Open the demo site, hit **💬 Comment**, click the yellow **Edit Profile** button,
   write "@agent this button style is not right" → watch the thread run
   triggered → analyzing → putting up code change → deploying → preview ready.
2. Open the preview (new sha, patched page), follow up with "@agent make it green
   and font size 16" → a new worktree branches off the preview sha.
   Only comments mentioning **@agent** launch/join agent work — plain comments
   (and @mentions of people) are collaboration and never touch the agent;
   a thread stays an `open` discussion until @agent is summoned.
   The sha you *commented on* is the iteration base: commenting on an older
   preview branches off that version (rollback; announced in the thread), while
   comments from the production page iterate from the thread's tip. The PR diff
   is always `original base sha...final preview sha`, every intermediate sha
   kept as history.
3. Comment again while the task is analyzing → it interrupts and combines; comment
   while it's coding/deploying → it queues for the next iteration.
4. Approval is role-gated: only the approver group (Evan, engineering) sees
   **Approve → open PR**; the agent auto-tags approvers when a preview lands,
   and designers see "waiting for @evan to approve". Then pr_open → merged →
   done. Closed threads reject follow-ups; their previews go read-only.
5. Repeat as **Evan (Engineer)** on the red **Export Data** button: "@agent this
   button should not show up" → the fix hides it. **Vic (Viewer)** is view-only.
6. Chaos keywords in a comment demo the failure handling: `flaky` (worker crashes
   once, retry succeeds), `fatal` (crashes every attempt → dead-letter queue;
   replay via `POST /api/queue/dlq/{task_id}/requeue`), `vanish` (worker dies
   silently; the janitor reclaims the expired lease). Inspect `GET /api/queue`.
7. Click the 📦 chip on any comment to see the raw capture bundle the agent gets
   (screenshot, last 50 network requests with trace ids, console buffer, DOM
   snapshot, sha, viewport, session).

## Layout

```
backend/                  pure API server (FastAPI)
  main.py                 app wiring + lifespan (starts the agent worker pool)
  domain/models.py        core entities: ThreadStatus enum, Thread, Comment,
                          Iteration, Patch, User (+ status sets, to_api)
  container.py            AppContainer dataclass + create_container() — the
                          whole object graph wired in one factory
  routes/                 API layer: comments, threads, previews, SSE events,
                          queue observability, health, auth deps
    schemas.py            pydantic request models (transport shape; policy
                          limits stay in comments/utils.py)
  comments/               domain logic: append-only rules, input validation,
                          per-user rate limiting
  agent/
    models.py             agent-layer types: AgentTask/TaskState (queue unit
                          of work), FixTaskSpec/FixTaskResult (launcher IO)
    agent.py              Agent: pure EXECUTOR — run the launcher, push branch,
                          deploy preview, PR side effects; returns
                          IterationOutcome, owns no records/messaging/statuses
    launchers/            TaskLauncher abstraction (base.py) + registry;
                          fake_claude.py simulates a Claude Code worktree task
                          (canned output + chaos injection). Add real Claude /
                          other runners here; pick via AGENT_LAUNCHER.
    integrations.py       GitClient / PreviewDeployer / PRService seams + fakes
    queue.py              DELIVERY — owns task state (TaskRepo): atomic CAS
                          claims, leases + heartbeats, retry → DLQ, janitor,
                          active-task/coalesce/DLQ-replay API for the runner
    runner.py             DECIDES + NARRATES — interrupt/coalesce/queue policy,
                          base-sha resolution, iteration records, system
                          comments, status pubsub, exactly-once dedup, recovery
  db/
    migrations/           real SQL schema + seeds, applied in order
    database.py           SQLite connection + migration runner
    repos.py              typed repositories (the only code touching SQL)
  demo/                   DEMO-ONLY fake app APIs (network traffic for capture)

frontend/                 Next.js app (App Router, TypeScript)
  app/                    route layer
    page.tsx              landing
    demo/profile/         the fake "Acme Social" site (production sha)
    preview/[sha]/        preview deployments (patches applied server-side)
  components/
    widget/               the commentToFix overlay: Widget (orchestrator),
                          Composer, ThreadPanel, Markers, status meta
    demo/                 DEMO-ONLY site components (ProfileSite + sections)
  lib/                    api client, capture engine (fetch/console
                          instrumentation, screenshot, DOM snapshot),
                          validation (mirrors backend limits), wire types
  hooks/                  useThreads (fetch + SSE live updates)
```

Validation is layered: pydantic schemas (`backend/routes/schemas.py`) own the
transport shape (422 at the edge); `backend/comments/utils.py` owns the policy
(limits, truncation) with `frontend/lib/validation.ts` mirroring it for
instant feedback.

**Capture safety** — the capture bundle is runtime surveillance of a real
page: screenshots, DOM, network URLs, and console output can carry secrets and
PII. Defenses (POC-grade): the SDK redacts client-side first; the server
redacts again (secret-looking query params, `key: value` assignments, JWTs,
password-input values) and enforces per-field caps plus a total bundle
ceiling. Production additionally needs request/response bodies off by default,
allowlist DOM capture, retention TTLs + deletion, encryption at rest, and
capture access scoped to thread participants. See the note in
`backend/comments/utils.py`.

Configuration is centralized: the backend reads env only in
[backend/config.py](backend/config.py) (a `.env` file at the repo root is
loaded first — copy `.env.example` and uncomment what you need: db path, queue
knobs, rate limits, CORS, logging, statsd). The frontend uses Next's own
`.env.local` (see `frontend/.env.example`). Threads, previews, and the task
queue survive restarts.

Observability (`backend/observability/`): structured logs with request-id
correlation (send `X-Request-ID`, grep it across every layer; `LOG_FORMAT=json`
for shippers), statsd metrics over UDP (per-route QPS/latency/status codes via
middleware — zero code in routes; queue depth/outcomes; `@track` for agent
internals), `/healthz` + `/readyz` for orchestrators, and unhandled errors
returned as traceable 500s carrying the request id.

## What's simulated vs real

Real: capture bundle (screenshot/network/console/DOM/sha/trace ids), append-only
threads, SSE pubsub, interrupt/coalesce/queue policy, preview lineage +
comment-sha-based iteration bases, role permissions, SQLite persistence with
migrations, durable task queue (atomic claims, leases, retries, DLQ, janitor),
input validation, rate limiting.

Simulated: the agent's reasoning (keyword → CSS patch instead of a Claude
worktree task), git/deploy/PR side effects (fake clients behind the
GitClient/PreviewDeployer/PRService interfaces), auth (header instead of real
sessions).
