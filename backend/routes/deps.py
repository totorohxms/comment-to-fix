"""Shared route dependencies: fake auth via the X-CTF-User header."""

from fastapi import Header, HTTPException

from backend.container import container
from backend.domain.models import User

def auth(x_ctf_user: str = Header(default="")) -> User:
    user = container.users.get(x_ctf_user)
    if not user:
        raise HTTPException(401, "unknown user")
    return user

def require_commenter(user: User) -> User:
    if not user.can_comment:
        raise HTTPException(403, f"{user.name} has view-only permission")
    return user

def require_approver(user: User) -> User:
    if not user.can_approve:
        raise HTTPException(403, f"{user.name} cannot approve — only the "
                                 "engineering (approver) group can open a PR")
    return user
