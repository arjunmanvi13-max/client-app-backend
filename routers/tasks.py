import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from core import (
    db,
    TaskCreate,
    TaskUpdate,
    CommentIn,
    TASK_STATUSES,
    TASK_STATUS_ALIASES,
    get_current_user,
    get_perm,
    is_super_admin,
    now_utc,
)
from notifications_service import send_notification

router = APIRouter(prefix="/tasks", tags=["tasks"])

OPEN_STATUSES = ("open", "assigned", "in_progress", "blocked", "delayed")
DONE_STATUSES = ("completed", "reviewed", "cancelled")


def _normalize_status(status: Optional[str]) -> str:
    if not status:
        return "open"
    return TASK_STATUS_ALIASES.get(status, status)


from rbac.guards import can_supervise_tasks as _can_supervise_tasks


def _task_visibility_filter(user: dict) -> dict:
    if _can_supervise_tasks(user):
        return {}
    return {
        "$or": [
            {"created_by": user["id"]},
            {"assignee_id": user["id"]},
            {"assignee_ids": user["id"]},
        ]
    }


def _assert_task_access(user: dict, task: dict):
    if _can_supervise_tasks(user):
        return
    uid = user["id"]
    if task.get("created_by") == uid:
        return
    if task.get("assignee_id") == uid:
        return
    if uid in (task.get("assignee_ids") or []):
        return
    raise HTTPException(403, "Not allowed to view this task")


async def _resolve_assignees(payload: TaskCreate | TaskUpdate, existing: Optional[dict] = None) -> tuple[Optional[str], list]:
    assignee_ids = list(payload.assignee_ids or (existing or {}).get("assignee_ids") or [])
    assignee_id = payload.assignee_id if hasattr(payload, "assignee_id") and payload.assignee_id is not None else (existing or {}).get("assignee_id")
    if payload.assignee_id:
        if payload.assignee_id not in assignee_ids:
            assignee_ids.insert(0, payload.assignee_id)
        assignee_id = payload.assignee_id
    elif assignee_ids and not assignee_id:
        assignee_id = assignee_ids[0]
    return assignee_id, assignee_ids


def _resolve_due_date(payload: TaskCreate | TaskUpdate, existing: Optional[dict] = None) -> Optional[str]:
    due = getattr(payload, "due_date", None) or getattr(payload, "deadline", None)
    if due is None and existing:
        return existing.get("due_date") or existing.get("deadline")
    return due.isoformat() if due else None


def _task_out(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k != "_id"}
    status = _normalize_status(out.get("status"))
    out["status"] = status
    due = out.get("due_date") or out.get("deadline")
    out["due_date"] = due
    out["deadline"] = due
    if not out.get("assignee_id") and out.get("assignee_ids"):
        out["assignee_id"] = out["assignee_ids"][0]
    if out.get("assignee_id") and not out.get("assignee_name"):
        # assignee_name filled on create/update when possible
        pass
    if status in ("completed",) and not out.get("completed_at") and out.get("updated_at"):
        # legacy completed tasks may lack completed_at
        pass
    return out


@router.post("")
async def create_task(payload: TaskCreate, user: dict = Depends(get_current_user)):
    assignee_id, assignee_ids = await _resolve_assignees(payload)
    due_date = _resolve_due_date(payload)
    assignee_name = None
    if assignee_id:
        assignee = await db.users.find_one({"id": assignee_id}, {"_id": 0, "name": 1})
        assignee_name = assignee["name"] if assignee else None
    doc = {
        "id": str(uuid.uuid4()),
        "title": payload.title.strip(),
        "description": payload.description or "",
        "entity_id": payload.entity_id,
        "priority": payload.priority,
        "due_date": due_date,
        "deadline": due_date,
        "assignee_id": assignee_id,
        "assignee_name": assignee_name,
        "assignee_ids": assignee_ids,
        "department": payload.department,
        "follow_up_required": payload.follow_up_required,
        "status": "open",
        "created_by": user["id"],
        "created_by_name": user["name"],
        "created_at": now_utc().isoformat(),
        "updated_at": now_utc().isoformat(),
        "completed_at": None,
        "completion_remark": None,
        "proof_url": None,
        "comments": [],
    }
    await db.tasks.insert_one(doc)
    for aid in assignee_ids:
        if aid == user["id"]:
            continue
        await send_notification(
            aid,
            ntype="task_assigned",
            title="New task assigned",
            message=payload.title,
            ref_id=doc["id"],
            ref_type="task",
            entity_id=payload.entity_id,
        )
    return _task_out(doc)


