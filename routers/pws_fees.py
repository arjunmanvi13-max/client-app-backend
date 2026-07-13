"""PWS fee collection — 2026-27 roadmap, sync, and invoice export."""
import uuid
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from core import db, get_current_user, get_perm, is_super_admin, now_utc, format_date_display, format_month_display
from pws_fee_structure import (
    PWS_ACADEMIC_YEAR,
    PWS_CLASSES,
    PWS_STUDENT_TYPES,
    TRANSPORT_DISTANCES,
    build_pws_fee_schedule,
    pws_student_profile_from_person,
    resolve_category_amounts,
    structure_metadata,
    student_type_to_legacy,
)
from routers.fees import (
    _build_fee,
    _fy_end,
    _month_key,
    _require_view_fees,
    first_month_amount,
)

router = APIRouter(prefix="/pws-fees", tags=["pws-fees"])


def _can_override_fees(user: dict) -> bool:
    return is_super_admin(user) or user.get("role") in ("principal", "vice_principal")


def _require_collect(user: dict) -> None:
    if not get_perm(user, "collect_fees"):
        raise HTTPException(403, "collect_fees permission required")


async def _get_pws_student(student_id: str) -> dict:
    person = await db.people.find_one({"id": student_id, "kind": "student"}, {"_id": 0})
    if not person:
        raise HTTPException(404, "Student not found")
    if person.get("organization") != "PWS":
        raise HTTPException(400, "PWS students only")
    return person


async def sync_pws_fees_for_student(student: dict) -> List[dict]:
    """Materialize scheduled fees into db.fees (idempotent)."""
    profile = pws_student_profile_from_person(student)
    schedule = build_pws_fee_schedule(
        profile["pws_class"],
        profile["date_of_admission"],
        profile["transport_enabled"],
        profile["transport_distance"],
        profile["overrides"],
    )
    admission = profile["date_of_admission"]
    created: List[dict] = []
    for item in schedule:
        existing = await db.fees.find_one({
            "player_id": student["id"],
            "fee_type": item.fee_type,
            "period_month": item.period_month,
        })
        if existing:
            continue
        amt = item.amount
        if item.fee_type == "Monthly" and item.period_month == _month_key(admission):
            amt = first_month_amount(item.amount, admission)
        due = f"{item.period_month}-05"
        if item.fee_type in ("Registration", "Admission", "Security", "Annual"):
            due = admission
        doc = _build_fee(student, item.fee_type, item.amount, amt, item.period_month, due, extra={
            "pws_category": item.category,
            "academic_year": PWS_ACADEMIC_YEAR,
        })
        await db.fees.insert_one(doc)
        created.append(doc)
    return created


def _roadmap_month_label(period: str) -> str:
    return format_month_display(period)


@router.get("/structure")
async def get_structure(user: dict = Depends(get_current_user)):
    _require_view_fees(user)
    return structure_metadata()


class PreviewIn(BaseModel):
    pws_class: str
    pws_student_type: Literal["Day School", "Boarding", "Day Boarding"] = "Day School"
    transport_enabled: bool = False
    transport_distance: Optional[Literal["Up to 5 km", "Over 5 km"]] = None
    date_of_admission: Optional[str] = None
    overrides: Optional[Dict[str, int]] = None


@router.post("/preview")
async def preview_fees(payload: PreviewIn, user: dict = Depends(get_current_user)):
    _require_view_fees(user)
    if payload.pws_class not in PWS_CLASSES:
        raise HTTPException(400, f"Invalid class — choose from {PWS_CLASSES}")
    if payload.transport_enabled and not payload.transport_distance:
        raise HTTPException(400, "transport_distance required when transport is enabled")
    amounts = resolve_category_amounts(
        payload.pws_class,
        payload.transport_enabled,
        payload.transport_distance,
        payload.overrides if _can_override_fees(user) else None,
    )
    admission = payload.date_of_admission or now_utc().strftime("%Y-%m-%d")
    schedule = build_pws_fee_schedule(
        payload.pws_class,
        admission,
        payload.transport_enabled,
        payload.transport_distance,
        payload.overrides if _can_override_fees(user) else None,
    )
    return {
        "amounts": amounts,
        "schedule": [s.to_dict() for s in schedule],
        "total": sum(s.amount for s in schedule),
        "can_override": _can_override_fees(user),
    }


