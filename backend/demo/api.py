"""DEMO-ONLY: fake app APIs for the Acme Social page, so the SDK's network
capture buffer has real traffic to record. Not part of the core product."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/demo")

@router.get("/profile")
def profile():
    return {"id": "u_182", "name": "Maya Chen", "role": "Staff Product Designer",
            "followers": 1284, "following": 311, "projects": 42}

@router.get("/activity")
def activity():
    return [
        {"t": "Shipped “Dark mode v2”", "at": "2026-08-25"},
        {"t": "Commented on “Nav redesign”", "at": "2026-08-24"},
        {"t": "Starred “design-tokens”", "at": "2026-08-22"},
    ]

@router.get("/flags")
def flags():
    return {"exportData": True, "betaBadge": False}
