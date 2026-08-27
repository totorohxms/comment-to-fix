"""Liveness + readiness. No auth — load balancers and orchestrators call these."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend.container import container

router = APIRouter()

@router.get("/healthz")
def healthz():
    """Liveness: the process is up and serving."""
    return {"status": "ok"}

@router.get("/readyz")
def readyz():
    """Readiness: dependencies are usable. 503 keeps traffic away until then."""
    checks = {}
    try:
        container.db.query_one("SELECT 1")
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"
    checks["agent_workers"] = "ok" if container.queue.running else "not started"
    checks["queue"] = container.queue.counts()
    ready = checks["db"] == "ok" and checks["agent_workers"] == "ok"
    return JSONResponse(status_code=200 if ready else 503,
                        content={"status": "ready" if ready else "not ready", "checks": checks})