@router.get("")
async def list_tasks(
    mine: bool = False,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    entity_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    q = _task_visibility_filter(user)
    if mine:
        mine_q = {"$or": [{"assignee_id": user["id"]}, {"assignee_ids": user["id"]}]}
        q = {"$and": [q, mine_q]} if q else mine_q
    if status:
        normalized = _normalize_status(status)
        legacy = [status] if status != normalized else []
        if normalized == "open":
            legacy.extend(["assigned"])
        elif normalized == "blocked":
            legacy.extend(["delayed"])
        elif normalized == "completed":
            legacy.extend(["reviewed"])
        q["status"] = {"$in": [normalized, *legacy]}
    if priority:
        q["priority"] = priority
    if entity_id:
        q["entity_id"] = entity_id
    rows = await db.tasks.find(q, {"_id": 0}).sort("created_at", -1).to_list(1000)
    return [_task_out(r) for r in rows]


@router.get("/{task_id}")
async def get_task(task_id: str, user: dict = Depends(get_current_user)):
    doc = await db.tasks.find_one({"id": task_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Task not found")
    _assert_task_access(user, doc)
    return _task_out(doc)


@router.patch("/{task_id}")
async def update_task(task_id: str, payload: TaskUpdate, user: dict = Depends(get_current_user)):
    existing = await db.tasks.find_one({"id": task_id}, {"_id": 0})
    if not existing:
        raise HTTPException(404, "Task not found")
    _assert_task_access(user, existing)

    upd = {k: v for k, v in payload.dict().items() if v is not None}
    if not upd:
        raise HTTPException(400, "No fields to update")

    if "status" in upd:
        upd["status"] = _normalize_status(upd["status"])
        if upd["status"] not in TASK_STATUSES:
            raise HTTPException(400, f"Invalid status. Use one of: {', '.join(TASK_STATUSES)}")
        if upd["status"] == "completed":
            upd["completed_at"] = now_utc().isoformat()
        elif upd["status"] in ("open", "in_progress", "blocked", "cancelled"):
            upd["completed_at"] = None

    if "due_date" in upd or "deadline" in upd:
        due_date = _resolve_due_date(payload, existing)
        upd["due_date"] = due_date
        upd["deadline"] = due_date

    if "assignee_id" in upd or "assignee_ids" in upd:
        assignee_id, assignee_ids = await _resolve_assignees(payload, existing)
        upd["assignee_id"] = assignee_id
        upd["assignee_ids"] = assignee_ids
        if assignee_id:
            assignee = await db.users.find_one({"id": assignee_id}, {"_id": 0, "name": 1})
            upd["assignee_name"] = assignee["name"] if assignee else None

    upd["updated_at"] = now_utc().isoformat()
    await db.tasks.update_one({"id": task_id}, {"$set": upd})
    doc = await db.tasks.find_one({"id": task_id}, {"_id": 0})
    return _task_out(doc)


@router.post("/{task_id}/comments")
async def add_comment(task_id: str, payload: CommentIn, user: dict = Depends(get_current_user)):
    existing = await db.tasks.find_one({"id": task_id}, {"_id": 0})
    if not existing:
        raise HTTPException(404, "Task not found")
    _assert_task_access(user, existing)
    comment = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "user_name": user["name"],
        "user_role": user["role"],
        "text": payload.text.strip(),
        "created_at": now_utc().isoformat(),
    }
    await db.tasks.update_one({"id": task_id}, {"$push": {"comments": comment}})
    return comment
