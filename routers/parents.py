"""Parent portal MVP — read-only access scoped to linked children only."""
from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from core import db, get_current_user, now_utc, derive_person_entities
from notifications_service import normalize_notification, notification_filter_for_user, mark_read

router = APIRouter(prefix="/parent", tags=["parent"])

ENTITY_NAMES = {
    "PWS": "Prarambhika World School",
    "ALPHA": "ALPHA Sports Academy",
}


def _ensure_parent(user: dict) -> None:
    if user.get("role") != "parent":
        raise HTTPException(403, "Parent role required")


def _linked_ids(user: dict) -> list[str]:
    return user.get("linked_person_ids") or []


def _assert_ward_access(user: dict, person_id: str) -> None:
    _ensure_parent(user)
    if person_id not in _linked_ids(user):
        raise HTTPException(404, "Ward not found")


def _entity_labels(person: dict) -> list[dict]:
    ents = derive_person_entities(person)
    labels = []
    if "PWS" in ents:
        labels.append({
            "code": "PWS",
            "name": ENTITY_NAMES["PWS"],
            "short": "School",
        })
    if "ALPHA" in ents:
        labels.append({
            "code": "ALPHA",
            "name": ENTITY_NAMES["ALPHA"],
            "short": "Sports",
        })
    return labels


def _public_profile(person: dict) -> dict:
    """Read-only ward profile — no staff fields or edit metadata."""
    ents = derive_person_entities(person)
    return {
        "id": person.get("id"),
        "name": person.get("name"),
        "kind": person.get("kind"),
        "organization": person.get("organization"),
        "entities": ents,
        "entity_labels": _entity_labels(person),
        "is_dual_participation": len(ents) > 1,
        "group": person.get("group"),
        "section_id": person.get("section_id"),
        "admission_number": person.get("admission_number"),
        "roll_number": person.get("roll_number"),
        "sport": person.get("sport"),
        "centre": person.get("centre"),
        "slot": person.get("slot"),
        "player_type": person.get("player_type"),
        "is_resident": person.get("is_resident"),
        "date_of_admission": person.get("date_of_admission"),
        "gender": person.get("gender"),
        "status": person.get("status"),
    }


async def _wards_for(user: dict) -> list[dict]:
    ids = _linked_ids(user)
    if not ids:
        return []
    return await db.people.find({"id": {"$in": ids}}, {"_id": 0}).to_list(50)


async def _resolve_alpha_player_id(person: dict) -> Optional[str]:
    if person.get("kind") == "player":
        return person["id"]
    if "ALPHA" not in derive_person_entities(person):
        return None
    player = await db.people.find_one(
        {"kind": "player", "name": person.get("name")},
        {"_id": 0, "id": 1},
    )
    return (player or {}).get("id")


def _has_fee_visibility(person: dict) -> bool:
    org = person.get("organization")
    if org in ("PWS", "ALPHA", "BOTH"):
        return True
    return bool(derive_person_entities(person))


@router.get("/profile")
async def parent_profile(user: dict = Depends(get_current_user)):
    _ensure_parent(user)
    return {
        "id": user["id"],
        "name": user.get("name"),
        "email": user.get("email"),
        "mobile": user.get("mobile"),
        "role": "parent",
        "linked_person_ids": _linked_ids(user),
        "ward_count": len(_linked_ids(user)),
    }


@router.get("/wards")
async def list_wards(user: dict = Depends(get_current_user)):
    _ensure_parent(user)
    wards = await _wards_for(user)
    today = now_utc().strftime("%Y-%m-%d")
    thirty_ago = (now_utc() - timedelta(days=30)).strftime("%Y-%m-%d")
    out = []
    for w in wards:
        today_rec = await db.attendance.find_one(
            {"person_id": w["id"], "date": today}, {"_id": 0, "status": 1}
        )
        records = await db.attendance.find(
            {"person_id": w["id"], "date": {"$gte": thirty_ago}}, {"_id": 0, "status": 1}
        ).to_list(200)
        total = len(records)
        present = sum(1 for r in records if r.get("status") in ("present", "late"))
        profile = _public_profile(w)
        out.append({
            **profile,
            "today_status": (today_rec or {}).get("status"),
            "attendance_30d": {
                "total": total,
                "present": present,
                "absent": total - present,
                "pct": round((present / total) * 100, 1) if total else None,
            },
        })
    return out


