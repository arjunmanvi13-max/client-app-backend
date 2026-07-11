"""Invoice and payment MVP — parallel to legacy `fees` (paid history preserved).

Entity numbering: PWS-YYYY-NNNN, ALPHA-YYYY-NNNN
Receipt numbering: RCP-PWS-YYYY-NNNN, RCP-ALPHA-YYYY-NNNN
"""
import io
import uuid
from datetime import datetime
from typing import Optional, Literal, List
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from core import db, get_current_user, is_admin, is_super_admin, get_perm, now_utc

router = APIRouter(prefix="/invoices", tags=["invoices"])

ENTITY_IDS = ("alpha", "pws")
INVOICE_STATUSES = ("draft", "issued", "partially_paid", "paid", "overdue", "cancelled", "refunded")
STATUS_ALIASES = {"partial": "partially_paid", "void": "cancelled"}


def _entity_title(entity_id: str) -> str:
    return "Prarambhika World School" if entity_id == "pws" else "ALPHA Sports Academy"


def _entity_prefix(entity_id: str) -> str:
    return "PWS" if entity_id == "pws" else "ALPHA"


def _normalize_status(status: Optional[str]) -> str:
    if not status:
        return "draft"
    return STATUS_ALIASES.get(status, status)


def _require_view_fees(user: dict) -> None:
    if not get_perm(user, "view_fees"):
        raise HTTPException(403, "view_fees permission required")


def _require_collect_fees(user: dict) -> None:
    if not get_perm(user, "collect_fees"):
        raise HTTPException(403, "collect_fees permission required")


def _can_manage_invoices(user: dict) -> bool:
    return is_super_admin(user) or user.get("role") in ("principal", "vice_principal")


async def get_entity_settings(entity_id: str) -> dict:
    doc = await db.entity_settings.find_one({"entity_id": entity_id}, {"_id": 0})
    if not doc:
        return {"entity_id": entity_id, "use_invoice_engine": False, "tax_rate_percent": 0}
    return doc


async def is_invoice_engine_enabled(entity_id: str) -> bool:
    return bool((await get_entity_settings(entity_id)).get("use_invoice_engine"))


async def _next_sequence(key: str) -> int:
    doc = await db.counters.find_one_and_update(
        {"_id": key},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc.get("seq", 1))


async def _next_invoice_number(entity_id: str) -> str:
    year = now_utc().year
    seq = await _next_sequence(f"invoice_{entity_id}_{year}")
    return f"{_entity_prefix(entity_id)}-{year}-{seq:04d}"


async def _next_receipt_number(entity_id: str) -> str:
    year = now_utc().year
    seq = await _next_sequence(f"receipt_{entity_id}_{year}")
    return f"RCP-{_entity_prefix(entity_id)}-{year}-{seq:04d}"


def _fee_entity_filter(entity_id: str) -> dict:
    if entity_id == "pws":
        return {"entity_id": "pws"}
    return {"$or": [{"entity_id": "alpha"}, {"entity_id": {"$exists": False}}]}


def _invoice_totals(items: List[dict], concession: int = 0, tax_rate: float = 0) -> dict:
    subtotal = sum(int(i.get("line_total", 0)) for i in items)
    paid = sum(int(i.get("amount_paid", 0)) for i in items)
    taxable = max(subtotal - concession, 0)
    tax_amount = int(round(taxable * tax_rate / 100)) if tax_rate else 0
    total = taxable + tax_amount
    balance = max(total - paid, 0)
    return {
        "subtotal": subtotal,
        "concession_amount": concession,
        "tax_amount": tax_amount,
        "total_amount": total,
        "amount_paid": paid,
        "balance_due": balance,
        "outstanding_amount": balance,
    }


def _is_overdue(inv: dict) -> bool:
    if inv.get("status") in ("paid", "cancelled", "refunded", "draft"):
        return False
    due = inv.get("due_date") or ""
    if not due or len(due) < 10:
        return False
    try:
        return due[:10] < now_utc().strftime("%Y-%m-%d") and int(inv.get("balance_due", 0)) > 0
    except Exception:
        return False


