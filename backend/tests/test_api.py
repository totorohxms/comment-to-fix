"""API layer: auth, permissions, HTTP shapes. Uses the real app + container
(in-memory db via CTF_DB set in conftest) with lifespan-managed workers."""

import pytest
from fastapi.testclient import TestClient

from backend.main import app

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def hdr(user):
    return {"X-CTF-User": user}

def test_auth_required_and_unknown_user_rejected(client):
    assert client.get("/api/threads").status_code == 401
    assert client.get("/api/threads", headers=hdr("nobody")).status_code == 401

def test_view_only_user_cannot_comment_but_can_read(client):
    r = client.post("/api/comments", headers=hdr("vic"),
                    json={"text": "hi", "target": {"selector": "#x"}})
    assert r.status_code == 403
    assert client.get("/api/threads", headers=hdr("vic")).status_code == 200

def test_users_and_meta(client):
    users = client.get("/api/users").json()
    assert {u["id"] for u in users} == {"dana", "evan", "vic"}
    meta = client.get("/api/meta").json()
    assert meta["mainSha"] and "agents" in meta

def test_validation_errors_surface_as_400(client):
    r = client.post("/api/comments", headers=hdr("dana"),
                    json={"text": "   ", "target": {"selector": "#x"}})
    assert r.status_code == 400 and "empty" in r.json()["detail"]

def test_comment_flow_and_capture_roundtrip(client):
    r = client.post("/api/comments", headers=hdr("dana"), json={
        "text": "@agent this button should not show up",
        "target": {"selector": "#btn-api-test", "label": "Btn"},
        "capture": {"sha": "abc1234", "url": "/demo/profile",
                    "network": [{"url": "/api/x", "status": 200}], "console": []},
    })
    assert r.status_code == 200
    body = r.json()
    thread = body["thread"]
    assert thread["status"] == "triggered"
    comment_id = body["commentId"]

    # comments are summarized in thread payloads; the raw capture has its own endpoint
    got = client.get(f"/api/threads/{thread['id']}", headers=hdr("vic")).json()
    c = next(x for x in got["comments"] if x["id"] == comment_id)
    assert c["hasCapture"] and c["captureMeta"]["sha"] == "abc1234"
    assert "capture" not in c

    cap = client.get(f"/api/comments/{comment_id}/capture", headers=hdr("vic")).json()
    assert cap["network"][0]["url"] == "/api/x"

def test_queue_endpoint_and_dlq_requeue_permissions(client):
    q = client.get("/api/queue", headers=hdr("vic"))
    assert q.status_code == 200 and "dlq" in q.json()
    # replay is a write: view-only forbidden, unknown task 409 for commenter
    assert client.post("/api/queue/dlq/task_x/requeue", headers=hdr("vic")).status_code == 403
    assert client.post("/api/queue/dlq/task_x/requeue", headers=hdr("dana")).status_code == 409

def test_events_requires_valid_user(client):
    assert client.get("/api/events").status_code == 401
    assert client.get("/api/events?user=nobody").status_code == 401

def test_unknown_preview_404(client):
    assert client.get("/api/previews/deadbeef").status_code == 404

def test_unknown_thread_404(client):
    assert client.get("/api/threads/thr_nope", headers=hdr("dana")).status_code == 404