@router.get("/ward/{person_id}")
async def ward_profile(person_id: str, user: dict = Depends(get_current_user)):
    _assert_ward_access(user, person_id)
    person = await db.people.find_one({"id": person_id}, {"_id": 0})
    if not person:
        raise HTTPException(404, "Ward not found")
    return _public_profile(person)


@router.get("/attendance/{person_id}")
async def ward_attendance(person_id: str, days: int = 30, user: dict = Depends(get_current_user)):
    _assert_ward_access(user, person_id)
    since = (now_utc() - timedelta(days=max(1, min(days, 90)))).strftime("%Y-%m-%d")
    records = await db.attendance.find(
        {"person_id": person_id, "date": {"$gte": since}}, {"_id": 0}
    ).sort("date", -1).to_list(500)
    return {"person_id": person_id, "days": days, "records": records}


@router.get("/fees/{person_id}")
async def ward_fees(person_id: str, user: dict = Depends(get_current_user)):
    """Legacy fee dues — read-only."""
    _assert_ward_access(user, person_id)
    person = await db.people.find_one({"id": person_id}, {"_id": 0})
    if not person:
        raise HTTPException(404, "Ward not found")
    if not _has_fee_visibility(person):
        return {"person_id": person_id, "fees": [], "summary": {"total_due": 0, "total_paid": 0, "overdue_count": 0}}
    fees = await db.fees.find({"player_id": person_id}, {"_id": 0}).sort("due_date", -1).to_list(200)
    total_due = sum(f.get("amount_due", 0) for f in fees if f.get("status") != "paid")
    total_paid = sum(f.get("amount_due", 0) for f in fees if f.get("status") == "paid")
    today = now_utc().strftime("%Y-%m-%d")
    overdue_count = sum(1 for f in fees if f.get("status") != "paid" and (f.get("due_date") or "9999") < today)
    return {
        "person_id": person_id,
        "fees": fees,
        "summary": {"total_due": total_due, "total_paid": total_paid, "overdue_count": overdue_count},
    }


@router.get("/invoices/{person_id}")
async def ward_invoices(person_id: str, user: dict = Depends(get_current_user)):
    _assert_ward_access(user, person_id)
    rows = await db.invoices.find(
        {
            "person_id": person_id,
            "status": {"$nin": ["draft", "cancelled", "void"]},
        },
        {"_id": 0},
    ).sort("issue_date", -1).to_list(100)
    out = []
    for inv in rows:
        items = await db.invoice_items.find({"invoice_id": inv["id"]}, {"_id": 0}).to_list(50)
        inv["items"] = items
        inv["outstanding_amount"] = inv.get("outstanding_amount", inv.get("balance_due", 0))
        out.append(inv)
    return {"person_id": person_id, "invoices": out}


@router.get("/payments/{person_id}")
async def ward_payments(person_id: str, user: dict = Depends(get_current_user)):
    _assert_ward_access(user, person_id)
    payments = await db.payments.find(
        {"person_id": person_id, "status": {"$ne": "refunded"}},
        {"_id": 0},
    ).sort("created_at", -1).to_list(200)
    inv_ids = list({p["invoice_id"] for p in payments if p.get("invoice_id")})
    inv_map = {}
    if inv_ids:
        invs = await db.invoices.find({"id": {"$in": inv_ids}}, {"_id": 0, "invoice_number": 1, "id": 1}).to_list(200)
        inv_map = {i["id"]: i.get("invoice_number") for i in invs}
    for p in payments:
        p["invoice_number"] = inv_map.get(p.get("invoice_id"))
    return {"person_id": person_id, "payments": payments}