def _derive_status(inv: dict, items: List[dict]) -> str:
    current = _normalize_status(inv.get("status"))
    if current in ("cancelled", "refunded", "draft"):
        return current
    totals = _invoice_totals(
        items,
        int(inv.get("concession_amount") or 0),
        float(inv.get("tax_rate_percent") or 0),
    )
    total, paid, balance = totals["total_amount"], totals["amount_paid"], totals["balance_due"]
    if total <= 0 and paid <= 0:
        return current if current == "draft" else "issued"
    if paid <= 0:
        st = "issued" if current != "draft" else "draft"
    elif paid >= total:
        return "paid"
    else:
        st = "partially_paid"
    if _is_overdue({**inv, "balance_due": balance, "status": st}):
        return "overdue"
    return st


def _serialize_invoice(inv: dict, items: Optional[List[dict]] = None) -> dict:
    inv = dict(inv)
    inv["status"] = _normalize_status(inv.get("status"))
    if items is not None:
        inv["items"] = items
        inv["status"] = _derive_status(inv, items)
    inv["outstanding_amount"] = inv.get("outstanding_amount", inv.get("balance_due", 0))
    return inv


async def _load_invoice(invoice_id: str, refresh_status: bool = True) -> dict:
    inv = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not inv:
        raise HTTPException(404, "Invoice not found")
    items = await db.invoice_items.find({"invoice_id": invoice_id}, {"_id": 0}).sort("created_at", 1).to_list(500)
    inv = _serialize_invoice(inv, items)
    if refresh_status and inv["status"] not in ("cancelled", "refunded", "draft"):
        prev_status = inv.get("status")
        new_status = _derive_status(inv, items)
        if new_status != prev_status:
            await db.invoices.update_one(
                {"id": invoice_id},
                {"$set": {"status": new_status, "updated_at": now_utc().isoformat()}},
            )
            inv["status"] = new_status
            if new_status == "overdue":
                await _notify_invoice_event(
                    inv,
                    "invoice_overdue",
                    "Invoice overdue",
                    f"Invoice {inv.get('invoice_number', invoice_id)} is overdue. Balance due: ₹{int(inv.get('balance_due') or 0):,}.",
                )
    return inv


async def _notify_invoice_event(inv: dict, ntype: str, title: str, message: str) -> None:
    """Notify linked parents when an invoice is issued or becomes overdue."""
    person_id = inv.get("person_id")
    if not person_id:
        return
    try:
        from notifications_service import send_notification, notify_person_parents
        person = await db.people.find_one({"id": person_id}, {"_id": 0, "parent_user_ids": 1})
        parent_ids = (person or {}).get("parent_user_ids") or []
        if not parent_ids:
            return
        for pid in parent_ids:
            await send_notification(
                pid,
                ntype=ntype,
                title=title,
                message=message,
                ref_id=inv["id"],
                ref_type="invoice",
                entity_id=inv.get("entity_id"),
            )
    except Exception:
        pass


def _rs(n) -> str:
    return f"Rs. {int(n or 0):,}"


def _render_invoice_pdf(inv: dict, person: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    title = _entity_title(inv["entity_id"])
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    W, H = A4

    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.rect(0, H - 38 * mm, W, 38 * mm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(20 * mm, H - 18 * mm, title)
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, H - 25 * mm, "Tax Invoice / Fee Invoice")
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(W - 20 * mm, H - 18 * mm, inv.get("invoice_number") or "")
    c.setFont("Helvetica", 9)
    c.drawRightString(W - 20 * mm, H - 25 * mm, f"Issue: {inv.get('issue_date') or '-'} · Due: {inv.get('due_date') or '-'}")

    y = H - 50 * mm
    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, "Bill To")
    y -= 7 * mm
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, y, inv.get("person_name") or person.get("name") or "-")
    y -= 6 * mm
    c.drawString(20 * mm, y, f"Status: {_normalize_status(inv.get('status')).upper()}")
    y -= 10 * mm

    c.setFillColorRGB(0.95, 0.96, 0.98)
    c.rect(20 * mm, y - 2 * mm, W - 40 * mm, 8 * mm, fill=1, stroke=0)
    c.setFillColorRGB(0.28, 0.33, 0.41)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(23 * mm, y, "DESCRIPTION")
    c.drawRightString(W - 23 * mm, y, "AMOUNT")
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    for it in inv.get("items", []):
        c.setFillColorRGB(0.06, 0.09, 0.16)
        c.drawString(23 * mm, y, str(it.get("description") or "-")[:60])
        c.drawRightString(W - 23 * mm, y, _rs(it.get("line_total", 0)))
        y -= 6.5 * mm
        if y < 80 * mm:
            c.showPage()
            y = H - 30 * mm

    y -= 4 * mm
    c.setFont("Helvetica", 10)
    if inv.get("subtotal") is not None:
        c.drawString(23 * mm, y, "Subtotal")
        c.drawRightString(W - 23 * mm, y, _rs(inv.get("subtotal")))
        y -= 6 * mm
    if int(inv.get("concession_amount") or 0) > 0:
        c.drawString(23 * mm, y, "Concession / Discount")
        c.drawRightString(W - 23 * mm, y, f"- {_rs(inv.get('concession_amount'))}")
        y -= 6 * mm
    if int(inv.get("tax_amount") or 0) > 0:
        c.drawString(23 * mm, y, f"Tax ({inv.get('tax_rate_percent', 0)}%)")
        c.drawRightString(W - 23 * mm, y, _rs(inv.get("tax_amount")))
        y -= 6 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(23 * mm, y, "Total")
    c.drawRightString(W - 23 * mm, y, _rs(inv.get("total_amount", 0)))
    y -= 7 * mm
    c.setFont("Helvetica", 10)
    c.drawString(23 * mm, y, "Paid")
    c.drawRightString(W - 23 * mm, y, _rs(inv.get("amount_paid", 0)))
    y -= 7 * mm
    c.setFont("Helvetica-Bold", 11)
    c.setFillColorRGB(0.02, 0.53, 0.32)
    c.drawString(23 * mm, y, "Outstanding")
    c.drawRightString(W - 23 * mm, y, _rs(inv.get("outstanding_amount", inv.get("balance_due", 0))))

    c.setFont("Helvetica-Oblique", 8)
    c.setFillColorRGB(0.58, 0.64, 0.72)
    c.drawCentredString(W / 2, 15 * mm, "Computer-generated invoice — not a statutory tax document unless configured.")
    c.save()
    return buf.getvalue()


