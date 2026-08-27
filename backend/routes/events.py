"""SSE endpoint: authenticated, page-scoped, replayable.

- Auth: the browser EventSource API cannot set headers, so identity rides the
  `user` query param, validated against the user store. (The fake-auth header
  has the same trust level; a real system puts a short-lived signed token
  here or relies on a session cookie.)
- Scoping: `pageUrl` filters events to the threads relevant to that page —
  subscribers no longer receive every thread's comments.
- Replay: frames carry `id:`; on reconnect the browser resends Last-Event-ID
  and missed events are replayed from the broker's ring buffer. If the gap
  can't be verified (evicted / fresh process) the client gets `event: reset`
  and must refetch thread state from the REST API.
"""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.container import container

router = APIRouter()

def _relevant(event: dict, page_url: str | None) -> bool:
    thread = event.get("thread")
    if not page_url or not thread:
        return True
    return (thread.get("pageUrl") == page_url
            or any(page_url == f"/preview/{i['sha']}"
                   for i in thread.get("iterations", [])))

def _frame(event: dict) -> str:
    return f"id: {event['eventId']}\ndata: {json.dumps(event)}\n\n"

@router.get("/api/events")
async def events(request: Request, user: str = "", pageUrl: str | None = None):
    if not container.users.get(user):
        raise HTTPException(401, "unknown user")

    # Subscribe BEFORE computing the replay so nothing published in between
    # is lost; the sent-id watermark below dedupes the overlap.
    q = container.broker.subscribe()

    last_raw = request.headers.get("last-event-id") or request.query_params.get("lastEventId")
    try:
        last_id = int(last_raw) if last_raw is not None else None
    except ValueError:
        last_id = None

    async def stream():
        sent_until = 0
        try:
            yield "retry: 2000\n\n"
            if last_id is not None:
                missed = container.broker.replay_since(last_id)
                if missed is None:
                    yield "event: reset\ndata: {}\n\n"
                else:
                    for ev in missed:
                        sent_until = ev["eventId"]
                        if _relevant(ev, pageUrl):
                            yield _frame(ev)
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if ev["eventId"] <= sent_until:
                    continue  # already covered by the replay
                sent_until = ev["eventId"]
                if _relevant(ev, pageUrl):
                    yield _frame(ev)
        finally:
            container.broker.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})
