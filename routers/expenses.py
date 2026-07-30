"""Expense management — heads, entries, approvals, audit trail."""
from __future__ import annotations

import base64
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from core import db, get_current_user, get_perm, is_super_admin, now_utc, assert_entity_access, user_entity_scope
from rbac.authorization import has_permission
from rbac.enums import BusinessEntity, Permission
from notifications_service import send_notification, send_to_role

router = APIRouter(prefix="/expenses", tags=["expenses"])

ENTITY_IDS = ("pws", "alpha")
EXPENSE_STATUSES = ("pending", "approved", "rejected", "cancelled")
PAYMENT_MODES = ("Cash", "UPI", "Bank Transfer", "Cheque", "Credit Card")
MAIN_CATEGORIES = (
    "Operational", "Capital Expenditure", "Canteen", "Sports Equipment",
    "Academic Supplies", "Utilities", "Maintenance",
)
ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024
ALLOWED_MIME = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/jpg"}


def _audit_entry(action: str, user: dict, note: Optional[str] = None, changes: Optional[dict] = None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "action": action,
        "user_id": user["id"],
        "user_name": user["name"],
        "user_role": user.get("role"),
        "note": note,
        "changes": changes or {},
        "at": now_utc().isoformat(),
    }


async def _write_audit_log(entry_id: str, log: dict) -> None:
    await db.expense_audit_logs.insert_one({"entry_id": entry_id, **log})


def _can_manage_structure(user: dict) -> bool:
    return is_super_admin(user) or has_permission(user, Permission.MANAGE_EXPENSE_STRUCTURE)


def _can_capture(user: dict, entity_id: str) -> bool:
    if is_super_admin(user):
        return True
    perm = Permission.CAPTURE_PWS_EXPENSES if entity_id == "pws" else Permission.CAPTURE_ALPHA_EXPENSES
    return has_permission(user, perm, entity=BusinessEntity.PWS if entity_id == "pws" else BusinessEntity.ALPHA)


def _can_view_entity_expenses(user: dict, entity_id: str) -> bool:
    if is_super_admin(user):
        return True
    role = (user.get("role") or "").lower()
    if entity_id == "pws" and role in ("principal", "vice_principal", "pws_admin", "pws_accounts"):
        return True
    if entity_id == "alpha" and role in ("admin", "alpha_admin", "alpha_accounts"):
        return True
    return _can_capture(user, entity_id) or _can_approve_expenses(user)


def _can_approve_expenses(user: dict) -> bool:
    role = (user.get("role") or "").lower()
    return (
        is_super_admin(user)
        or role in ("principal", "vice_principal")
        or get_perm(user, "approve_requests")
        or has_permission(user, Permission.APPROVE_REQUESTS)
    )


def _can_approve_entity_expenses(user: dict, entity_id: str) -> bool:
    if not _can_approve_expenses(user):
        return False
    if is_super_admin(user):
        return True
    try:
        assert_entity_access(user, entity_id)
        return True
    except HTTPException:
        return False


def _can_view_both_entities(user: dict) -> bool:
    return _can_view_entity_expenses(user, "pws") and _can_view_entity_expenses(user, "alpha")


def _entry_out(doc: dict, head: Optional[dict] = None) -> dict:
    out = {k: v for k, v in doc.items() if k != "_id"}
    if head:
        out["expense_head_name"] = head.get("sub_category") or head.get("name")
        out["main_category"] = head.get("main_category")
        out["category_code"] = head.get("category_code")
    return out


async def _load_head(head_id: str) -> dict:
    head = await db.expense_heads.find_one({"id": head_id}, {"_id": 0})
    if not head:
        raise HTTPException(404, "Expense head not found")
    return head


async def _monthly_spent(head_id: str, expense_date: str) -> int:
    month = expense_date[:7]
    pipeline = [
        {"$match": {
            "expense_head_id": head_id,
            "status": {"$in": ["pending", "approved"]},
            "expense_date": {"$regex": f"^{month}"},
        }},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]
    rows = await db.expense_entries.aggregate(pipeline).to_list(1)
    return int(rows[0]["total"]) if rows else 0


