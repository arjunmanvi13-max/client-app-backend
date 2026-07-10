import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from core import db, TaskCreate, TaskUpdate, CommentIn, get_current_user, now_utc

router = APIRouter(prefix="/tasks", tags=["tasks"])

@router.post("")
async def create_task(payload: TaskCreate, user: dict = Depends(get_current_user)):
    doc = {
        "id": str(uuid.uuid4()),
        "title": payload.title,
        "description": payload.description,
        "priority": payload.priority,
        "deadline": payload.deadline.isoformat() if payload.deadline else None,
        "assignee_ids": payload.assignee_ids,
        "department": payload.department,
        "follow_up_required": payload.follow_up_required,
        "status": "assigned",
        "created_by": user["id"],
        "created_by_name": user["name"],
        "created_at": now_utc().isoformat(),
        "updated_at": now_utc().isoformat(),
        "completion_remark": None,
        "proof_url": None,
        "comments": [],
    }
    await db.tasks.insert_one(doc)
    for aid in payload.assignee_ids:
        await db.notifications.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": aid,
            "type": "task_assigned",
            "title": "New task assigned",
            "message": payload.title,
            "ref_id": doc["id"],
            "read": False,
            "created_at": now_utc().isoformat(),
        })
    doc.pop("_id", None)
    return doc

@router.get("")
async def list_tasks(
    mine: bool = False,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    q = {}
    if mine:
        q["$or"] = [{"assignee_ids": user["id"]}, {"created_by": user["id"]}]
    if status:
        q["status"] = status
    if priority:
        q["priority"] = priority
    return await db.tasks.find(q, {"_id": 0}).sort("created_at", -1).to_list(1000)

@router.get("/{task_id}")
async def get_task(task_id: str, _user: dict = Depends(get_current_user)):
    doc = await db.tasks.find_one({"id": task_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Task not found")
    return doc

@router.patch("/{task_id}")
async def update_task(task_id: str, payload: TaskUpdate, _user: dict = Depends(get_current_user)):
    upd = {k: v for k, v in payload.dict().items() if v is not None}
    if not upd:
        raise HTTPException(400, "No fields to update")
    upd["updated_at"] = now_utc().isoformat()
    res = await db.tasks.update_one({"id": task_id}, {"$set": upd})
    if res.matched_count == 0:
        raise HTTPException(404, "Task not found")
    return await db.tasks.find_one({"id": task_id}, {"_id": 0})

@router.post("/{task_id}/comments")
async def add_comment(task_id: str, payload: CommentIn, user: dict = Depends(get_current_user)):
    comment = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "user_name": user["name"],
        "user_role": user["role"],
        "text": payload.text,
        "created_at": now_utc().isoformat(),
    }
    res = await db.tasks.update_one({"id": task_id}, {"$push": {"comments": comment}})
    if res.matched_count == 0:
        raise HTTPException(404, "Task not found")
    return comment
