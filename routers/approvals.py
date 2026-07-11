"""Unified approval workflow for sensitive operations."""
import uuid
from typing import Optional, Literal, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from core import (
    db,
    get_current_user,
    get_perm,
    is_super_admin,
    is_admin,
    now_utc,
    CommentIn,
)
from notifications_service import send_notification, send_to_role

router = APIRouter(prefix="/approval-requests", tags=["approvals"])

APPROVAL_TYPES = (
    "student_deactivation",
    "player_deactivation",
    "fee_concession",
    "refund",
)
APPROVAL_STATUSES = ("pending", "approved", "rejected", "cancelled")


def _can_approve(user: dict) -> bool:
    return is_super_admin(user) or get_perm(user, "approve_requests") or get_perm(user, "approve_deactivation")


def _can_view_approvals(user: dict) -> bool:
    return _can_approve(user) or is_admin(user) or get_perm(user, "edit_players") or get_perm(user, "edit_students") or get_perm(user, "edit_fees")


def _visibility_filter(user: dict) -> dict:
    if _can_approve(user) or is_admin(user):
        return {}
    return {"requested_by_id": user["id"]}


def _history_entry(action: str, user: dict, note: Optional[str] = None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "action": action,
        "user_id": user["id"],
        "user_name": user["name"],
        "note": note,
        "at": now_utc().isoformat(),
    }


def _approval_out(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k != "_id"}
    out.setdefault("history", [])
    out.setdefault("comments", [])
    return out


class ApprovalCreate(BaseModel):
    type: Literal["student_deactivation", "player_deactivation", "fee_concession", "refund"]
    subject_id: str
    reason: str = Field(min_length=3)
    payload: dict = Field(default_factory=dict)


class DecisionIn(BaseModel):
    note: Optional[str] = None


async def _load_person(subject_id: str, kind: str) -> dict:
    person = await db.people.find_one({"id": subject_id, "kind": kind}, {"_id": 0})
    if not person:
        raise HTTPException(404, f"{kind.title()} not found")
    return person


async def _validate_create(payload: ApprovalCreate, user: dict) -> tuple[dict, str, str, dict]:
    """Return (subject_doc_or_stub, subject_label, entity_id, normalized_payload)."""
    p = dict(payload.payload or {})
    if payload.type == "student_deactivation":
        if not (is_admin(user) or get_perm(user, "edit_students")):
            raise HTTPException(403, "Permission required to request student deactivation")
        person = await _load_person(payload.subject_id, "student")
        if person.get("status") == "deactivated":
            raise HTTPException(400, "Student already deactivated")
        entity_id = "pws"
        label = person["name"]
        p["person_id"] = person["id"]
        return person, label, entity_id, p

    if payload.type == "player_deactivation":
        if not (is_admin(user) or get_perm(user, "edit_players")):
            raise HTTPException(403, "Permission required to request player deactivation")
        person = await _load_person(payload.subject_id, "player")
        if person.get("status") == "deactivated":
            raise HTTPException(400, "Player already deactivated")
        entity_id = "alpha"
        label = person["name"]
        p.update({
            "person_id": person["id"],
            "centre": person.get("centre"),
            "sport": person.get("sport"),
            "category": person.get("player_type"),
        })
        return person, label, entity_id, p

    if payload.type == "fee_concession":
        if not (get_perm(user, "edit_fees") or get_perm(user, "collect_fees")):
            raise HTTPException(403, "Permission required to request fee concession")
        fee = await db.fees.find_one({"id": payload.subject_id}, {"_id": 0})
        if not fee:
            raise HTTPException(404, "Fee not found")
        if fee.get("status") == "paid":
            raise HTTPException(400, "Cannot request concession on a paid fee")
        discount_amount = int(p.get("discount_amount") or 0)
        if discount_amount <= 0:
            raise HTTPException(400, "discount_amount required in payload")
        entity_id = fee.get("entity_id") or ("pws" if fee.get("organization") == "PWS" else "alpha")
        label = f"{fee.get('person_name', 'Fee')} · ₹{discount_amount} off"
        p.setdefault("fee_id", fee["id"])
        p.setdefault("discount_amount", discount_amount)
        return fee, label, entity_id, p

    if payload.type == "refund":
        if not (is_super_admin(user) or get_perm(user, "edit_fees") or user.get("role") in ("principal", "vice_principal")):
            raise HTTPException(403, "Permission required to request refund")
        invoice_id = payload.subject_id
        payment_id = p.get("payment_id")
        amount = int(p.get("amount") or 0)
        if not payment_id or amount <= 0:
            raise HTTPException(400, "payload must include payment_id and amount")
        inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not inv:
            raise HTTPException(404, "Invoice not found")
        payment = await db.payments.find_one({"id": payment_id, "invoice_id": invoice_id}, {"_id": 0})
        if not payment:
            raise HTTPException(404, "Payment not found on this invoice")
        entity_id = inv.get("entity_id", "pws")
        label = f"Refund ₹{amount} · {inv.get('invoice_number', invoice_id)}"
        p.setdefault("invoice_id", invoice_id)
        return inv, label, entity_id, p

    raise HTTPException(400, "Unknown approval type")


