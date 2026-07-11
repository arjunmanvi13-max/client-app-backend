from fastapi import APIRouter, Depends, HTTPException
from core import (
    db,
    get_current_user,
    normalize_notification,
    notification_filter_for_user,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(user: dict = Depends(get_current_user)):
    rows = await db.notifications.find(
        notification_filter_for_user(user),
        {"_id": 0},
    ).to_list(500)
    normalized = [normalize_notification(r) for r in rows]
    normalized.sort(key=lambda n: n.get("created_at") or "", reverse=True)
    return normalized[:200]


@router.post("/{nid}/read")
async def read_notification(nid: str, user: dict = Depends(get_current_user)):
    filt = {**notification_filter_for_user(user), "id": nid}
    result = await db.notifications.update_one(filt, {"$set": {"read": True}})
    if result.matched_count == 0:
        raise HTTPException(404, "Notification not found")
    return {"ok": True}
