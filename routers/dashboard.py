from fastapi import APIRouter, Depends
from core import db, get_current_user, now_utc

router = APIRouter(tags=["dashboard"])

@router.get("/dashboard")
async def dashboard(user: dict = Depends(get_current_user)):
    my_tasks = await db.tasks.count_documents({"assignee_ids": user["id"]})
    pending_tasks = await db.tasks.count_documents({
        "assignee_ids": user["id"],
        "status": {"$nin": ["completed", "reviewed"]},
    })
    overdue_tasks = await db.tasks.count_documents({
        "assignee_ids": user["id"],
        "status": {"$nin": ["completed", "reviewed"]},
        "deadline": {"$lt": now_utc().isoformat()},
    })
    unread = await db.notifications.count_documents({"user_id": user["id"], "read": False})
    today = now_utc().strftime("%Y-%m-%d")

    extras = {}
    if user["role"] in ("admin", "super_admin"):
        extras["total_users"] = await db.users.count_documents({})
        extras["total_tasks"] = await db.tasks.count_documents({})
        extras["pending_gate_passes"] = await db.gate_passes.count_documents({"status": "pending"})
    if user["role"] == "warden":
        extras["pending_gate_passes"] = await db.gate_passes.count_documents({"status": "pending"})
        extras["residents"] = await db.users.count_documents({"role": {"$in": ["student", "player"]}})

    return {
        "my_tasks": my_tasks,
        "pending_tasks": pending_tasks,
        "overdue_tasks": overdue_tasks,
        "unread_notifications": unread,
        "today": today,
        **extras,
    }
