"""Comment write API (append-only) + capture-bundle inspection."""

from fastapi import APIRouter, Depends, HTTPException

from backend.container import container
from backend.comments.utils import CommentError
from backend.domain.models import User
from backend.routes.deps import auth, require_commenter
from backend.routes.schemas import PostCommentRequest

router = APIRouter()

@router.post("/api/comments")
async def post_comment(req: PostCommentRequest, user: User = Depends(auth)):
    # async so the runner can schedule agent tasks on the main event loop
    require_commenter(user)
    try:
        thread, comment = container.comment_service.post_comment(user, req.to_body())
    except CommentError as e:
        raise HTTPException(e.status_code, e.message)
    container.runner.on_comment(thread, comment)
    return {"thread": container.threads.get(thread.id).to_api(), "commentId": comment.id}

@router.get("/api/comments/{comment_id}/capture")
def get_capture(comment_id: str, user: User = Depends(auth)):
    c = container.comments.get(comment_id)
    if not c:
        raise HTTPException(404, "not found")
    return c.capture or {}
