"""Queue observability + DLQ replay."""

from fastapi import APIRouter, Depends, HTTPException

from backend.container import container
from backend.domain.models import User
from backend.routes.deps import auth, require_commenter

router = APIRouter()

@router.get("/api/queue")
def queue_state(user: User = Depends(auth)):
    return {
        "maxInflight": container.queue.max_inflight,
        "inflight": container.queue.inflight,
        "counts": container.queue.counts(),
        "leaseS": container.queue.lease_ms / 1000,
        "reaperIntervalS": container.queue.reaper_interval_s,
        "dlq": [t.to_api() for t in container.queue.list_dlq()],
    }

@router.post("/api/queue/dlq/{task_id}/requeue")
async def requeue_dlq(task_id: str, user: User = Depends(auth)):
    require_commenter(user)
    if not container.runner.requeue_from_dlq(task_id):
        raise HTTPException(409, "task is not in the DLQ")
    return container.queue.get_task(task_id).to_api()
