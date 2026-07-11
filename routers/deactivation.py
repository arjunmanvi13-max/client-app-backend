"""Deactivation Approval Workflow — thin wrapper over unified approvals."""
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core import db, get_current_user, is_admin, is_super_admin, get_perm, now_utc, notify_role, notify_user

router = APIRouter(prefix="/deactivation-requests", tags=["deactivation"])


class CreateReq(BaseModel):
    player_id: str
    reason: Optional[str] = None


def _can_approve(user: dict) -> bool:
    return is_super_admin(user) or get_perm(user, "approve_requests") or get_perm(user, "approve_deactivation")


def _legacy_from_approval(doc: dict) -> dict:
    p = doc.get("payload") or {}
    return {
        "id": doc["id"],
        "player_id": p.get("person_id") or doc.get("subject_id"),
        "player_name": doc.get("subject_label"),
        "centre": p.get("centre"),
        "sport": p.get("sport"),
        "category": p.get("category"),
        "reason": doc.get("reason"),
        "status": doc.get("status"),
        "requested_by_id": doc.get("requested_by_id"),
        "requested_by_name": doc.get("requested_by_name"),
        "requested_at": doc.get("requested_at"),
        "decided_by_id": doc.get("decided_by_id"),
        "decided_by_name": doc.get("decided_by_name"),
        "decided_at": doc.get("decided_at"),
        "decision_note": doc.get("decision_note"),
        "history": doc.get("history", []),
        "comments": doc.get("comments", []),
    }


@router.post("")
async def create_request(payload: CreateReq, user: dict = Depends(get_current_user)):
    if not (is_admin(user) or get_perm(user, "edit_players")):
        raise HTTPException(403, "Permission required to request deactivation")
    player = await db.people.find_one({"id": payload.player_id, "kind": "player"})
    if not player:
        raise HTTPException(404, "Player not found")
    if player.get("status") == "deactivated":
        raise HTTPException(400, "Player already deactivated")
    existing = await db.approval_requests.find_one({
        "type": "player_deactivation",
        "subject_id": payload.player_id,
        "status": "pending",
    })
    if existing:
        raise HTTPException(400, "A pending request already exists for this player")

    from routers.approvals import _history_entry, _approval_out
    doc = {
        "id": str(uuid.uuid4()),
        "type": "player_deactivation",
        "status": "pending",
        "entity_id": "alpha",
        "subject_id": player["id"],
        "subject_label": player["name"],
        "reason": payload.reason or "",
        "payload": {
            "person_id": player["id"],
            "centre": player.get("centre"),
            "sport": player.get("sport"),
            "category": player.get("player_type"),
        },
        "requested_by_id": user["id"],
        "requested_by_name": user["name"],
        "requested_at": now_utc().isoformat(),
        "decided_by_id": None,
        "decided_by_name": None,
        "decided_at": None,
        "decision_note": None,
        "history": [_history_entry("submitted", user, payload.reason)],
        "comments": [],
    }
    await db.approval_requests.insert_one(doc)
    await notify_role(
        "super_admin",
        ntype="deactivation_request",
        title="Deactivation request",
        message=f"{user['name']} requested deactivation of {player['name']}",
        ref_id=doc["id"],
    )
    return _legacy_from_approval(doc)


@router.get("")
async def list_requests(status: Optional[str] = None, user: dict = Depends(get_current_user)):
    if not (is_super_admin(user) or is_admin(user) or get_perm(user, "edit_players")):
        raise HTTPException(403, "Not allowed")
    q: dict = {"type": "player_deactivation"}
    if status:
        q["status"] = status
    rows = await db.approval_requests.find(q, {"_id": 0}).sort("requested_at", -1).to_list(500)
    return [_legacy_from_approval(r) for r in rows]


class DecisionIn(BaseModel):
    note: Optional[str] = None


@router.post("/{req_id}/approve")
async def approve(req_id: str, payload: DecisionIn, user: dict = Depends(get_current_user)):
    if not _can_approve(user):
        raise HTTPException(403, "Super Admin approval required")
    from routers.approvals import approve as approve_request
    doc = await approve_request(req_id, payload, user)
    return _legacy_from_approval(doc)


@router.post("/{req_id}/reject")
async def reject(req_id: str, payload: DecisionIn, user: dict = Depends(get_current_user)):
    if not _can_approve(user):
        raise HTTPException(403, "Super Admin approval required")
    from routers.approvals import reject as reject_request
    doc = await reject_request(req_id, payload, user)
    return _legacy_from_approval(doc)
