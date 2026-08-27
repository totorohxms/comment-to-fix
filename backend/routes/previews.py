"""Preview deployment data: the frontend's /preview/[sha] route fetches the
accumulated patch set for a sha and renders the site with it applied."""

from fastapi import APIRouter, HTTPException

from backend.container import container

router = APIRouter()

@router.get("/api/previews/live/{thread_id}")
def live_preview(thread_id: str):
    """The thread's living preview: always resolves to the current tip.

    Backs the stable /preview/live/<thread> URL — one link per thread that
    keeps up with its own iterations, while /preview/<sha> permalinks stay
    frozen as version history.
    """
    thread = container.threads.get(thread_id)
    if thread is None or not thread.preview_sha:
        raise HTTPException(404, "thread has no preview yet")
    patches = container.patches.get(thread.preview_sha)
    if patches is None:
        raise HTTPException(404, "unknown preview sha")
    return {"sha": thread.preview_sha, "css": "\n".join(p.css for p in patches),
            "summaries": [p.summary for p in patches]}

@router.get("/api/previews/{sha}")
def preview(sha: str):
    patches = container.patches.get(sha)
    if patches is None:
        raise HTTPException(404, "unknown preview sha")
    return {"sha": sha, "css": "\n".join(p.css for p in patches),
            "summaries": [p.summary for p in patches]}
