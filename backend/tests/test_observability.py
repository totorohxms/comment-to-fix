"""Observability: statsd wire format, @track decorator, request middleware,
health endpoints, unhandled-exception handler."""

import asyncio
import socket

import pytest
from fastapi.testclient import TestClient

from backend.observability import track
from backend.observability.statsd import StatsdClient
from backend.main import app

# ---- statsd client -----------------------------------------------------------

def test_statsd_emits_wire_format_over_udp():
    recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv.bind(("127.0.0.1", 0))
    recv.settimeout(1)
    port = recv.getsockname()[1]
    client = StatsdClient("127.0.0.1", port, prefix="t")
    client.incr("hits")
    client.timing("lat", 12.345)
    client.gauge("depth", 3)
    got = sorted(recv.recv(1024).decode() for _ in range(3))
    assert got == ["t.depth:3|g", "t.hits:1|c", "t.lat:12.35|ms"]
    recv.close()

def test_statsd_never_raises_when_nothing_listens():
    client = StatsdClient("127.0.0.1", 1, prefix="t")  # port 1: nobody home
    client.incr("hits")  # must not raise

# ---- @track ------------------------------------------------------------------

def test_track_counts_ok_and_error(monkeypatch):
    sent: list[str] = []
    from backend.observability import statsd
    monkeypatch.setattr(statsd.metrics, "_send", sent.append)

    @track("op")
    async def ok():
        return 42

    @track("op")
    async def boom():
        raise ValueError("x")

    assert asyncio.run(ok()) == 42
    with pytest.raises(ValueError):
        asyncio.run(boom())
    counters = [s for s in sent if s.endswith("|c")]
    assert any("op.ok:1" in s for s in counters)
    assert any("op.error:1" in s for s in counters)
    assert sum(1 for s in sent if "|ms" in s) == 2  # timing for both outcomes

# ---- middleware + health via the real app ------------------------------------

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def test_healthz_and_readyz(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/readyz")
    assert r.status_code == 200
    checks = r.json()["checks"]
    assert checks["db"] == "ok" and checks["agent_workers"] == "ok"

def test_request_id_generated_and_echoed(client):
    r = client.get("/healthz")
    assert len(r.headers["x-request-id"]) == 12

def test_request_id_honors_incoming_header(client):
    r = client.get("/healthz", headers={"X-Request-ID": "trace-me-123"})
    assert r.headers["x-request-id"] == "trace-me-123"

def test_unhandled_exception_returns_traceable_500():
    # a route that raises something FastAPI doesn't handle
    @app.get("/api/_test_boom")
    def boom():
        raise RuntimeError("kaboom")

    # TestClient re-raises server errors by default; a real server responds.
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/_test_boom")
    assert r.status_code == 500
    body = r.json()
    assert body["detail"] == "internal server error"
    assert body["requestId"] == r.headers["x-request-id"]

def test_http_metrics_emitted_per_request(client, monkeypatch):
    sent: list[str] = []
    from backend.observability import statsd
    monkeypatch.setattr(statsd.metrics, "_send", sent.append)
    client.get("/healthz")
    assert any("api.request.healthz.GET:1|c" in s for s in sent)
    assert any(s.startswith("ctf.api.latency.healthz:") for s in sent)
    assert any("api.status.200:1|c" in s for s in sent)
