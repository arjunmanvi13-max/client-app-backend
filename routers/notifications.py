from fastapi import APIRouter, Depends
from core import db, get_current_user, now_utc

router = APIRouter(prefix="/notifications", tags=["notifications"])

@router.get("")
async def list_notifications(user: dict = Depends(get_current_user)):
    return await db.notifications.find({"user_id": user["id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)

@router.post("/{nid}/read")
async def read_notification(nid: str, user: dict = Depends(get_current_user)):
    await db.notifications.update_one({"id": nid, "user_id": user["id"]}, {"$set": {"read": True}})
    return {"ok": True}
