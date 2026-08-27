"""In-process pubsub for thread status updates (the diagram's "publish status").

Events carry monotonically increasing ids and are kept in a bounded ring
buffer, so a reconnecting SSE client (the browser sends Last-Event-ID
automatically) replays what it missed; a client whose id has been evicted is
told to resync from the REST API instead. Swap for Redis streams / Kafka when
the API layer scales past one process — the id/replay contract stays the same.
"""

import asyncio
from collections import deque
from itertools import count

from backend.observability import metrics

class StatusBroker:
    def __init__(self, history: int = 500):
        self._subscribers: set[asyncio.Queue] = set()
        self._events: deque[dict] = deque(maxlen=history)
        self._ids = count(1)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        metrics.gauge("sse.subscribers", len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)
        metrics.gauge("sse.subscribers", len(self._subscribers))

    def publish(self, event: dict) -> None:
        event = {**event, "eventId": next(self._ids)}
        self._events.append(event)
        for q in list(self._subscribers):
            q.put_nowait(event)

    def replay_since(self, last_id: int) -> list[dict] | None:
        """Events newer than last_id, or None when the gap can't be verified
        (id evicted from the ring, or a fresh process) — the client must
        resync its state from the REST API in that case."""
        if not self._events:
            return None if last_id > 0 else []
        oldest = self._events[0]["eventId"]
        if last_id + 1 < oldest:
            return None
        return [e for e in self._events if e["eventId"] > last_id]