async def _apply_approval(req: dict) -> None:
    t = req["type"]
    p = req.get("payload") or {}

    if t in ("student_deactivation", "player_deactivation"):
        pid = p.get("person_id") or req["subject_id"]
        await db.people.update_one({"id": pid}, {"$set": {"status": "deactivated"}})
        person = await db.people.find_one({"id": pid}, {"_id": 0})
        if person and person.get("kind") == "staff":
            from routers.people import ensure_staff_user_account
            await ensure_staff_user_account(person)
        return

    if t == "fee_concession":
        fee_id = p.get("fee_id") or req["subject_id"]
        fee = await db.fees.find_one({"id": fee_id})
        if not fee:
            raise HTTPException(404, "Fee not found")
        discount_amount = int(p.get("discount_amount") or 0)
        new_amt = p.get("new_amount_due")
        if new_amt is None:
            new_amt = max(0, int(fee.get("amount_due") or 0) - discount_amount)
        await db.fees.update_one({"id": fee_id}, {"$set": {
            "amount_due": new_amt,
            "discount_applied": int(fee.get("discount_applied") or 0) + discount_amount,
            "discount_reason": req.get("reason"),
            "discounted_by_id": req.get("decided_by_id"),
            "discounted_by_name": req.get("decided_by_name"),
            "discounted_at": now_utc().isoformat(),
        }})
        return

    if t == "refund":
        from routers.invoices import execute_refund
        await execute_refund(
            invoice_id=p.get("invoice_id") or req["subject_id"],
            payment_id=p["payment_id"],
            amount=int(p["amount"]),
            reason=req.get("reason") or "Approved refund",
            user_id=req.get("decided_by_id"),
            user_name=req.get("decided_by_name"),
        )
        return

    raise HTTPException(400, "Cannot apply this approval type")


@router.post("")
async def create_approval(payload: ApprovalCreate, user: dict = Depends(get_current_user)):
    subject, label, entity_id, norm_payload = await _validate_create(payload, user)
    existing = await db.approval_requests.find_one({
        "type": payload.type,
        "subject_id": payload.subject_id,
        "status": "pending",
    })
    if existing:
        raise HTTPException(400, "A pending approval already exists for this subject")

    doc = {
        "id": str(uuid.uuid4()),
        "type": payload.type,
        "status": "pending",
        "entity_id": entity_id,
        "subject_id": payload.subject_id,
        "subject_label": label,
        "reason": payload.reason.strip(),
        "payload": norm_payload,
        "requested_by_id": user["id"],
        "requested_by_name": user["name"],
        "requested_at": now_utc().isoformat(),
        "decided_by_id": None,
        "decided_by_name": None,
        "decided_at": None,
        "decision_note": None,
        "history": [_history_entry("submitted", user, payload.reason.strip())],
        "comments": [],
    }
    await db.approval_requests.insert_one(doc)
    await send_to_role(
        "super_admin",
        ntype="approval_requested",
        title=f"Approval: {payload.type.replace('_', ' ')}",
        message=f"{user['name']} submitted {label}",
        ref_id=doc["id"],
        ref_type="approval",
        entity_id=entity_id,
    )
    return _approval_out(doc)