async def _budget_alert(head: dict, expense_date: str, extra_amount: int = 0, exclude_id: Optional[str] = None) -> Optional[dict]:
    limit = head.get("monthly_budget_limit")
    if not limit or limit <= 0:
        return None
    month = expense_date[:7]
    q: Dict[str, Any] = {
        "expense_head_id": head["id"],
        "status": {"$in": ["pending", "approved"]},
        "expense_date": {"$regex": f"^{month}"},
    }
    if exclude_id:
        q["id"] = {"$ne": exclude_id}
    pipeline = [{"$match": q}, {"$group": {"_id": None, "total": {"$sum": "$amount"}}}]
    rows = await db.expense_entries.aggregate(pipeline).to_list(1)
    spent = int(rows[0]["total"]) if rows else 0
    projected = spent + extra_amount
    if projected > limit:
        return {
            "over_budget": True,
            "monthly_budget_limit": limit,
            "monthly_spent": spent,
            "projected_total": projected,
            "overage": projected - limit,
        }
    return {"over_budget": False, "monthly_budget_limit": limit, "monthly_spent": spent, "projected_total": projected}


async def _next_category_code(entity_id: str, main_category: str) -> str:
    abbr = re.sub(r"[^A-Za-z]", "", main_category)[:3].upper() or "GEN"
    prefix = f"{entity_id.upper()}-{abbr}-"
    existing = await db.expense_heads.find({"category_code": {"$regex": f"^{prefix}"}}, {"category_code": 1}).to_list(500)
    nums = []
    for h in existing:
        m = re.search(r"-(\d+)$", h.get("category_code") or "")
        if m:
            nums.append(int(m.group(1)))
    n = max(nums, default=0) + 1
    return f"{prefix}{n:03d}"


# -------------------- Expense Heads --------------------
class ExpenseHeadIn(BaseModel):
    entity_id: Literal["pws", "alpha"]
    category_code: Optional[str] = None
    main_category: str = Field(min_length=2)
    sub_category: str = Field(min_length=2)
    monthly_budget_limit: Optional[int] = Field(default=None, ge=0)
    status: Literal["active", "inactive"] = "active"


class ExpenseHeadPatch(BaseModel):
    entity_id: Optional[Literal["pws", "alpha"]] = None
    category_code: Optional[str] = None
    main_category: Optional[str] = None
    sub_category: Optional[str] = None
    monthly_budget_limit: Optional[int] = Field(default=None, ge=0)
    status: Optional[Literal["active", "inactive"]] = None


def _rewrite_category_code_prefix(code: str, from_entity: str, to_entity: str) -> str:
    old_prefix = f"{from_entity.upper()}-"
    new_prefix = f"{to_entity.upper()}-"
    if (code or "").upper().startswith(old_prefix):
        return new_prefix + code[len(old_prefix):]
    return code


def _entity_from_code(code: str) -> Optional[str]:
    upper = (code or "").upper()
    if upper.startswith("ALPHA-"):
        return "alpha"
    if upper.startswith("PWS-"):
        return "pws"
    return None


async def _ensure_unique_category_code(entity_id: str, code: str, exclude_id: Optional[str] = None) -> None:
    q: Dict[str, Any] = {"entity_id": entity_id, "category_code": code}
    if exclude_id:
        q["id"] = {"$ne": exclude_id}
    if await db.expense_heads.find_one(q, {"_id": 1}):
        raise HTTPException(400, "Category code already exists for this entity")


@router.get("/heads")
async def list_expense_heads(
    user: dict = Depends(get_current_user),
    entity_id: Optional[str] = None,
    active_only: bool = False,
):
    if entity_id and entity_id not in ENTITY_IDS:
        raise HTTPException(400, "entity_id must be pws or alpha")
    if not _can_manage_structure(user) and not (entity_id and _can_view_entity_expenses(user, entity_id)):
        raise HTTPException(403, "Access denied")
    q: Dict[str, Any] = {}
    if entity_id:
        q["entity_id"] = entity_id
    if active_only:
        q["status"] = "active"
    rows = await db.expense_heads.find(q, {"_id": 0}).sort([("entity_id", 1), ("main_category", 1), ("sub_category", 1)]).to_list(500)
    return rows


@router.post("/heads")
async def create_expense_head(payload: ExpenseHeadIn, user: dict = Depends(get_current_user)):
    _require_structure(user)
    code = (payload.category_code or "").strip() or await _next_category_code(payload.entity_id, payload.main_category)
    dup = await db.expense_heads.find_one({"entity_id": payload.entity_id, "category_code": code})
    if dup:
        raise HTTPException(400, "Category code already exists for this entity")
    doc = {
        "id": str(uuid.uuid4()),
        "entity_id": payload.entity_id,
        "category_code": code,
        "main_category": payload.main_category.strip(),
        "sub_category": payload.sub_category.strip(),
        "monthly_budget_limit": payload.monthly_budget_limit,
        "status": payload.status,
        "created_at": now_utc().isoformat(),
        "created_by_id": user["id"],
        "created_by_name": user["name"],
        "updated_at": now_utc().isoformat(),
    }
    await db.expense_heads.insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


def _require_structure(user: dict) -> None:
    if not _can_manage_structure(user):
        raise HTTPException(403, "Super Admin only")


