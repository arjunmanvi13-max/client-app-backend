from fastapi import APIRouter, Depends, HTTPException
from core import db, get_current_user, now_utc
from notifications_service import (
    normalize_notification,
    notification_filter_for_user,
    unread_count_for_user,
    mark_read,
    mark_all_read,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    unread_only: bool = False,
    user: dict = Depends(get_current_user),
):
    if user.get("role") == "coach":
        raise HTTPException(403, "Notifications are not available for coach accounts")
    filt = notification_filter_for_user(user)
    if unread_only:
        filt = {**filt, "read": False}
    rows = await db.notifications.find(filt, {"_id": 0}).to_list(500)
    normalized = [normalize_notification(r) for r in rows]
    normalized.sort(key=lambda n: n.get("created_at") or "", reverse=True)
    unread = sum(1 for n in normalized if not n.get("read"))
    return {
        "items": normalized[:200],
        "unread_count": unread,
        "total": len(normalized),
    }


@router.get("/unread-count")
async def get_unread_count(user: dict = Depends(get_current_user)):
    if user.get("role") == "coach":
        return {"unread_count": 0}
    count = await unread_count_for_user(user)
    return {"unread_count": count}


@router.post("/{nid}/read")
async def read_notification(nid: str, user: dict = Depends(get_current_user)):
    if user.get("role") == "coach":
        raise HTTPException(403, "Notifications are not available for coach accounts")
    if not await mark_read(user, nid):
        raise HTTPException(404, "Notification not found")
    return {"ok": True, "read_at": now_utc().isoformat()}


@router.post("/read-all")
async def read_all_notifications(user: dict = Depends(get_current_user)):
    if user.get("role") == "coach":
        raise HTTPException(403, "Notifications are not available for coach accounts")
    modified = await mark_all_read(user)
    return {"ok": True, "marked_read": modified}