def _render_receipt_pdf(payment: dict, inv: dict, person: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    title = _entity_title(inv["entity_id"])
    is_pws = inv.get("entity_id") == "pws"
    person_label = "Student" if is_pws else "Player"
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    W, H = A4

    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.rect(0, H - 38 * mm, W, 38 * mm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(20 * mm, H - 18 * mm, title)
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, H - 25 * mm, "Payment Receipt")
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(W - 20 * mm, H - 18 * mm, payment.get("receipt_number") or payment["id"][:8].upper())
    c.setFont("Helvetica", 9)
    c.drawRightString(W - 20 * mm, H - 25 * mm, f"Invoice: {inv.get('invoice_number') or '-'}")

    y = H - 50 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, f"{person_label} Details")
    y -= 7 * mm
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, y, inv.get("person_name") or person.get("name") or "-")
    y -= 10 * mm

    item_map = {i["id"]: i for i in inv.get("items", [])}
    c.setFillColorRGB(0.95, 0.96, 0.98)
    c.rect(20 * mm, y - 2 * mm, W - 40 * mm, 8 * mm, fill=1, stroke=0)
    c.setFillColorRGB(0.28, 0.33, 0.41)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(23 * mm, y, "ALLOCATION")
    c.drawRightString(W - 23 * mm, y, "AMOUNT")
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    for alloc in payment.get("allocations") or []:
        it = item_map.get(alloc.get("item_id"), {})
        desc = it.get("description") or alloc.get("item_id", "-")
        c.drawString(23 * mm, y, str(desc)[:55])
        c.drawRightString(W - 23 * mm, y, _rs(alloc.get("amount", 0)))
        y -= 6.5 * mm

    y -= 4 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(23 * mm, y, "Amount Received")
    c.setFillColorRGB(0.02, 0.53, 0.32)
    c.drawRightString(W - 23 * mm, y, _rs(payment.get("amount", 0)))
    y -= 14 * mm

    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, "Payment Details")
    y -= 7 * mm
    c.setFont("Helvetica", 10)
    for label, val in [
        ("Mode", payment.get("payment_mode")),
        ("Reference", payment.get("reference_id") or "-"),
        ("Date", payment.get("transaction_date") or "-"),
        ("Collected by", payment.get("collected_by_name") or "-"),
        ("Status", payment.get("status", "completed")),
    ]:
        c.drawString(20 * mm, y, f"{label}: {val}")
        y -= 6 * mm

    c.setFont("Helvetica-Oblique", 8)
    c.setFillColorRGB(0.58, 0.64, 0.72)
    c.drawCentredString(W / 2, 15 * mm, "This is a computer-generated receipt and does not require a signature.")
    c.save()
    return buf.getvalue()