@router.get("")
async def list_approvals(
    status: Optional[str] = None,
    type: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    if not _can_view_approvals(user):
        raise HTTPException(403, "Not allowed")
    q = _visibility_filter(user)
    if status:
        q["status"] = status
    if type:
        q["type"] = type
    rows = await db.approval_requests.find(q, {"_id": 0}).sort("requested_at", -1).to_list(500)
    return [_approval_out(r) for r in rows]


@router.get("/{req_id}")
async def get_approval(req_id: str, user: dict = Depends(get_current_user)):
    doc = await db.approval_requests.find_one({"id": req_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Approval request not found")
    if not _can_approve(user) and doc.get("requested_by_id") != user["id"]:
        raise HTTPException(403, "Not allowed")
    return _approval_out(doc)


@router.post("/{req_id}/approve")
async def approve(req_id: str, payload: DecisionIn, user: dict = Depends(get_current_user)):
    if not _can_approve(user):
        raise HTTPException(403, "Approval permission required")
    req = await db.approval_requests.find_one({"id": req_id})
    if not req:
        raise HTTPException(404, "Approval request not found")
    if req["status"] != "pending":
        raise HTTPException(400, f"Request already {req['status']}")

    req["decided_by_id"] = user["id"]
    req["decided_by_name"] = user["name"]
    await _apply_approval(req)

    entry = _history_entry("approved", user, payload.note)
    await db.approval_requests.update_one({"id": req_id}, {"$set": {
        "status": "approved",
        "decided_by_id": user["id"],
        "decided_by_name": user["name"],
        "decided_at": now_utc().isoformat(),
        "decision_note": payload.note,
    }, "$push": {"history": entry}})

    await send_notification(
        req["requested_by_id"],
        ntype="approval_completed",
        title="Request approved",
        message=f"{req['subject_label']} — approved by {user['name']}",
        ref_id=req_id,
        ref_type="approval",
        entity_id=req.get("entity_id"),
    )
    doc = await db.approval_requests.find_one({"id": req_id}, {"_id": 0})
    return _approval_out(doc)


@router.post("/{req_id}/reject")
async def reject(req_id: str, payload: DecisionIn, user: dict = Depends(get_current_user)):
    if not _can_approve(user):
        raise HTTPException(403, "Approval permission required")
    req = await db.approval_requests.find_one({"id": req_id})
    if not req:
        raise HTTPException(404, "Approval request not found")
    if req["status"] != "pending":
        raise HTTPException(400, f"Request already {req['status']}")

    entry = _history_entry("rejected", user, payload.note)
    await db.approval_requests.update_one({"id": req_id}, {"$set": {
        "status": "rejected",
        "decided_by_id": user["id"],
        "decided_by_name": user["name"],
        "decided_at": now_utc().isoformat(),
        "decision_note": payload.note,
    }, "$push": {"history": entry}})

    await send_notification(
        req["requested_by_id"],
        ntype="approval_completed",
        title="Request rejected",
        message=f"{req['subject_label']} — rejected by {user['name']}",
        ref_id=req_id,
        ref_type="approval",
        entity_id=req.get("entity_id"),
    )
    doc = await db.approval_requests.find_one({"id": req_id}, {"_id": 0})
    return _approval_out(doc)


@router.post("/{req_id}/comments")
async def add_comment(req_id: str, payload: CommentIn, user: dict = Depends(get_current_user)):
    req = await db.approval_requests.find_one({"id": req_id}, {"_id": 0})
    if not req:
        raise HTTPException(404, "Approval request not found")
    if not _can_approve(user) and req.get("requested_by_id") != user["id"]:
        raise HTTPException(403, "Not allowed")
    comment = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "user_name": user["name"],
        "user_role": user["role"],
        "text": payload.text.strip(),
        "created_at": now_utc().isoformat(),
    }
    await db.approval_requests.update_one({"id": req_id}, {"$push": {"comments": comment}})
    return comment
