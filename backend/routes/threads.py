"""Thread read APIs + approve action."""

from fastapi import APIRouter, Depends, HTTPException

from backend.container import container
from backend.domain.models import User
from backend.routes.deps import auth, require_approver, require_commenter
from backend.routes.schemas import ApproveThreadRequest

router = APIRouter()

@router.get("/api/users")
def users():
    return [u.to_api() for u in container.users.list()]

@router.get("/api/meta")
def meta():
    return {"mainSha": container.main_sha,
            "agents": {"maxInflight": container.queue.max_inflight,
                       "inflight": container.queue.inflight,
                       "backlog": container.queue.backlog}}

@router.get("/api/threads")
def list_threads(pageUrl: str | None = None, user: User = Depends(auth)):
    return [t.to_api() for t in container.threads.list(pageUrl)]

@router.get("/api/threads/{thread_id}")
def get_thread(thread_id: str, user: User = Depends(auth)):
    t = container.threads.get(thread_id)
    if not t:
        raise HTTPException(404, "not found")
    return t.to_api()

@router.post("/api/threads/{thread_id}/cancel")
async def cancel(thread_id: str, user: User = Depends(auth)):
    require_commenter(user)
    t = container.threads.get(thread_id)
    if not t:
        raise HTTPException(404, "not found")
    r = container.runner.cancel_run(t, user)
    if "error" in r:
        raise HTTPException(409, r["error"])
    return container.threads.get(thread_id).to_api()

@router.post("/api/threads/{thread_id}/approve")
async def approve(thread_id: str, req: ApproveThreadRequest, user: User = Depends(auth)):
    require_approver(user)
    t = container.threads.get(thread_id)
    if not t:
        raise HTTPException(404, "not found")
    # previewSha = the sha the approver reviewed; approving a superseded
    # preview is rejected so nobody ships a sha they never looked at.
    r = container.runner.approve(t, user, req.previewSha)
    if "error" in r:
        raise HTTPException(409, r["error"])
    return container.threads.get(thread_id).to_api()