# ------------------ Config ------------------
@router.get("/config")
async def list_entity_config(user: dict = Depends(get_current_user)):
    _require_view_fees(user)
    rows = await db.entity_settings.find({}, {"_id": 0}).to_list(10)
    by_id = {r["entity_id"]: r for r in rows}
    out = []
    for eid in ENTITY_IDS:
        out.append(by_id.get(eid) or {"entity_id": eid, "use_invoice_engine": False, "tax_rate_percent": 0})
    return {"entities": out, "invoice_statuses": list(INVOICE_STATUSES)}


class EntityConfigPatch(BaseModel):
    use_invoice_engine: Optional[bool] = None
    tax_rate_percent: Optional[float] = Field(None, ge=0, le=100)


@router.patch("/config/{entity_id}")
async def patch_entity_config(entity_id: Literal["alpha", "pws"], payload: EntityConfigPatch, user: dict = Depends(get_current_user)):
    if not is_super_admin(user):
        raise HTTPException(403, "Super Admin only")
    patch = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    patch["entity_id"] = entity_id
    patch["updated_at"] = now_utc().isoformat()
    patch["updated_by"] = user["id"]
    await db.entity_settings.update_one({"entity_id": entity_id}, {"$set": patch}, upsert=True)
    return await get_entity_settings(entity_id)


# ------------------ Reconciliation & migration (legacy fees preserved) ------------------
@router.get("/reconcile/{entity_id}")
async def reconcile_legacy_fees(entity_id: Literal["alpha", "pws"], user: dict = Depends(get_current_user)):
    if not is_super_admin(user):
        raise HTTPException(403, "Super Admin only")
    fee_q = _fee_entity_filter(entity_id)
    fees = await db.fees.find(fee_q, {"_id": 0}).to_list(50000)
    migrated_ids = set()
    async for it in db.invoice_items.find({"legacy_fee_id": {"$exists": True, "$ne": None}}, {"legacy_fee_id": 1}):
        if it.get("legacy_fee_id"):
            migrated_ids.add(it["legacy_fee_id"])

    legacy_total = sum(int(f.get("amount", f.get("amount_due", 0))) for f in fees)
    legacy_paid = sum(int(f.get("amount_due", 0)) for f in fees if f.get("status") == "paid")
    legacy_due = sum(int(f.get("amount_due", 0)) for f in fees if f.get("status") != "paid")
    unmigrated = [f for f in fees if f["id"] not in migrated_ids]

    by_person: dict[str, list] = {}
    for f in unmigrated:
        by_person.setdefault(f["player_id"], []).append(f)

    proposed_total = sum(int(f.get("amount_due", 0)) for f in unmigrated)
    proposed_paid = sum(int(f.get("amount_due", 0)) for f in unmigrated if f.get("status") == "paid")

    return {
        "entity_id": entity_id,
        "legacy": {
            "fee_count": len(fees),
            "total_amount": legacy_total,
            "paid_amount": legacy_paid,
            "due_amount": legacy_due,
            "already_migrated_count": len(migrated_ids),
            "note": "Legacy fee rows are never deleted — invoices are additive.",
        },
        "proposed_opening": {
            "invoice_count": len(by_person),
            "line_count": len(unmigrated),
            "total_amount": proposed_total,
            "paid_amount": proposed_paid,
            "due_amount": proposed_total - proposed_paid,
        },
        "reconciles": True,
        "engine_enabled": await is_invoice_engine_enabled(entity_id),
    }