@router.get("/roadmap/{student_id}")
async def get_roadmap(student_id: str, user: dict = Depends(get_current_user)):
    _require_view_fees(user)
    student = await _get_pws_student(student_id)
    await sync_pws_fees_for_student(student)
    profile = pws_student_profile_from_person(student)
    schedule = build_pws_fee_schedule(
        profile["pws_class"],
        profile["date_of_admission"],
        profile["transport_enabled"],
        profile["transport_distance"],
        profile["overrides"],
    )
    fees = await db.fees.find({"player_id": student_id, "entity_id": "pws"}, {"_id": 0}).to_list(500)
    fee_index = {(f.get("fee_type"), f.get("period_month")): f for f in fees}

    months_map: Dict[str, list] = {}
    for item in schedule:
        months_map.setdefault(item.period_month, [])
        key = (item.fee_type, item.period_month)
        row = fee_index.get(key)
        months_map[item.period_month].append({
            "category": item.category,
            "fee_type": item.fee_type,
            "period_month": item.period_month,
            "amount": item.amount,
            "amount_due": row.get("amount_due", item.amount) if row else item.amount,
            "fee_id": row.get("id") if row else None,
            "status": row.get("status", "due") if row else "due",
            "paid_at": row.get("paid_at"),
        })

    months = []
    for period in sorted(months_map.keys()):
        items = months_map[period]
        months.append({
            "period_month": period,
            "label": _roadmap_month_label(period),
            "items": items,
            "month_total": sum(i["amount_due"] for i in items if i["status"] != "paid"),
            "paid_total": sum(i["amount_due"] for i in items if i["status"] == "paid"),
        })

    unpaid = [f for f in fees if f.get("status") != "paid"]
    paid = [f for f in fees if f.get("status") == "paid"]
    current_month = now_utc().strftime("%Y-%m")

    return {
        "academic_year": PWS_ACADEMIC_YEAR,
        "student": {
            "id": student["id"],
            "name": student["name"],
            "pws_class": profile["pws_class"],
            "pws_student_type": profile["pws_student_type"],
            "transport_enabled": profile["transport_enabled"],
            "transport_distance": profile["transport_distance"],
            "group": student.get("group"),
            "admission_number": student.get("admission_number"),
        },
        "amounts": resolve_category_amounts(
            profile["pws_class"],
            profile["transport_enabled"],
            profile["transport_distance"],
            profile["overrides"],
        ),
        "months": months,
        "summary": {
            "total_outstanding": sum(f.get("amount_due", 0) for f in unpaid),
            "paid_total": sum(f.get("amount_due", 0) for f in paid),
            "current_month": current_month,
            "financial_year_end": _fy_end(current_month),
        },
        "can_override": _can_override_fees(user),
        "can_collect": bool(get_perm(user, "collect_fees")),
    }


class CollectRoadmapIn(BaseModel):
    fee_ids: List[str]
    payment_mode: Literal["Cash", "Online"]
    reference_id: Optional[str] = None
    transaction_date: Optional[str] = None
    notes: Optional[str] = None


@router.post("/collect")
async def collect_roadmap_fees(payload: CollectRoadmapIn, user: dict = Depends(get_current_user)):
    _require_collect(user)
    if not payload.fee_ids:
        raise HTTPException(400, "Select at least one fee")
    if payload.payment_mode == "Online" and not (payload.reference_id or "").strip():
        raise HTTPException(400, "Reference ID required for Online payments")
    fees = await db.fees.find({"id": {"$in": payload.fee_ids}}, {"_id": 0}).to_list(100)
    if len(fees) != len(payload.fee_ids):
        raise HTTPException(404, "One or more fees not found")
    player_ids = {f["player_id"] for f in fees}
    if len(player_ids) != 1:
        raise HTTPException(400, "All fees must belong to the same student")
    if any(f.get("status") == "paid" for f in fees):
        raise HTTPException(400, "Some fees are already paid")
    batch_id = str(uuid.uuid4())
    paid_at = now_utc().isoformat()
    txn_date = payload.transaction_date or now_utc().strftime("%Y-%m-%d")
    update = {
        "status": "paid",
        "payment_mode": payload.payment_mode,
        "reference_id": payload.reference_id or None,
        "transaction_date": txn_date,
        "paid_at": paid_at,
        "collected_by_id": user["id"],
        "collected_by_name": user["name"],
        "batch_id": batch_id,
        "notes": payload.notes or None,
    }
    await db.fees.update_many({"id": {"$in": payload.fee_ids}}, {"$set": update})
    student = await db.people.find_one({"id": next(iter(player_ids))}, {"_id": 0})
    fees_after = await db.fees.find({"id": {"$in": payload.fee_ids}}, {"_id": 0}).sort("period_month", 1).to_list(100)
    return {
        "batch_id": batch_id,
        "student": student,
        "fees": fees_after,
        "total_amount": sum(f.get("amount_due", 0) for f in fees_after),
        "payment_mode": payload.payment_mode,
        "collected_by": {"id": user["id"], "name": user["name"]},
    }


@router.get("/invoice/{student_id}/pdf")
async def export_invoice_pdf(student_id: str, user: dict = Depends(get_current_user)):
    _require_view_fees(user)
    student = await _get_pws_student(student_id)
    await sync_pws_fees_for_student(student)
    profile = pws_student_profile_from_person(student)
    fees = await db.fees.find({"player_id": student_id, "entity_id": "pws"}, {"_id": 0}).sort("period_month", 1).to_list(500)

    from fee_receipt_pdf import render_pws_fee_statement_pdf

    pdf_bytes = render_pws_fee_statement_pdf(
        student,
        profile,
        fees,
        PWS_ACADEMIC_YEAR,
        format_month=format_month_display,
        format_date=lambda _: format_date_display(now_utc().strftime("%Y-%m-%d")),
    )
    fname = f"pws-fees-{student.get('name', 'student').replace(' ', '-').lower()}.pdf"
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fname}"'})
