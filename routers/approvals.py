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
from approval_types import (
    enrich_approval,
    types_for_category,
    role_label_from_person,
    role_label_from_user,
    entity_from_person,
    entity_from_user,
    LEGACY_DEACTIVATION_TYPES,
)

router = APIRouter(prefix="/approval-requests", tags=["approvals"])

APPROVAL_TYPES = (
    "student_deactivation",
    "player_deactivation",
    "user_deactivation",
    "fee_edit",
    "fee_concession",
    "fee_override_admission",
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
    return enrich_approval(doc)


async def _insert_deactivation_approval(
    *,
    subject_id: str,
    subject_label: str,
    entity_id: str,
    entity: str,
    target_role: str,
    reason: str,
    user: dict,
    payload: dict,
) -> dict:
    existing = await db.approval_requests.find_one({
        "type": {"$in": list(LEGACY_DEACTIVATION_TYPES)},
        "subject_id": subject_id,
        "status": "pending",
    })
    if existing:
        raise HTTPException(400, "A pending deactivation approval already exists for this subject")

    norm_payload = {
        **payload,
        "target_role": target_role,
    }
    doc = {
        "id": str(uuid.uuid4()),
        "type": "user_deactivation",
        "status": "pending",
        "entity_id": entity_id.lower() if entity_id else "pws",
        "organization": entity,
        "subject_id": subject_id,
        "subject_label": subject_label,
        "reason": reason.strip() or "Deactivation requested",
        "payload": norm_payload,
        "requested_by_id": user["id"],
        "requested_by_name": user["name"],
        "requested_at": now_utc().isoformat(),
        "decided_by_id": None,
        "decided_by_name": None,
        "decided_at": None,
        "decision_note": None,
        "history": [_history_entry("submitted", user, reason.strip() or "Deactivation requested")],
        "comments": [],
    }
    await db.approval_requests.insert_one(doc)
    await send_to_role(
        "super_admin",
        ntype="approval_requested",
        title="User deactivation request",
        message=f"{user['name']} requested deactivation of {subject_label} ({target_role})",
        ref_id=doc["id"],
        ref_type="approval",
        entity_id=doc["entity_id"],
    )
    return doc


class ApprovalCreate(BaseModel):
    type: Literal[
        "student_deactivation",
        "player_deactivation",
        "user_deactivation",
        "fee_edit",
        "fee_concession",
        "fee_override_admission",
        "refund",
    ]
    subject_id: str
    reason: str = Field(min_length=3)
    payload: dict = Field(default_factory=dict)


class DecisionIn(BaseModel):
    note: Optional[str] = None
    modified_custom_fees: Optional[dict] = None


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

    if payload.type == "user_deactivation":
        user_id = p.get("user_id")
        person_id = p.get("person_id") or payload.subject_id
        if user_id:
            target_user = await db.users.find_one({"id": user_id}, {"_id": 0})
            if not target_user:
                raise HTTPException(404, "User not found")
            if target_user.get("status") == "deactivated":
                raise HTTPException(400, "User already deactivated")
            if target_user.get("role") == "super_admin":
                raise HTTPException(400, "Cannot deactivate Super Admin")
            entity = entity_from_user(target_user)
            entity_id = entity.lower()
            role = role_label_from_user(target_user)
            label = target_user["name"]
            p.update({"user_id": user_id, "target_kind": "user", "target_role": role})
            return target_user, label, entity_id, p

        person = await db.people.find_one({"id": person_id}, {"_id": 0})
        if not person:
            raise HTTPException(404, "Person not found")
        if person.get("status") == "deactivated":
            raise HTTPException(400, "Person already deactivated")
        entity = entity_from_person(person)
        entity_id = "pws" if entity == "PWS" else "alpha" if entity == "ALPHA" else "pws"
        role = role_label_from_person(person)
        label = person["name"]
        p.update({
            "person_id": person_id,
            "target_kind": person.get("kind"),
            "target_role": role,
            "centre": person.get("centre"),
            "sport": person.get("sport"),
            "category": person.get("player_type") or person.get("pws_student_type"),
        })
        return person, label, entity_id, p

    if payload.type == "fee_edit":
        if not (get_perm(user, "edit_fees") or is_super_admin(user)):
            raise HTTPException(403, "Permission required to request fee edit")
        fee = await db.fees.find_one({"id": payload.subject_id}, {"_id": 0})
        if not fee:
            raise HTTPException(404, "Fee not found")
        new_amount = p.get("new_amount_due")
        if new_amount is None:
            raise HTTPException(400, "new_amount_due required in payload")
        new_amount = int(new_amount)
        if new_amount < 0:
            raise HTTPException(400, "new_amount_due must be non-negative")
        prev = int(fee.get("amount_due") or 0)
        entity_id = fee.get("entity_id") or ("pws" if fee.get("organization") == "PWS" else "alpha")
        person_name = fee.get("player_name") or fee.get("person_name") or "Fee"
        label = f"{person_name} · fee edit ₹{prev} → ₹{new_amount}"
        p.update({
            "fee_id": fee["id"],
            "previous_amount_due": prev,
            "new_amount_due": new_amount,
            "person_name": person_name,
            "fee_type": fee.get("fee_type"),
            "period_month": fee.get("period_month"),
        })
        return fee, label, entity_id, p

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
        person_name = fee.get("player_name") or fee.get("person_name") or "Fee"
        label = f"{person_name} · ₹{discount_amount} off"
        p.setdefault("fee_id", fee["id"])
        p.setdefault("discount_amount", discount_amount)
        p.setdefault("person_name", person_name)
        p.setdefault("original_amount_due", int(fee.get("amount_due") or 0))
        if p.get("discount_percent"):
            p["discount_percent"] = float(p["discount_percent"])
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

    if t in LEGACY_DEACTIVATION_TYPES:
        p = req.get("payload") or {}
        if p.get("user_id"):
            await db.users.update_one({"id": p["user_id"]}, {"$set": {"status": "deactivated"}})
            return
        pid = p.get("person_id") or req["subject_id"]
        await db.people.update_one({"id": pid}, {"$set": {"status": "deactivated"}})
        person = await db.people.find_one({"id": pid}, {"_id": 0})
        if person and person.get("kind") == "staff":
            from routers.people import ensure_staff_user_account
            await ensure_staff_user_account(person)
        return

    if t == "fee_edit":
        fee_id = p.get("fee_id") or req["subject_id"]
        fee = await db.fees.find_one({"id": fee_id})
        if not fee:
            raise HTTPException(404, "Fee not found")
        new_amt = int(p.get("new_amount_due") if p.get("new_amount_due") is not None else fee.get("amount_due") or 0)
        await db.fees.update_one({"id": fee_id}, {"$set": {
            "amount_due": new_amt,
            "fee_edit_reason": req.get("reason"),
            "fee_edit_by_id": req.get("decided_by_id"),
            "fee_edit_by_name": req.get("decided_by_name"),
            "fee_edit_at": now_utc().isoformat(),
        }})
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

    if t == "fee_override_admission":
        from fee_override_approval import apply_approved_fee_override
        modified = req.get("modified_custom_fees")
        try:
            await apply_approved_fee_override(req, modified_custom_fees=modified)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
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
    category: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    if not _can_view_approvals(user):
        raise HTTPException(403, "Not allowed")
    q = _visibility_filter(user)
    if status:
        q["status"] = status
    if category:
        cat_types = types_for_category(category)
        if cat_types:
            q["type"] = {"$in": list(cat_types)}
    elif type:
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


def _completion_notification(req: dict, *, approved: bool, decider_name: str) -> tuple[str, str]:
    """Return (title, message) for requester notification after approve/reject."""
    raw_type = req.get("type") or ""
    subject = req.get("subject_label") or "Your request"
    person_name = subject.split(" · ")[0].strip() if " · " in subject else subject.strip()

    if raw_type == "fee_override_admission":
        payload = req.get("payload") or {}
        entity_type = (payload.get("entity_type") or "RECORD").replace("_", " ").title()
        if approved:
            return (
                "Custom fee override approved",
                f"Your custom fee override for {person_name} ({entity_type}) has been approved by {decider_name}.",
            )
        return (
            "Custom fee override rejected",
            f"Your custom fee override for {person_name} ({entity_type}) was rejected by {decider_name}.",
        )

    if approved:
        return ("Request approved", f"{subject} — approved by {decider_name}")
    return ("Request rejected", f"{subject} — rejected by {decider_name}")


@router.post("/{req_id}/approve")
async def approve(req_id: str, payload: DecisionIn, user: dict = Depends(get_current_user)):
    if not _can_approve(user):
        raise HTTPException(403, "Approval permission required")
    req = await db.approval_requests.find_one({"id": req_id})
    if not req:
        raise HTTPException(404, "Approval request not found")
    if req["status"] != "pending":
        raise HTTPException(400, f"Request already {req['status']}")

    decided_at = now_utc().isoformat()
    entry = _history_entry("approved", user, payload.note)
    await db.approval_requests.update_one({"id": req_id}, {"$set": {
        "status": "approved",
        "decided_by_id": user["id"],
        "decided_by_name": user["name"],
        "decided_at": decided_at,
        "decision_note": payload.note,
    }, "$push": {"history": entry}})

    req["status"] = "approved"
    req["decided_by_id"] = user["id"]
    req["decided_by_name"] = user["name"]
    req["decided_at"] = decided_at
    req["decision_note"] = payload.note
    if payload.modified_custom_fees is not None:
        req["modified_custom_fees"] = payload.modified_custom_fees

    try:
        await _apply_approval(req)
    except HTTPException:
        await db.approval_requests.update_one({"id": req_id}, {"$set": {
            "status": "pending",
            "decided_by_id": None,
            "decided_by_name": None,
            "decided_at": None,
            "decision_note": None,
        }})
        raise
    except Exception:
        import logging
        logging.getLogger("approvals").exception("Side-effect failed for approved request %s", req_id)

    notify_title, notify_message = _completion_notification(req, approved=True, decider_name=user["name"])
    await send_notification(
        req["requested_by_id"],
        ntype="approval_completed",
        title=notify_title,
        message=notify_message,
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

    if req.get("type") == "fee_override_admission":
        from fee_override_approval import apply_rejected_fee_override
        await apply_rejected_fee_override(req)

    entry = _history_entry("rejected", user, payload.note)
    await db.approval_requests.update_one({"id": req_id}, {"$set": {
        "status": "rejected",
        "decided_by_id": user["id"],
        "decided_by_name": user["name"],
        "decided_at": now_utc().isoformat(),
        "decision_note": payload.note,
    }, "$push": {"history": entry}})

    notify_title, notify_message = _completion_notification(req, approved=False, decider_name=user["name"])
    await send_notification(
        req["requested_by_id"],
        ntype="approval_completed",
        title=notify_title,
        message=notify_message,
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