@router.post("/migrate-legacy/{entity_id}")
async def migrate_legacy_fees(entity_id: Literal["alpha", "pws"], user: dict = Depends(get_current_user)):
    """Create opening invoices from legacy fees (one per person). Idempotent — legacy rows untouched."""
    if not is_super_admin(user):
        raise HTTPException(403, "Super Admin only")
    fee_q = _fee_entity_filter(entity_id)
    fees = await db.fees.find(fee_q, {"_id": 0}).to_list(50000)
    migrated_ids = set()
    async for it in db.invoice_items.find({"legacy_fee_id": {"$exists": True, "$ne": None}}, {"legacy_fee_id": 1}):
        if it.get("legacy_fee_id"):
            migrated_ids.add(it["legacy_fee_id"])

    by_person: dict[str, list] = {}
    for f in fees:
        if f["id"] in migrated_ids:
            continue
        by_person.setdefault(f["player_id"], []).append(f)

    settings = await get_entity_settings(entity_id)
    tax_rate = float(settings.get("tax_rate_percent") or 0)
    today = now_utc().strftime("%Y-%m-%d")
    created_invoices = 0
    created_items = 0

    for person_id, person_fees in by_person.items():
        person = await db.people.find_one({"id": person_id}, {"_id": 0, "name": 1})
        inv_id = str(uuid.uuid4())
        inv_num = await _next_invoice_number(entity_id)
        items = []
        for f in sorted(person_fees, key=lambda x: (x.get("due_date") or "", x.get("fee_type") or "")):
            amt = int(f.get("amount_due", 0))
            paid = amt if f.get("status") == "paid" else 0
            item = {
                "id": str(uuid.uuid4()),
                "invoice_id": inv_id,
                "description": f"{f.get('fee_type', 'Fee')}" + (f" · {f.get('period_month')}" if f.get("period_month") else ""),
                "fee_type": f.get("fee_type"),
                "period_month": f.get("period_month"),
                "line_total": amt,
                "amount_paid": paid,
                "balance_due": max(amt - paid, 0),
                "legacy_fee_id": f["id"],
                "created_at": now_utc().isoformat(),
            }
            items.append(item)
            created_items += 1

        totals = _invoice_totals(items, 0, tax_rate)
        earliest_due = min((f.get("due_date") or "9999-12-31") for f in person_fees)
        inv_doc = {
            "id": inv_id,
            "entity_id": entity_id,
            "person_id": person_id,
            "person_name": (person or {}).get("name") or person_fees[0].get("player_name"),
            "invoice_number": inv_num,
            "issue_date": today,
            "due_date": earliest_due,
            "status": "draft",
            "tax_rate_percent": tax_rate,
            "notes": "Opening balance from legacy fees",
            "is_opening_balance": True,
            "issued_at": None,
            "created_at": now_utc().isoformat(),
            "created_by": user["id"],
            "created_by_name": user["name"],
            **totals,
        }
        inv_doc["status"] = _derive_status(inv_doc, items)
        if inv_doc["status"] == "draft":
            inv_doc["status"] = "issued"
        await db.invoices.insert_one(inv_doc)
        if items:
            await db.invoice_items.insert_many(items)
        created_invoices += 1

    return {
        "entity_id": entity_id,
        "created_invoices": created_invoices,
        "created_items": created_items,
        "skipped_already_migrated": len(migrated_ids),
    }


# ------------------ CRUD ------------------
class InvoiceItemIn(BaseModel):
    description: str
    fee_type: Optional[str] = None
    period_month: Optional[str] = None
    line_total: int = Field(gt=0)
    quantity: int = Field(default=1, ge=1)


class InvoiceCreateIn(BaseModel):
    entity_id: Literal["alpha", "pws"]
    person_id: str
    issue_date: Optional[str] = None
    due_date: str
    items: List[InvoiceItemIn]
    concession_amount: int = Field(default=0, ge=0)
    notes: Optional[str] = None
    as_draft: bool = False


@router.get("")
async def list_invoices(
    entity_id: Optional[Literal["alpha", "pws"]] = None,
    person_id: Optional[str] = None,
    status: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    _require_view_fees(user)
    q: dict = {}
    if entity_id:
        q["entity_id"] = entity_id
    if person_id:
        q["person_id"] = person_id
    if status:
        q["status"] = STATUS_ALIASES.get(status, status)
    rows = await db.invoices.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    out = []
    for inv in rows:
        items = await db.invoice_items.find({"invoice_id": inv["id"]}, {"_id": 0}).to_list(100)
        out.append(_serialize_invoice(inv, items))
    return out


class CancelIn(BaseModel):
    reason: Optional[str] = None


@router.get("/receipts/{payment_id}/pdf")
async def payment_receipt_pdf(payment_id: str):
    """Public receipt PDF — payment UUID acts as capability token."""
    payment = await db.payments.find_one({"id": payment_id}, {"_id": 0})
    if not payment:
        raise HTTPException(404, "Payment not found")
    inv = await _load_invoice(payment["invoice_id"])
    person = await db.people.find_one({"id": inv["person_id"]}, {"_id": 0}) or {}
    pdf = _render_receipt_pdf(payment, inv, person)
    fname = (payment.get("receipt_number") or payment_id).replace("/", "-")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}.pdf"'},
    )


