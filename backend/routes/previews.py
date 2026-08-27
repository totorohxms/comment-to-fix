"""Preview deployment data: the frontend's /preview/[sha] route fetches the
accumulated patch set for a sha and renders the site with it applied."""

from fastapi import APIRouter, HTTPException

from backend.container import container

router = APIRouter()

@router.get("/api/previews/{sha}")
def preview(sha: str):
    patches = container.patches.get(sha)
    if patches is None:
        raise HTTPException(404, "unknown preview sha")
    return {"sha": sha, "css": "\n".join(p.css for p in patches),
            "summaries": [p.summary for p in patches]}