@router.patch("/heads/{head_id}")
async def update_expense_head(head_id: str, payload: ExpenseHeadPatch, user: dict = Depends(get_current_user)):
    _require_structure(user)
    head = await _load_head(head_id)
    patch: Dict[str, Any] = {"updated_at": now_utc().isoformat()}
    head_entity = head.get("entity_id") or "pws"
    target_entity = payload.entity_id or head_entity
    entity_changed = payload.entity_id is not None and payload.entity_id != head_entity

    if payload.category_code is not None and payload.entity_id is not None:
        implied = _entity_from_code(payload.category_code.strip())
        if implied and implied != payload.entity_id:
            raise HTTPException(400, "Category code prefix does not match selected entity")

    for field in ("main_category", "sub_category", "status"):
        val = getattr(payload, field, None)
        if val is not None:
            patch[field] = val.strip() if isinstance(val, str) else val

    if payload.monthly_budget_limit is not None:
        patch["monthly_budget_limit"] = payload.monthly_budget_limit

    if entity_changed:
        patch["entity_id"] = payload.entity_id
        target_entity = payload.entity_id  # type: ignore[assignment]

    if payload.category_code is not None:
        code = payload.category_code.strip()
        if not code:
            raise HTTPException(400, "Category code cannot be empty")
        await _ensure_unique_category_code(target_entity, code, exclude_id=head_id)
        patch["category_code"] = code
    elif entity_changed:
        main_cat = patch.get("main_category") or head.get("main_category") or "Operational"
        rewritten = _rewrite_category_code_prefix(head.get("category_code") or "", head_entity, target_entity)
        try:
            await _ensure_unique_category_code(target_entity, rewritten, exclude_id=head_id)
            patch["category_code"] = rewritten
        except HTTPException:
            patch["category_code"] = await _next_category_code(target_entity, main_cat)

    if not patch.keys() - {"updated_at"}:
        return head

    await db.expense_heads.update_one({"id": head_id}, {"$set": patch})
    return await _load_head(head_id)


@router.post("/heads/{head_id}/toggle-active")
async def toggle_expense_head(head_id: str, user: dict = Depends(get_current_user)):
    _require_structure(user)
    head = await _load_head(head_id)
    new_status = "inactive" if head.get("status") == "active" else "active"
    await db.expense_heads.update_one({"id": head_id}, {"$set": {"status": new_status, "updated_at": now_utc().isoformat()}})
    head["status"] = new_status
    return head


# -------------------- Expense Entries --------------------
URGENCY_OPTIONS = ("Today", "Tomorrow", "This Week")


def _validate_payment_reference(payment_mode: str, reference_number: Optional[str]) -> None:
    if payment_mode != "Cash" and not (reference_number or "").strip():
        raise HTTPException(400, "Reference number is required for non-cash payment methods")


class ExpenseLineItemIn(BaseModel):
    item_name: str = Field(min_length=1)
    rate: float = Field(ge=0)
    quantity: float = Field(gt=0)
    amount: int = Field(gt=0)


def _normalize_line_items(
    items: Optional[List[ExpenseLineItemIn]],
    *,
    rate: Optional[float] = None,
    quantity: Optional[float] = None,
    amount: Optional[int] = None,
    sub_category: Optional[str] = None,
) -> tuple[List[dict], int, Optional[str], Optional[float], Optional[float]]:
    if items:
        normalized: List[dict] = []
        total = 0
        for it in items:
            name = it.item_name.strip()
            if not name:
                raise HTTPException(400, "Each line item requires a name")
            calc = round(it.rate * it.quantity)
            if abs(calc - it.amount) > 1:
                raise HTTPException(400, f"Line item amount mismatch for '{name}'")
            normalized.append({
                "item_name": name,
                "rate": float(it.rate),
                "quantity": float(it.quantity),
                "amount": int(it.amount),
            })
            total += int(it.amount)
        if total <= 0:
            raise HTTPException(400, "Total amount must be greater than zero")
        sub = normalized[0]["item_name"] if len(normalized) == 1 else f"{len(normalized)} items"
        legacy_rate = normalized[0]["rate"] if len(normalized) == 1 else None
        legacy_qty = normalized[0]["quantity"] if len(normalized) == 1 else None
        return normalized, total, sub, legacy_rate, legacy_qty
    if amount and amount > 0 and rate is not None and quantity is not None:
        name = (sub_category or "Item").strip() or "Item"
        return [{
            "item_name": name,
            "rate": float(rate),
            "quantity": float(quantity),
            "amount": int(amount),
        }], int(amount), name, float(rate), float(quantity)
    if amount and amount > 0:
        name = (sub_category or "Expense").strip() or "Expense"
        return [{"item_name": name, "rate": 0.0, "quantity": 1.0, "amount": int(amount)}], int(amount), name, None, None
    raise HTTPException(400, "At least one line item is required")