@router.get("/{invoice_id}")
async def get_invoice(invoice_id: str, user: dict = Depends(get_current_user)):
    _require_view_fees(user)
    inv = await _load_invoice(invoice_id)
    inv["payments"] = await db.payments.find({"invoice_id": invoice_id}, {"_id": 0}).sort("created_at", -1).to_list(50)
    inv["refunds"] = await db.refunds.find({"invoice_id": invoice_id}, {"_id": 0}).sort("created_at", -1).to_list(50)
    return inv


@router.post("")
async def create_invoice(payload: InvoiceCreateIn, user: dict = Depends(get_current_user)):
    if not _can_manage_invoices(user):
        raise HTTPException(403, "Not authorized to create invoices")
    if not await is_invoice_engine_enabled(payload.entity_id):
        raise HTTPException(400, f"Invoice engine is disabled for {payload.entity_id}. Enable the feature flag first.")
    person = await db.people.find_one({"id": payload.person_id}, {"_id": 0})
    if not person:
        raise HTTPException(404, "Person not found")
    if not payload.items:
        raise HTTPException(400, "At least one line item required")

    settings = await get_entity_settings(payload.entity_id)
    tax_rate = float(settings.get("tax_rate_percent") or 0)
    issue_date = payload.issue_date or now_utc().strftime("%Y-%m-%d")
    inv_id = str(uuid.uuid4())
    items = []
    for it in payload.items:
        item = {
            "id": str(uuid.uuid4()),
            "invoice_id": inv_id,
            "description": it.description.strip(),
            "fee_type": it.fee_type,
            "period_month": it.period_month,
            "quantity": it.quantity,
            "line_total": it.line_total,
            "amount_paid": 0,
            "balance_due": it.line_total,
            "legacy_fee_id": None,
            "created_at": now_utc().isoformat(),
        }
        items.append(item)

    totals = _invoice_totals(items, payload.concession_amount, tax_rate)
    status = "draft" if payload.as_draft else "issued"
    inv = {
        "id": inv_id,
        "entity_id": payload.entity_id,
        "person_id": payload.person_id,
        "person_name": person.get("name"),
        "invoice_number": await _next_invoice_number(payload.entity_id),
        "issue_date": issue_date,
        "due_date": payload.due_date,
        "status": status,
        "tax_rate_percent": tax_rate,
        "notes": payload.notes,
        "is_opening_balance": False,
        "issued_at": None if payload.as_draft else now_utc().isoformat(),
        "created_at": now_utc().isoformat(),
        "created_by": user["id"],
        "created_by_name": user["name"],
        **totals,
    }
    inv["status"] = _derive_status(inv, items)
    await db.invoices.insert_one(inv)
    if items:
        await db.invoice_items.insert_many(items)
    inv["items"] = items
    serialized = _serialize_invoice(inv, items)
    if not payload.as_draft:
        await _notify_invoice_event(
            serialized,
            "invoice_issued",
            "New invoice",
            f"Invoice {serialized.get('invoice_number', inv_id)} issued. Amount due: ₹{int(serialized.get('balance_due') or serialized.get('total_amount') or 0):,}.",
        )
    return serialized


@router.post("/{invoice_id}/issue")
async def issue_invoice(invoice_id: str, user: dict = Depends(get_current_user)):
    if not _can_manage_invoices(user):
        raise HTTPException(403, "Not authorized")
    inv = await _load_invoice(invoice_id, refresh_status=False)
    if _normalize_status(inv["status"]) != "draft":
        raise HTTPException(400, "Only draft invoices can be issued")
    ts = now_utc().isoformat()
    await db.invoices.update_one(
        {"id": invoice_id},
        {"$set": {
            "status": _derive_status({**inv, "status": "issued"}, inv["items"]),
            "issued_at": ts,
            "issue_date": inv.get("issue_date") or ts[:10],
            "updated_at": ts,
            "issued_by": user["id"],
        }},
    )
    refreshed = await _load_invoice(invoice_id)
    await _notify_invoice_event(
        refreshed,
        "invoice_issued",
        "New invoice",
        f"Invoice {refreshed.get('invoice_number', invoice_id)} issued. Amount due: ₹{int(refreshed.get('balance_due') or refreshed.get('total_amount') or 0):,}.",
    )
    return refreshed


