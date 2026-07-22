from fastapi import APIRouter, Depends, Query
from typing import Optional
from core import db, get_current_user, now_utc, is_super_admin
from notifications_service import unread_count_for_user
from dashboard_mvp import build_mvp_dashboard

router = APIRouter(tags=["dashboard"])

@router.get("/dashboard")
async def dashboard(user: dict = Depends(get_current_user)):
    uid = user["id"]
    my_tasks = await db.tasks.count_documents({"$or": [{"assignee_id": uid}, {"assignee_ids": uid}]})
    pending_tasks = await db.tasks.count_documents({
        "$or": [{"assignee_id": uid}, {"assignee_ids": uid}],
        "status": {"$nin": ["completed", "reviewed", "cancelled"]},
    })
    overdue_tasks = await db.tasks.count_documents({
        "$and": [
            {"$or": [{"assignee_id": uid}, {"assignee_ids": uid}]},
            {"status": {"$nin": ["completed", "reviewed", "cancelled"]}},
            {"$or": [
                {"due_date": {"$lt": now_utc().isoformat()}},
                {"deadline": {"$lt": now_utc().isoformat()}},
            ]},
        ],
    })
    unread = await unread_count_for_user(user)
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


@router.get("/dashboard/mvp")
async def dashboard_mvp(
    user: dict = Depends(get_current_user),
    entity: Optional[str] = Query(None, description="pws | alpha | both — Super Admin only"),
):
    """Role-based dashboard MVP — lightweight tiles, no advanced financial analytics."""
    if entity and not is_super_admin(user):
        entity = None
    return await build_mvp_dashboard(user, entity)


@router.get("/dashboard/super-admin-metrics")
async def dashboard_super_admin_metrics(
    user: dict = Depends(get_current_user),
    entity: Optional[str] = Query(None, description="pws | alpha | both"),
):
    from academy_structure import assert_super_admin, build_super_admin_metrics
    assert_super_admin(user)
    return await build_super_admin_metrics(entity)