class ExpenseEntryIn(BaseModel):
    entity_id: Literal["pws", "alpha"]
    expense_head_id: str
    expense_date: str
    amount: Optional[int] = Field(default=None, gt=0)
    payment_mode: Literal["Cash", "UPI", "Bank Transfer", "Cheque", "Credit Card"]
    vendor_name: Optional[str] = None
    reference_number: Optional[str] = None
    description: Optional[str] = None
    venue: Optional[str] = None
    sub_category: Optional[str] = None
    items: Optional[List[ExpenseLineItemIn]] = None
    rate: Optional[float] = Field(default=None, ge=0)
    quantity: Optional[float] = Field(default=None, ge=0)
    urgency: Optional[Literal["Today", "Tomorrow", "This Week"]] = None


class ExpenseEntryPatch(BaseModel):
    expense_head_id: Optional[str] = None
    expense_date: Optional[str] = None
    amount: Optional[int] = Field(default=None, gt=0)
    payment_mode: Optional[Literal["Cash", "UPI", "Bank Transfer", "Cheque", "Credit Card"]] = None
    vendor_name: Optional[str] = None
    reference_number: Optional[str] = None
    description: Optional[str] = None
    venue: Optional[str] = None
    sub_category: Optional[str] = None
    items: Optional[List[ExpenseLineItemIn]] = None
    rate: Optional[float] = Field(default=None, ge=0)
    quantity: Optional[float] = Field(default=None, ge=0)
    urgency: Optional[Literal["Today", "Tomorrow", "This Week"]] = None


class RejectIn(BaseModel):
    reason: str = Field(min_length=3)


class BulkApproveIn(BaseModel):
    entry_ids: List[str] = Field(min_length=1)