@router.post("/{invoice_id}/cancel")
async def cancel_invoice(invoice_id: str, payload: CancelIn = CancelIn(), user: dict = Depends(get_current_user)):
    if not _can_manage_invoices(user):
        raise HTTPException(403, "Not authorized")
    inv = await _load_invoice(invoice_id, refresh_status=False)
    st = _normalize_status(inv["status"])
    if st in ("paid", "cancelled", "refunded"):
        raise HTTPException(400, f"Cannot cancel invoice in status {st}")
    if int(inv.get("amount_paid") or 0) > 0:
        raise HTTPException(400, "Refund payments before cancelling a partially paid invoice")
    await db.invoices.update_one(
        {"id": invoice_id},
        {"$set": {
            "status": "cancelled",
            "cancelled_at": now_utc().isoformat(),
            "cancelled_by": user["id"],
            "cancellation_reason": (payload.reason or "").strip() or None,
            "updated_at": now_utc().isoformat(),
        }},
    )
    return await _load_invoice(invoice_id)


# ------------------ Payments ------------------
class AllocationIn(BaseModel):
    item_id: str
    amount: int = Field(gt=0)


class PaymentIn(BaseModel):
    amount: int = Field(gt=0)
    payment_mode: Literal["Cash", "Online"]
    reference_id: Optional[str] = None
    transaction_date: Optional[str] = None
    allocations: List[AllocationIn]
    notes: Optional[str] = None


@router.post("/{invoice_id}/payments")
async def record_payment(invoice_id: str, payload: PaymentIn, user: dict = Depends(get_current_user)):
    _require_collect_fees(user)
    inv = await _load_invoice(invoice_id, refresh_status=False)
    st = _normalize_status(inv["status"])
    if st in ("cancelled", "refunded"):
        raise HTTPException(400, f"Cannot pay invoice with status {st}")
    if st == "paid":
        raise HTTPException(400, "Invoice already fully paid")
    if payload.payment_mode == "Online" and not (payload.reference_id or "").strip():
        raise HTTPException(400, "Reference ID required for Online payments")
    if not payload.allocations:
        raise HTTPException(400, "At least one allocation required")

    alloc_sum = sum(a.amount for a in payload.allocations)
    if alloc_sum != payload.amount:
        raise HTTPException(400, "Allocation sum must equal payment amount")

    item_map = {i["id"]: i for i in inv["items"]}
    for a in payload.allocations:
        if a.item_id not in item_map:
            raise HTTPException(400, f"Unknown item {a.item_id}")
        if a.amount > item_map[a.item_id].get("balance_due", 0):
            raise HTTPException(400, f"Allocation exceeds balance for item {a.item_id}")

    pay_id = str(uuid.uuid4())
    receipt_number = await _next_receipt_number(inv["entity_id"])
    txn_date = payload.transaction_date or now_utc().strftime("%Y-%m-%d")
    payment = {
        "id": pay_id,
        "invoice_id": invoice_id,
        "entity_id": inv["entity_id"],
        "person_id": inv["person_id"],
        "receipt_number": receipt_number,
        "amount": payload.amount,
        "payment_mode": payload.payment_mode,
        "reference_id": payload.reference_id,
        "transaction_date": txn_date,
        "allocations": [a.model_dump() for a in payload.allocations],
        "notes": payload.notes,
        "status": "completed",
        "collected_by_id": user["id"],
        "collected_by_name": user["name"],
        "created_at": now_utc().isoformat(),
    }
    await db.payments.insert_one(payment)

    for a in payload.allocations:
        item = item_map[a.item_id]
        new_paid = int(item.get("amount_paid", 0)) + a.amount
        new_balance = max(int(item.get("line_total", 0)) - new_paid, 0)
        await db.invoice_items.update_one(
            {"id": a.item_id},
            {"$set": {"amount_paid": new_paid, "balance_due": new_balance}},
        )

    refreshed = await _load_invoice(invoice_id, refresh_status=False)
    totals = _invoice_totals(
        refreshed["items"],
        int(refreshed.get("concession_amount") or 0),
        float(refreshed.get("tax_rate_percent") or 0),
    )
    status = _derive_status({**refreshed, **totals}, refreshed["items"])
    await db.invoices.update_one(
        {"id": invoice_id},
        {"$set": {**totals, "status": status, "updated_at": now_utc().isoformat()}},
    )
    refreshed = await _load_invoice(invoice_id)
    refreshed["payment"] = payment
    refreshed["receipt_number"] = receipt_number
    return refreshed


@router.get("/{invoice_id}/payments")
async def list_invoice_payments(invoice_id: str, user: dict = Depends(get_current_user)):
    _require_view_fees(user)
    return await db.payments.find({"invoice_id": invoice_id}, {"_id": 0}).sort("created_at", -1).to_list(100)