@router.get("/receipts/{person_id}")
async def ward_receipts(person_id: str, user: dict = Depends(get_current_user)):
    """Legacy fee batch receipts + invoice payment receipts."""
    _assert_ward_access(user, person_id)
    receipts = []

    paid_fees = await db.fees.find(
        {"player_id": person_id, "status": "paid", "batch_id": {"$exists": True, "$ne": None}},
        {"_id": 0},
    ).sort("paid_at", -1).to_list(500)
    batches: dict[str, list] = {}
    for f in paid_fees:
        bid = f.get("batch_id")
        if bid:
            batches.setdefault(bid, []).append(f)
    for batch_id, fee_rows in batches.items():
        total = sum(int(x.get("amount_due", 0)) for x in fee_rows)
        f0 = fee_rows[0]
        receipts.append({
            "id": batch_id,
            "type": "legacy_fee",
            "receipt_number": f"RCP-{batch_id[:8].upper()}",
            "amount": total,
            "payment_mode": f0.get("payment_mode"),
            "transaction_date": f0.get("transaction_date") or (f0.get("paid_at") or "")[:10],
            "collected_by_name": f0.get("collected_by_name"),
            "pdf_url": f"/api/fees/receipt/{batch_id}/pdf",
            "line_count": len(fee_rows),
        })

    payments = await db.payments.find(
        {"person_id": person_id, "status": {"$in": ["completed", "partially_refunded"]}},
        {"_id": 0},
    ).sort("created_at", -1).to_list(200)
    for p in payments:
        receipts.append({
            "id": p["id"],
            "type": "invoice_payment",
            "receipt_number": p.get("receipt_number") or p["id"][:8].upper(),
            "amount": p.get("amount"),
            "payment_mode": p.get("payment_mode"),
            "transaction_date": p.get("transaction_date"),
            "collected_by_name": p.get("collected_by_name"),
            "invoice_id": p.get("invoice_id"),
            "pdf_url": f"/api/invoices/receipts/{p['id']}/pdf",
        })

    receipts.sort(key=lambda r: r.get("transaction_date") or "", reverse=True)
    return {"person_id": person_id, "receipts": receipts}


@router.get("/marks/{person_id}")
async def ward_marks(person_id: str, user: dict = Depends(get_current_user)):
    _assert_ward_access(user, person_id)
    from routers.marks import _published_marks_for_person
    marks = await _published_marks_for_person(person_id)
    report_cards = await db.report_cards.find(
        {"person_id": person_id, "status": "published"}, {"_id": 0}
    ).sort("published_at", -1).to_list(20)
    return {
        "person_id": person_id,
        "marks": marks,
        "report_cards": report_cards,
    }


@router.get("/coach-assessments/{person_id}")
async def ward_coach_assessments(person_id: str, user: dict = Depends(get_current_user)):
    _assert_ward_access(user, person_id)
    person = await db.people.find_one({"id": person_id}, {"_id": 0})
    if not person:
        raise HTTPException(404, "Ward not found")
    player_id = await _resolve_alpha_player_id(person)
    if not player_id:
        return {"person_id": person_id, "assessments": [], "entity": "ALPHA", "available": False}
    from routers.coach_assessments import published_for_player
    return {
        "person_id": person_id,
        "player_id": player_id,
        "entity": "ALPHA",
        "available": True,
        "assessments": await published_for_player(player_id),
    }


@router.get("/notifications")
async def parent_notifications(user: dict = Depends(get_current_user)):
    _ensure_parent(user)
    stored_raw = await db.notifications.find(
        notification_filter_for_user(user),
        {"_id": 0},
    ).sort("created_at", -1).to_list(100)
    stored = [normalize_notification(n) for n in stored_raw]
    computed = await _compute_alerts_for_parent(user)
    unread = sum(1 for n in stored if not n.get("read"))
    return {
        "stored": stored,
        "computed": computed,
        "count": len(stored) + len(computed),
        "unread_count": unread,
    }