async def _load_entry(entry_id: str) -> dict:
    doc = await db.expense_entries.find_one({"id": entry_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Expense entry not found")
    return doc


@router.get("/entries")
async def list_expense_entries(
    user: dict = Depends(get_current_user),
    entity_id: str = Query(...),
    status: Optional[str] = None,
    tab: Optional[str] = None,
):
    if entity_id not in ENTITY_IDS:
        raise HTTPException(400, "entity_id must be pws or alpha")
    if not _can_view_entity_expenses(user, entity_id):
        raise HTTPException(403, "Access denied")
    q: Dict[str, Any] = {"entity_id": entity_id}
    if tab == "pending":
        q["status"] = "pending"
    elif tab == "approved":
        q["status"] = "approved"
    elif tab == "rejected":
        q["status"] = "rejected"
    elif status and status in EXPENSE_STATUSES:
        q["status"] = status
    rows = await db.expense_entries.find(q, {"_id": 0}).sort("expense_date", -1).to_list(2000)
    head_ids = list({r["expense_head_id"] for r in rows if r.get("expense_head_id")})
    heads = await db.expense_heads.find({"id": {"$in": head_ids}}, {"_id": 0}).to_list(len(head_ids) or 1)
    hmap = {h["id"]: h for h in heads}
    return [_entry_out(r, hmap.get(r.get("expense_head_id"))) for r in rows]


@router.post("/entries")
async def create_expense_entry(payload: ExpenseEntryIn, user: dict = Depends(get_current_user)):
    if not _can_capture(user, payload.entity_id):
        raise HTTPException(403, "Expense capture permission required")
    assert_entity_access(user, payload.entity_id)
    head = await _load_head(payload.expense_head_id)
    if head["entity_id"] != payload.entity_id:
        raise HTTPException(400, "Expense head does not belong to this entity")
    if head.get("status") != "active":
        raise HTTPException(400, "Expense head is inactive")
    _validate_payment_reference(payload.payment_mode, payload.reference_number)
    line_items, total_amount, sub_category, legacy_rate, legacy_qty = _normalize_line_items(
        payload.items,
        rate=payload.rate,
        quantity=payload.quantity,
        amount=payload.amount,
        sub_category=payload.sub_category,
    )
    if payload.amount is not None and payload.amount != total_amount:
        raise HTTPException(400, "amount must equal the sum of line items")
    vendor = (payload.vendor_name or line_items[0]["item_name"] or head.get("sub_category") or "General").strip()
    budget = await _budget_alert(head, payload.expense_date, total_amount)
    req_id = f"EXP-{payload.entity_id.upper()}-{uuid.uuid4().hex[:8].upper()}"
    log = _audit_entry("created", user, "Expense submitted for approval")
    doc = {
        "id": str(uuid.uuid4()),
        "request_id": req_id,
        "entity_id": payload.entity_id,
        "expense_head_id": payload.expense_head_id,
        "sub_category": sub_category,
        "items": line_items,
        "expense_date": payload.expense_date[:10],
        "amount": total_amount,
        "rate": legacy_rate,
        "quantity": legacy_qty,
        "urgency": payload.urgency,
        "payment_mode": payload.payment_mode,
        "vendor_name": vendor,
        "reference_number": (payload.reference_number or "").strip() or None,
        "description": (payload.description or "").strip() or None,
        "venue": (payload.venue or "").strip() or None,
        "status": "pending",
        "attachment_id": None,
        "rejection_reason": None,
        "budget_alert": budget,
        "created_at": now_utc().isoformat(),
        "created_by_id": user["id"],
        "created_by_name": user["name"],
        "created_by_role": user.get("role"),
        "approved_at": None,
        "approved_by_id": None,
        "approved_by_name": None,
        "rejected_at": None,
        "rejected_by_id": None,
        "rejected_by_name": None,
        "updated_at": now_utc().isoformat(),
        "audit_trail": [log],
    }
    await db.expense_entries.insert_one(doc)
    await _write_audit_log(doc["id"], log)
    await send_to_role("super_admin", ntype="expense_pending", title="Expense pending approval",
                       message=f"{user['name']} submitted {req_id} for ₹{total_amount:,}", ref_id=doc["id"], ref_type="expense")
    role_targets = ["principal", "vice_principal"] if payload.entity_id == "pws" else ["admin"]
    for role in role_targets:
        await send_to_role(role, ntype="expense_pending", title="Expense pending approval",
                           message=f"{user['name']} submitted {req_id}", ref_id=doc["id"], ref_type="expense", entity_id=payload.entity_id)
    return _entry_out(doc, head)


@router.patch("/entries/{entry_id}")
async def update_expense_entry(entry_id: str, payload: ExpenseEntryPatch, user: dict = Depends(get_current_user)):
    entry = await _load_entry(entry_id)
    if entry["status"] not in ("pending", "rejected"):
        raise HTTPException(400, "Only pending or rejected entries can be edited")
    if not _can_capture(user, entry["entity_id"]) and entry["created_by_id"] != user["id"]:
        raise HTTPException(403, "Access denied")
    head_id = payload.expense_head_id or entry["expense_head_id"]
    head = await _load_head(head_id)
    expense_date = (payload.expense_date or entry["expense_date"])[:10]
    payment_mode = payload.payment_mode or entry["payment_mode"]
    reference_number = payload.reference_number if payload.reference_number is not None else entry.get("reference_number")
    _validate_payment_reference(payment_mode, reference_number)

    if payload.items is not None:
        line_items, amount, sub_category, legacy_rate, legacy_qty = _normalize_line_items(payload.items)
    else:
        amount = payload.amount if payload.amount is not None else entry["amount"]
        line_items = None
        sub_category = None
        legacy_rate = None
        legacy_qty = None

    budget = await _budget_alert(head, expense_date, amount, exclude_id=entry_id)
    patch: Dict[str, Any] = {
        "updated_at": now_utc().isoformat(),
        "budget_alert": budget,
    }
    if line_items is not None:
        patch["items"] = line_items
        patch["amount"] = amount
        patch["sub_category"] = sub_category
        patch["rate"] = legacy_rate
        patch["quantity"] = legacy_qty
    elif payload.amount is not None:
        patch["amount"] = amount
    if entry["status"] == "rejected":
        patch["status"] = "pending"
        patch["rejection_reason"] = None
        patch["rejected_at"] = None
        patch["rejected_by_id"] = None
        patch["rejected_by_name"] = None
    for field in (
        "expense_head_id", "expense_date", "payment_mode", "vendor_name",
        "reference_number", "description", "venue", "urgency",
    ):
        val = getattr(payload, field, None)
        if val is not None:
            patch[field] = val.strip() if isinstance(val, str) else val
    if payload.expense_head_id and payload.items is None and payload.sub_category is None and "sub_category" not in patch:
        patch["sub_category"] = entry.get("sub_category") or head.get("sub_category")
    log = _audit_entry("updated", user, "Entry modified" + (" and resubmitted" if entry["status"] == "rejected" else ""))
    await db.expense_entries.update_one({"id": entry_id}, {"$set": patch, "$push": {"audit_trail": log}})
    await _write_audit_log(entry_id, log)
    updated = await _load_entry(entry_id)
    return _entry_out(updated, head)


@router.delete("/entries/{entry_id}")
async def delete_expense_entry(entry_id: str, user: dict = Depends(get_current_user)):
    entry = await _load_entry(entry_id)
    if entry["status"] != "pending":
        raise HTTPException(400, "Only pending entries can be deleted")
    if not _can_capture(user, entry["entity_id"]) and entry["created_by_id"] != user["id"]:
        raise HTTPException(403, "Access denied")
    log = _audit_entry("deleted", user, "Entry deleted before approval")
    await _write_audit_log(entry_id, log)
    await db.expense_entries.delete_one({"id": entry_id})
    return {"ok": True}


@router.post("/entries/{entry_id}/resubmit")
async def resubmit_expense_entry(entry_id: str, user: dict = Depends(get_current_user)):
    entry = await _load_entry(entry_id)
    if entry["status"] != "rejected":
        raise HTTPException(400, "Only rejected entries can be resubmitted")
    if entry["created_by_id"] != user["id"] and not _can_capture(user, entry["entity_id"]):
        raise HTTPException(403, "Access denied")
    head = await _load_head(entry["expense_head_id"])
    budget = await _budget_alert(head, entry["expense_date"], entry["amount"], exclude_id=entry_id)
    log = _audit_entry("resubmitted", user, "Rejected entry resubmitted for approval")
    await db.expense_entries.update_one({"id": entry_id}, {"$set": {
        "status": "pending",
        "rejection_reason": None,
        "rejected_at": None,
        "rejected_by_id": None,
        "rejected_by_name": None,
        "budget_alert": budget,
        "updated_at": now_utc().isoformat(),
    }, "$push": {"audit_trail": log}})
    await _write_audit_log(entry_id, log)
    return _entry_out(await _load_entry(entry_id), head)


@router.post("/entries/{entry_id}/recall")
async def recall_expense_entry(entry_id: str, user: dict = Depends(get_current_user)):
    if not is_super_admin(user):
        raise HTTPException(403, "Super Admin only")
    entry = await _load_entry(entry_id)
    if entry["status"] != "approved":
        raise HTTPException(400, "Only approved entries can be recalled")
    log = _audit_entry("recalled", user, "Approved entry recalled by Super Admin")
    await db.expense_entries.update_one({"id": entry_id}, {"$set": {
        "status": "cancelled",
        "updated_at": now_utc().isoformat(),
    }, "$push": {"audit_trail": log}})
    await _write_audit_log(entry_id, log)
    return await _load_entry(entry_id)


# -------------------- Attachments --------------------
@router.post("/entries/{entry_id}/attachment")
async def upload_expense_attachment(
    entry_id: str,
    user: dict = Depends(get_current_user),
    file: UploadFile = File(...),
):
    entry = await _load_entry(entry_id)
    if entry["status"] not in ("pending", "rejected"):
        raise HTTPException(400, "Cannot attach files to finalized entries")
    if entry["created_by_id"] != user["id"] and not _can_capture(user, entry["entity_id"]):
        raise HTTPException(403, "Access denied")
    content = await file.read()
    if len(content) > ATTACHMENT_MAX_BYTES:
        raise HTTPException(400, "File exceeds 5 MB limit")
    mime = file.content_type or "application/octet-stream"
    if mime not in ALLOWED_MIME:
        raise HTTPException(400, "Only PDF or image files are allowed")
    att_id = str(uuid.uuid4())
    att = {
        "id": att_id,
        "entry_id": entry_id,
        "filename": file.filename or "receipt",
        "mime_type": mime,
        "size_bytes": len(content),
        "data_base64": base64.b64encode(content).decode("ascii"),
        "uploaded_at": now_utc().isoformat(),
        "uploaded_by_id": user["id"],
        "uploaded_by_name": user["name"],
    }
    await db.expense_attachments.insert_one(att)
    await db.expense_entries.update_one({"id": entry_id}, {"$set": {"attachment_id": att_id, "updated_at": now_utc().isoformat()}})
    return {"id": att_id, "filename": att["filename"], "mime_type": mime, "size_bytes": len(content)}


@router.get("/attachments/{attachment_id}")
async def get_expense_attachment(attachment_id: str, user: dict = Depends(get_current_user)):
    att = await db.expense_attachments.find_one({"id": attachment_id}, {"_id": 0})
    if not att:
        raise HTTPException(404, "Attachment not found")
    entry = await _load_entry(att["entry_id"])
    if not _can_view_entity_expenses(user, entry["entity_id"]) and not _can_approve_expenses(user):
        raise HTTPException(403, "Access denied")
    return {
        "id": att["id"],
        "filename": att["filename"],
        "mime_type": att["mime_type"],
        "size_bytes": att["size_bytes"],
        "data_url": f"data:{att['mime_type']};base64,{att['data_base64']}",
    }


# -------------------- Approvals --------------------
@router.get("/approvals")
async def expense_approval_queue(
    user: dict = Depends(get_current_user),
    entity_id: Optional[str] = None,
    status: str = "pending",
):
    if not _can_approve_expenses(user):
        raise HTTPException(403, "Approver access required")
    q: Dict[str, Any] = {"status": status}
    if entity_id:
        if entity_id not in ENTITY_IDS:
            raise HTTPException(400, "Invalid entity_id")
        assert_entity_access(user, entity_id)
        q["entity_id"] = entity_id
    else:
        scope = user_entity_scope(user)
        if scope != "both":
            q["entity_id"] = scope
    rows = await db.expense_entries.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    head_ids = list({r["expense_head_id"] for r in rows})
    heads = await db.expense_heads.find({"id": {"$in": head_ids}}, {"_id": 0}).to_list(len(head_ids) or 1)
    hmap = {h["id"]: h for h in heads}
    creator_ids = list({r["created_by_id"] for r in rows if r.get("created_by_id")})
    creators = await db.users.find({"id": {"$in": creator_ids}}, {"_id": 0, "id": 1, "name": 1, "role": 1, "designation": 1}).to_list(len(creator_ids) or 1)
    cmap = {u["id"]: u for u in creators}
    out = []
    for r in rows:
        head = hmap.get(r.get("expense_head_id"))
        creator = cmap.get(r.get("created_by_id"), {})
        row = _entry_out(r, head)
        row["entered_by_name"] = r.get("created_by_name")
        row["entered_by_role"] = r.get("created_by_role") or creator.get("role")
        out.append(row)
    return out


@router.post("/entries/{entry_id}/approve")
async def approve_expense_entry(entry_id: str, user: dict = Depends(get_current_user)):
    if not _can_approve_expenses(user):
        raise HTTPException(403, "Approver access required")
    entry = await _load_entry(entry_id)
    if not _can_approve_entity_expenses(user, entry["entity_id"]):
        raise HTTPException(403, "Cannot approve expenses for this entity")
    if entry["status"] != "pending":
        raise HTTPException(400, "Entry is not pending")
    log = _audit_entry("approved", user, "Expense approved")
    now = now_utc().isoformat()
    await db.expense_entries.update_one({"id": entry_id}, {"$set": {
        "status": "approved",
        "approved_at": now,
        "approved_by_id": user["id"],
        "approved_by_name": user["name"],
        "updated_at": now,
    }, "$push": {"audit_trail": log}})
    await _write_audit_log(entry_id, log)
    await send_notification(entry["created_by_id"], ntype="expense_approved", title="Expense approved",
                            message=f"{entry['request_id']} approved for ₹{entry['amount']:,}", ref_id=entry_id, ref_type="expense")
    head = await _load_head(entry["expense_head_id"])
    return _entry_out(await _load_entry(entry_id), head)


@router.post("/entries/{entry_id}/reject")
async def reject_expense_entry(entry_id: str, payload: RejectIn, user: dict = Depends(get_current_user)):
    if not _can_approve_expenses(user):
        raise HTTPException(403, "Approver access required")
    entry = await _load_entry(entry_id)
    if not _can_approve_entity_expenses(user, entry["entity_id"]):
        raise HTTPException(403, "Cannot reject expenses for this entity")
    if entry["status"] != "pending":
        raise HTTPException(400, "Entry is not pending")
    log = _audit_entry("rejected", user, payload.reason.strip())
    now = now_utc().isoformat()
    await db.expense_entries.update_one({"id": entry_id}, {"$set": {
        "status": "rejected",
        "rejection_reason": payload.reason.strip(),
        "rejected_at": now,
        "rejected_by_id": user["id"],
        "rejected_by_name": user["name"],
        "updated_at": now,
    }, "$push": {"audit_trail": log}})
    await _write_audit_log(entry_id, log)
    await send_notification(entry["created_by_id"], ntype="expense_rejected", title="Expense rejected",
                            message=f"{entry['request_id']}: {payload.reason.strip()}", ref_id=entry_id, ref_type="expense")
    return await _load_entry(entry_id)


@router.post("/approvals/bulk-approve")
async def bulk_approve_expenses(payload: BulkApproveIn, user: dict = Depends(get_current_user)):
    if not _can_approve_expenses(user):
        raise HTTPException(403, "Approver access required")
    approved = []
    for eid in payload.entry_ids:
        try:
            entry = await _load_entry(eid)
            if entry["status"] != "pending":
                continue
            if not _can_approve_entity_expenses(user, entry["entity_id"]):
                continue
            log = _audit_entry("approved", user, "Bulk approval")
            now = now_utc().isoformat()
            await db.expense_entries.update_one({"id": eid}, {"$set": {
                "status": "approved",
                "approved_at": now,
                "approved_by_id": user["id"],
                "approved_by_name": user["name"],
                "updated_at": now,
            }, "$push": {"audit_trail": log}})
            await _write_audit_log(eid, log)
            await send_notification(entry["created_by_id"], ntype="expense_approved", title="Expense approved",
                                    message=f"{entry['request_id']} approved", ref_id=eid, ref_type="expense")
            approved.append(eid)
        except HTTPException:
            continue
    return {"approved": approved, "count": len(approved)}


@router.get("/entries/{entry_id}/audit")
async def expense_audit_trail(entry_id: str, user: dict = Depends(get_current_user)):
    entry = await _load_entry(entry_id)
    if not _can_view_entity_expenses(user, entry["entity_id"]) and entry["created_by_id"] != user["id"]:
        raise HTTPException(403, "Access denied")
    logs = await db.expense_audit_logs.find({"entry_id": entry_id}, {"_id": 0}).sort("at", 1).to_list(100)
    return {"entry_id": entry_id, "audit_trail": entry.get("audit_trail") or [], "logs": logs}


# -------------------- Finance Reports integration --------------------
@router.get("/summary")
async def expense_outflow_summary(
    user: dict = Depends(get_current_user),
    entity_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    venue: Optional[str] = None,
    all_statuses: bool = Query(False, description="Include pending and rejected entries for finance expense report"),
):
    if entity_id:
        if entity_id not in ENTITY_IDS:
            raise HTTPException(400, "entity_id must be pws or alpha")
        if not _can_view_entity_expenses(user, entity_id):
            raise HTTPException(403, "Access denied")
    elif not (is_super_admin(user) or _can_view_both_entities(user)):
        raise HTTPException(400, "entity_id required")
    q: Dict[str, Any] = {}
    if all_statuses:
        q["status"] = {"$in": ["pending", "approved", "rejected"]}
    else:
        q["status"] = "approved"
    if entity_id:
        q["entity_id"] = entity_id
    if date_from or date_to:
        rng: Dict[str, Any] = {}
        if date_from:
            rng["$gte"] = date_from[:10]
        if date_to:
            rng["$lte"] = date_to[:10]
        q["expense_date"] = rng
    if venue and venue not in ("All", "all"):
        q["venue"] = venue
    rows = await db.expense_entries.find(q, {"_id": 0}).sort("expense_date", -1).to_list(5000)
    head_ids = list({r["expense_head_id"] for r in rows if r.get("expense_head_id")})
    heads = await db.expense_heads.find({"id": {"$in": head_ids}}, {"_id": 0}).to_list(len(head_ids) or 1)
    hmap = {h["id"]: h for h in heads}
    by_head: Dict[str, Dict[str, Any]] = {}
    by_venue: Dict[str, int] = {}
    summary = {
        "total_amount": 0,
        "total_count": len(rows),
        "pending_count": 0,
        "pending_amount": 0,
        "approved_count": 0,
        "approved_amount": 0,
        "rejected_count": 0,
        "rejected_amount": 0,
    }
    approved_total = 0
    approved_count = 0
    for r in rows:
        amt = int(r.get("amount") or 0)
        status = r.get("status") or "pending"
        summary["total_amount"] += amt
        if status == "pending":
            summary["pending_count"] += 1
            summary["pending_amount"] += amt
        elif status == "approved":
            summary["approved_count"] += 1
            summary["approved_amount"] += amt
            approved_total += amt
            approved_count += 1
        elif status == "rejected":
            summary["rejected_count"] += 1
            summary["rejected_amount"] += amt
        head = hmap.get(r.get("expense_head_id"), {})
        label = r.get("sub_category") or head.get("sub_category") or "Other"
        bucket = by_head.setdefault(label, {"expense_head": label, "main_category": head.get("main_category"), "amount": 0, "count": 0})
        bucket["amount"] += amt
        bucket["count"] += 1
        v = r.get("venue") or "Unassigned"
        by_venue[v] = by_venue.get(v, 0) + amt
    return {
        "summary": summary,
        "totals": {"amount": approved_total if all_statuses else summary["total_amount"], "count": approved_count if all_statuses else len(rows)},
        "by_expense_head": sorted(by_head.values(), key=lambda x: -x["amount"]),
        "by_venue": [{"venue": k, "amount": v} for k, v in sorted(by_venue.items(), key=lambda x: -x[1])],
        "rows": [_entry_out(r, hmap.get(r.get("expense_head_id"))) for r in rows],
    }
