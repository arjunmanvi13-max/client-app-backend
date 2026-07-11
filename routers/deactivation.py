"""Deactivation Approval Workflow.

Admin requests deactivation -> Super Admin approves/rejects.
"""
import uuid
from typing import Optional, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core import db, get_current_user, is_admin, is_super_admin, get_perm, now_utc, notify_role, notify_user

router = APIRouter(prefix="/deactivation-requests", tags=["deactivation"])


class CreateReq(BaseModel):
    player_id: str
    reason: Optional[str] = None


@router.post("")
async def create_request(payload: CreateReq, user: dict = Depends(get_current_user)):
    # Anyone with edit_players or admin can request; Super Admin should use the direct deactivate endpoint instead.
    if not (is_admin(user) or get_perm(user, "edit_players")):
        raise HTTPException(403, "Permission required to request deactivation")
    player = await db.people.find_one({"id": payload.player_id, "kind": "player"})
    if not player:
        raise HTTPException(404, "Player not found")
    if player.get("status") == "deactivated":
        raise HTTPException(400, "Player already deactivated")
    # Avoid duplicate pending request
    existing = await db.deactivation_requests.find_one({"player_id": payload.player_id, "status": "pending"})
    if existing:
        raise HTTPException(400, "A pending request already exists for this player")
    doc = {
        "id": str(uuid.uuid4()),
        "player_id": player["id"],
        "player_name": player["name"],
        "centre": player.get("centre"),
        "sport": player.get("sport"),
        "category": player.get("player_type"),
        "reason": payload.reason,
        "status": "pending",
        "requested_by_id": user["id"],
        "requested_by_name": user["name"],
        "requested_at": now_utc().isoformat(),
        "decided_by_id": None,
        "decided_by_name": None,
        "decided_at": None,
        "decision_note": None,
    }
    await db.deactivation_requests.insert_one(doc)
    # Notify super admin
    await notify_role(
        "super_admin",
        ntype="deactivation_request",
        title="Deactivation request",
        message=f"{user['name']} requested deactivation of {player['name']}",
        ref_id=doc["id"],
    )
    doc.pop("_id", None)
    return doc


@router.get("")
async def list_requests(status: Optional[str] = None, user: dict = Depends(get_current_user)):
    if not (is_super_admin(user) or is_admin(user) or get_perm(user, "edit_players")):
        raise HTTPException(403, "Not allowed")
    q: dict = {}
    if status: q["status"] = status
    return await db.deactivation_requests.find(q, {"_id": 0}).sort("requested_at", -1).to_list(500)


class DecisionIn(BaseModel):
    note: Optional[str] = None


def _require_approver(user: dict):
    if not (is_super_admin(user) or get_perm(user, "approve_deactivation")):
        raise HTTPException(403, "Super Admin approval required")


@router.post("/{req_id}/approve")
async def approve(req_id: str, payload: DecisionIn, user: dict = Depends(get_current_user)):
    _require_approver(user)
    req = await db.deactivation_requests.find_one({"id": req_id})
    if not req:
        raise HTTPException(404, "Request not found")
    if req["status"] != "pending":
        raise HTTPException(400, f"Request already {req['status']}")
    await db.deactivation_requests.update_one({"id": req_id}, {"$set": {
        "status": "approved",
        "decided_by_id": user["id"],
        "decided_by_name": user["name"],
        "decided_at": now_utc().isoformat(),
        "decision_note": payload.note,
    }})
    # Apply player deactivation
    await db.people.update_one({"id": req["player_id"]}, {"$set": {"status": "deactivated"}})
    # Notify requester
    await notify_user(
        req["requested_by_id"],
        ntype="deactivation_approved",
        title="Deactivation approved",
        message=f"{req['player_name']} has been deactivated by {user['name']}",
        ref_id=req_id,
    )
    return await db.deactivation_requests.find_one({"id": req_id}, {"_id": 0})


@router.post("/{req_id}/reject")
async def reject(req_id: str, payload: DecisionIn, user: dict = Depends(get_current_user)):
    _require_approver(user)
    req = await db.deactivation_requests.find_one({"id": req_id})
    if not req:
        raise HTTPException(404, "Request not found")
    if req["status"] != "pending":
        raise HTTPException(400, f"Request already {req['status']}")
    await db.deactivation_requests.update_one({"id": req_id}, {"$set": {
        "status": "rejected",
        "decided_by_id": user["id"],
        "decided_by_name": user["name"],
        "decided_at": now_utc().isoformat(),
        "decision_note": payload.note,
    }})
    await notify_user(
        req["requested_by_id"],
        ntype="deactivation_rejected",
        title="Deactivation rejected",
        message=f"{req['player_name']} remains active",
        ref_id=req_id,
    )
    return await db.deactivation_requests.find_one({"id": req_id}, {"_id": 0})