# ------------------ Refunds ------------------
class RefundIn(BaseModel):
    payment_id: str
    amount: int = Field(gt=0)
    reason: str = Field(min_length=3)


async def execute_refund(
    invoice_id: str,
    payment_id: str,
    amount: int,
    reason: str,
    user_id: str,
    user_name: str,
) -> dict:
    """Apply a refund to a payment. Used by invoice endpoint and approval workflow."""
    inv = await _load_invoice(invoice_id, refresh_status=False)
    payment = await db.payments.find_one({"id": payment_id, "invoice_id": invoice_id}, {"_id": 0})
    if not payment:
        raise HTTPException(404, "Payment not found on this invoice")
    if payment.get("status") == "refunded":
        raise HTTPException(400, "Payment already fully refunded")
    refunded_so_far = int(payment.get("refunded_amount") or 0)
    remaining = int(payment.get("amount", 0)) - refunded_so_far
    if amount > remaining:
        raise HTTPException(400, f"Refund cannot exceed remaining payment amount ({remaining})")

    item_map = {i["id"]: i for i in inv["items"]}
    to_reverse = amount
    reversed_allocs = []
    for alloc in reversed(payment.get("allocations") or []):
        if to_reverse <= 0:
            break
        rev = min(to_reverse, int(alloc.get("amount", 0)))
        if rev <= 0:
            continue
        item = item_map.get(alloc["item_id"])
        if item:
            new_paid = max(int(item.get("amount_paid", 0)) - rev, 0)
            new_balance = int(item.get("line_total", 0)) - new_paid
            await db.invoice_items.update_one(
                {"id": alloc["item_id"]},
                {"$set": {"amount_paid": new_paid, "balance_due": new_balance}},
            )
        reversed_allocs.append({"item_id": alloc["item_id"], "amount": rev})
        to_reverse -= rev

    refund_id = str(uuid.uuid4())
    refund = {
        "id": refund_id,
        "invoice_id": invoice_id,
        "payment_id": payment_id,
        "entity_id": inv["entity_id"],
        "person_id": inv["person_id"],
        "amount": amount,
        "reason": reason.strip(),
        "reversed_allocations": reversed_allocs,
        "authorized_by_id": user_id,
        "authorized_by_name": user_name,
        "created_at": now_utc().isoformat(),
    }
    await db.refunds.insert_one(refund)

    new_refunded = refunded_so_far + amount
    pay_status = "refunded" if new_refunded >= int(payment.get("amount", 0)) else "partially_refunded"
    await db.payments.update_one(
        {"id": payment_id},
        {"$set": {"refunded_amount": new_refunded, "status": pay_status, "updated_at": now_utc().isoformat()}},
    )

    refreshed = await _load_invoice(invoice_id, refresh_status=False)
    totals = _invoice_totals(
        refreshed["items"],
        int(refreshed.get("concession_amount") or 0),
        float(refreshed.get("tax_rate_percent") or 0),
    )
    inv_status = _derive_status({**refreshed, **totals}, refreshed["items"])
    if pay_status == "refunded" and int(totals["amount_paid"]) == 0 and inv_status == "issued":
        inv_status = "refunded" if _normalize_status(refreshed.get("status")) == "refunded" else inv_status
    await db.invoices.update_one(
        {"id": invoice_id},
        {"$set": {**totals, "status": inv_status, "updated_at": now_utc().isoformat()}},
    )
    return refund


@router.post("/{invoice_id}/refunds")
async def refund_payment(invoice_id: str, payload: RefundIn, user: dict = Depends(get_current_user)):
    can_refund = is_super_admin(user) or _can_manage_invoices(user) or get_perm(user, "approve_requests")
    if not can_refund:
        raise HTTPException(403, "Authorization required for refunds — submit an approval request")
    refund = await execute_refund(
        invoice_id=invoice_id,
        payment_id=payload.payment_id,
        amount=payload.amount,
        reason=payload.reason,
        user_id=user["id"],
        user_name=user["name"],
    )
    return {"invoice": await _load_invoice(invoice_id), "refund": refund}


# ------------------ PDFs ------------------
@router.get("/{invoice_id}/pdf")
async def invoice_pdf(invoice_id: str):
    """Public PDF — invoice UUID acts as capability token."""
    inv = await _load_invoice(invoice_id)
    person = await db.people.find_one({"id": inv["person_id"]}, {"_id": 0}) or {}
    pdf = _render_invoice_pdf(inv, person)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{inv.get("invoice_number", invoice_id)}.pdf"'},
    )