@router.patch("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, user: dict = Depends(get_current_user)):
    _ensure_parent(user)
    if not await mark_read(user, notification_id):
        raise HTTPException(404, "Notification not found")
    return {"ok": True, "read_at": now_utc().isoformat()}


@router.get("/alerts")
async def parent_alerts(user: dict = Depends(get_current_user)):
    """Backward-compatible alias — use /notifications."""
    return await parent_notifications(user)


async def _compute_alerts_for_parent(user: dict) -> list[dict]:
    alerts: list[dict] = []
    wards = await _wards_for(user)
    today_str = now_utc().strftime("%Y-%m-%d")
    week_ago = (now_utc() - timedelta(days=7)).strftime("%Y-%m-%d")
    cutoff_overdue = (now_utc() - timedelta(days=7)).strftime("%Y-%m-%d")

    for w in wards:
        rec_today = await db.attendance.find_one({"person_id": w["id"], "date": today_str}, {"_id": 0, "status": 1})
        if rec_today and rec_today.get("status") == "absent":
            alerts.append({
                "id": f"absent-{w['id']}-{today_str}",
                "type": "absent_today",
                "severity": "high",
                "title": f"{w['name']} marked absent today",
                "message": f"{w['name']} was marked absent on {today_str}.",
                "ward_id": w["id"],
                "ward_name": w["name"],
                "created_at": now_utc().isoformat(),
            })

        week_recs = await db.attendance.find(
            {"person_id": w["id"], "date": {"$gte": week_ago}}, {"_id": 0, "status": 1}
        ).to_list(50)
        absences = sum(1 for r in week_recs if r.get("status") == "absent")
        if absences >= 3:
            alerts.append({
                "id": f"low7d-{w['id']}-{today_str}",
                "type": "low_attendance_7d",
                "severity": "medium",
                "title": f"{w['name']} — {absences} absences this week",
                "message": f"{w['name']} has been absent {absences} time(s) in the last 7 days.",
                "ward_id": w["id"],
                "ward_name": w["name"],
                "created_at": now_utc().isoformat(),
            })

        if _has_fee_visibility(w):
            overdue = await db.fees.find({
                "player_id": w["id"],
                "status": {"$ne": "paid"},
                "due_date": {"$lt": cutoff_overdue},
            }, {"_id": 0}).to_list(20)
            if overdue:
                total = sum(f.get("amount_due", 0) for f in overdue)
                alerts.append({
                    "id": f"fees-{w['id']}-{today_str}",
                    "type": "fees_overdue",
                    "severity": "medium",
                    "title": f"Fees overdue — {w['name']}",
                    "message": f"₹{total:,.0f} unpaid across {len(overdue)} fee(s) (>7 days overdue).",
                    "ward_id": w["id"],
                    "ward_name": w["name"],
                    "amount_due": total,
                    "created_at": now_utc().isoformat(),
                })

    sev_rank = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: sev_rank.get(a.get("severity", "low"), 9))
    return alerts


async def push_parent_notification(
    person_id: str,
    title: str,
    body: str,
    ntype: str = "absence",
    ref_id: Optional[str] = None,
) -> int:
    from notifications_service import send_notification
    person = await db.people.find_one({"id": person_id}, {"_id": 0, "parent_user_ids": 1, "entities": 1, "organization": 1, "name": 1})
    if not person:
        return 0
    parent_ids = person.get("parent_user_ids") or []
    ents = person.get("entities") or []
    if "PWS" in ents and "ALPHA" in ents:
        entity_id = "both"
    elif "PWS" in ents or person.get("organization") == "PWS":
        entity_id = "pws"
    else:
        entity_id = "alpha"
    sent = 0
    for pid in parent_ids:
        await send_notification(
            pid,
            ntype=ntype,
            title=title,
            message=body,
            ref_id=ref_id or person_id,
            ref_type="attendance",
            entity_id=entity_id,
            dedupe_today=True,
        )
        sent += 1
    return sent
