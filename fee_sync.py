"""Cascade directory fee overrides into db.fees (Collect Fees / ledgers).

When a Super Admin edits fee fields on a player/student profile, unpaid fee rows
are reconciled to match the updated profile. Paid / settled rows are never modified.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Set

from core import db, now_utc, today_ist
from fee_override_approval import FEE_OVERRIDE_KEYS

log = logging.getLogger("fee_sync")

FEE_PROFILE_KEYS = FEE_OVERRIDE_KEYS + (
    "transport_enabled",
    "transport_distance",
    "pws_class",
    "pws_student_type",
    "player_type",
    "date_of_admission",
)


def fee_related_keys_changed(upd: dict, target: dict) -> Set[str]:
    """Return fee/financial profile keys present in ``upd`` whose values changed."""
    changed: Set[str] = set()
    for key in FEE_PROFILE_KEYS:
        if key not in upd:
            continue
        if upd.get(key) != target.get(key):
            changed.add(key)
    return changed


def _month_key(date_iso: str) -> str:
    return date_iso[:7]


def unpaid_fee_is_before_admission(fee: dict, admission_iso: str) -> bool:
    """True when an unpaid ledger row is dated before the admission month."""
    start = (admission_iso or "")[:7]
    period = (fee.get("period_month") or fee.get("due_date") or "")[:7]
    return bool(start) and bool(period) and period < start


async def drop_unpaid_fees_before_admission(
    person: dict,
    audit_buffer: Optional[List[dict]] = None,
) -> int:
    """Remove unpaid rate-card fees whose period is before date_of_admission.

    Paid rows are left untouched. Unpaid Registration is moved to the admission
    month when possible so the one-time charge still exists after a date correction.
    """
    person_id = person.get("id")
    if not person_id:
        return 0
    admission = person.get("date_of_admission") or today_ist()
    start_month = _month_key(admission)
    if len(start_month) < 7:
        return 0

    changed = 0
    buffer = audit_buffer if audit_buffer is not None else []

    regs = await db.fees.find({
        "player_id": person_id,
        "fee_type": "Registration",
        "status": {"$ne": "paid"},
        "period_month": {"$lt": start_month},
    }).to_list(50)
    dest_reg = await db.fees.find_one({
        "player_id": person_id,
        "fee_type": "Registration",
        "period_month": start_month,
    })
    for fee in regs:
        if dest_reg:
            await db.fees.delete_one({"id": fee["id"]})
            action = "removed_before_admission"
        else:
            await db.fees.update_one({"id": fee["id"]}, {"$set": {
                "period_month": start_month,
                "due_date": admission if len(admission) >= 10 else f"{start_month}-01",
                "fee_synced_at": now_utc().isoformat(),
            }})
            dest_reg = {**fee, "period_month": start_month}
            action = "moved_to_admission_month"
        buffer.append({
            "fee_id": fee["id"],
            "fee_type": "Registration",
            "period_month": start_month if action == "moved_to_admission_month" else fee.get("period_month"),
            "previous_amount": int(fee.get("amount") or 0),
            "new_amount": int(fee.get("amount") or 0) if action == "moved_to_admission_month" else 0,
            "previous_amount_due": int(fee.get("amount_due") or 0),
            "new_amount_due": int(fee.get("amount_due") or 0) if action == "moved_to_admission_month" else 0,
            "action": action,
        })
        changed += 1

    cursor = db.fees.find({
        "player_id": person_id,
        "status": {"$ne": "paid"},
        "period_month": {"$lt": start_month},
        "is_adhoc": {"$ne": True},
        "fee_type": {"$ne": "Registration"},
    })
    async for fee in cursor:
        await db.fees.delete_one({"id": fee["id"]})
        buffer.append({
            "fee_id": fee["id"],
            "fee_type": fee.get("fee_type"),
            "period_month": fee.get("period_month"),
            "previous_amount": int(fee.get("amount") or 0),
            "new_amount": 0,
            "previous_amount_due": int(fee.get("amount_due") or 0),
            "new_amount_due": 0,
            "action": "removed_before_admission",
        })
        changed += 1
    return changed


def _first_month_amount(monthly: int, admission_iso: str) -> int:
    from datetime import datetime

    try:
        day = datetime.fromisoformat(admission_iso).day
    except Exception:
        day = 1
    return monthly if day <= 15 else int(monthly / 2)


def compute_amount_due(fee: dict, nominal: int, person: dict) -> int:
    """Target amount_due for an unpaid fee after a nominal amount change."""
    admission = person.get("date_of_admission") or today_ist()
    period = fee.get("period_month") or ""

    def base_due_for(nom: int) -> int:
        due = nom
        if fee.get("fee_type") in ("Monthly", "Transport", "Hostel") and period == _month_key(admission):
            due = _first_month_amount(nom, admission)
        return due

    discount = int(fee.get("discount_applied") or 0)
    old_amount = int(fee.get("amount") or 0)
    old_due = int(fee.get("amount_due") or 0)
    old_expected = max(0, base_due_for(old_amount) - discount)
    paid_partial = max(0, old_expected - old_due)
    new_base = base_due_for(nominal)
    return max(0, new_base - discount - paid_partial)


async def _reconcile_unpaid_fee(
    fee: dict,
    nominal: int,
    person: dict,
    audit_buffer: List[dict],
) -> bool:
    if fee.get("status") == "paid":
        return False

    new_due = compute_amount_due(fee, nominal, person)
    old_amount = int(fee.get("amount") or 0)
    old_due = int(fee.get("amount_due") or 0)
    if old_amount == nominal and old_due == new_due:
        return False

    patch: Dict[str, Any] = {
        "amount": nominal,
        "amount_due": new_due,
        "fee_synced_at": now_utc().isoformat(),
    }
    admission_month = (person.get("date_of_admission") or "")[:7]
    if fee.get("fee_type") == "Monthly" and fee.get("period_month") == admission_month:
        patch["is_first_month"] = True
        patch["first_month_discounted"] = new_due < nominal

    await db.fees.update_one({"id": fee["id"]}, {"$set": patch})
    audit_buffer.append({
        "fee_id": fee["id"],
        "fee_type": fee.get("fee_type"),
        "period_month": fee.get("period_month"),
        "previous_amount": old_amount,
        "new_amount": nominal,
        "previous_amount_due": old_due,
        "new_amount_due": new_due,
        "action": "amount_updated",
    })
    return True


async def _remove_unpaid_transport(person_id: str, audit_buffer: List[dict]) -> int:
    removed = 0
    cursor = db.fees.find({
        "player_id": person_id,
        "fee_type": "Transport",
        "status": {"$ne": "paid"},
    })
    async for fee in cursor:
        await db.fees.delete_one({"id": fee["id"]})
        audit_buffer.append({
            "fee_id": fee["id"],
            "fee_type": "Transport",
            "period_month": fee.get("period_month"),
            "previous_amount": int(fee.get("amount") or 0),
            "new_amount": 0,
            "previous_amount_due": int(fee.get("amount_due") or 0),
            "new_amount_due": 0,
            "action": "removed_transport_disabled",
        })
        removed += 1
    return removed


async def _reconcile_from_schedule(
    person: dict,
    schedule_map: Dict[tuple, int],
    audit_buffer: List[dict],
) -> int:
    updated = 0
    unpaid = await db.fees.find({
        "player_id": person["id"],
        "status": {"$ne": "paid"},
    }).to_list(2000)
    schedule_keys = set(schedule_map.keys())

    for fee in unpaid:
        key = (fee.get("fee_type"), fee.get("period_month"))
        if key in schedule_map:
            if await _reconcile_unpaid_fee(fee, schedule_map[key], person, audit_buffer):
                updated += 1
        elif fee.get("fee_type") == "Transport" and key not in schedule_keys:
            await db.fees.delete_one({"id": fee["id"]})
            audit_buffer.append({
                "fee_id": fee["id"],
                "fee_type": "Transport",
                "period_month": fee.get("period_month"),
                "previous_amount": int(fee.get("amount") or 0),
                "new_amount": 0,
                "previous_amount_due": int(fee.get("amount_due") or 0),
                "new_amount_due": 0,
                "action": "removed_orphan_transport",
            })
            updated += 1
    return updated


def _alpha_person_with_boarding_tuition(person: dict) -> dict:
    """Boarding ALPHA players may store tuition in pws_fee_overrides."""
    pws_ov = person.get("pws_fee_overrides") or {}
    if not isinstance(pws_ov, dict):
        return person
    tuition = pws_ov.get("Tuition")
    if tuition and not person.get("monthly_fee_override"):
        return {**person, "monthly_fee_override": tuition}
    return person


async def _reconcile_recurring_types(
    person: dict,
    amounts: dict,
    audit_buffer: List[dict],
) -> int:
    updated = 0
    for fee_type, amt in (
        ("Monthly", amounts.get("monthly", 0)),
        ("Transport", amounts.get("transport", 0)),
        ("Hostel", amounts.get("hostel", 0)),
    ):
        if fee_type == "Transport" and amt <= 0:
            updated += await _remove_unpaid_transport(person["id"], audit_buffer)
            continue
        if amt <= 0:
            continue
        unpaid = await db.fees.find({
            "player_id": person["id"],
            "fee_type": fee_type,
            "status": {"$ne": "paid"},
        }).to_list(500)
        for fee in unpaid:
            if await _reconcile_unpaid_fee(fee, amt, person, audit_buffer):
                updated += 1
    return updated


async def _reconcile_pws_student(person: dict, audit_buffer: List[dict]) -> dict:
    from pws_fee_structure import build_pws_fee_schedule, pws_student_profile_from_person
    from routers.pws_fees import sync_pws_fees_for_student

    profile = pws_student_profile_from_person(person)
    dropped = await drop_unpaid_fees_before_admission(person, audit_buffer)
    schedule = build_pws_fee_schedule(
        profile["pws_class"],
        profile["date_of_admission"],
        profile["transport_enabled"],
        profile["transport_distance"],
        profile["overrides"],
    )
    schedule_map = {(item.fee_type, item.period_month): item.amount for item in schedule}
    updated = await _reconcile_from_schedule(person, schedule_map, audit_buffer)
    created = await sync_pws_fees_for_student(person)
    return {"updated": updated, "inserted": len(created), "removed_before_admission": dropped}


async def _reconcile_alpha_player(person: dict, audit_buffer: List[dict]) -> dict:
    from routers.fees import (
        _rates_for_person,
        _recurring_amounts_async,
        auto_create_fees_for_player,
        ensure_monthly_fees_up_to_current,
    )

    person = _alpha_person_with_boarding_tuition(person)
    rates = await _rates_for_person(person)
    if not rates:
        return {"skipped": True, "reason": "no_rates"}

    dropped = await drop_unpaid_fees_before_admission(person, audit_buffer)
    reg_amt = int(person.get("registration_fee_override") or 0) or int(rates.get("registration") or 0)
    reg_fee = await db.fees.find_one({
        "player_id": person["id"],
        "fee_type": "Registration",
        "status": {"$ne": "paid"},
    })
    updated = 0
    if reg_fee and await _reconcile_unpaid_fee(reg_fee, reg_amt, person, audit_buffer):
        updated += 1

    amounts = await _recurring_amounts_async(person)
    updated += await _reconcile_recurring_types(person, amounts, audit_buffer)

    await auto_create_fees_for_player(person)
    created = await ensure_monthly_fees_up_to_current(person["id"])
    return {"updated": updated, "inserted": len(created), "removed_before_admission": dropped}


async def _reconcile_legacy_pws_student(person: dict, audit_buffer: List[dict]) -> dict:
    from routers.fees import (
        _rates_for_person,
        _recurring_amounts_async,
        auto_create_fees_for_student,
        ensure_monthly_fees_up_to_current,
    )

    rates = await _rates_for_person(person)
    if not rates:
        return {"skipped": True, "reason": "no_rates"}

    dropped = await drop_unpaid_fees_before_admission(person, audit_buffer)

    reg_amt = int(person.get("registration_fee_override") or 0) or int(rates.get("registration") or 0)
    reg_fee = await db.fees.find_one({
        "player_id": person["id"],
        "fee_type": "Registration",
        "status": {"$ne": "paid"},
    })
    updated = 0
    if reg_fee and await _reconcile_unpaid_fee(reg_fee, reg_amt, person, audit_buffer):
        updated += 1

    amounts = await _recurring_amounts_async(person)
    updated += await _reconcile_recurring_types(person, amounts, audit_buffer)

    await auto_create_fees_for_student(person)
    created = await ensure_monthly_fees_up_to_current(person["id"])
    return {"updated": updated, "inserted": len(created), "removed_before_admission": dropped}


async def _write_audit_logs(person_id: str, user: dict, entries: List[dict]) -> None:
    if not entries:
        return
    ts = now_utc().isoformat()
    docs = [{
        "id": str(uuid.uuid4()),
        "person_id": person_id,
        "fee_id": entry.get("fee_id"),
        "fee_type": entry.get("fee_type"),
        "period_month": entry.get("period_month"),
        "previous_amount": entry.get("previous_amount"),
        "new_amount": entry.get("new_amount"),
        "previous_amount_due": entry.get("previous_amount_due"),
        "new_amount_due": entry.get("new_amount_due"),
        "action": entry.get("action", "amount_updated"),
        "updated_by_id": user.get("id"),
        "updated_by_name": user.get("name"),
        "at": ts,
    } for entry in entries]
    await db.fee_sync_audit.insert_many(docs)


async def sync_person_fees_to_financials(
    person: dict,
    user: dict,
    *,
    changed_keys: Optional[Set[str]] = None,
) -> dict:
    """Reconcile unpaid db.fees with the person's current fee profile."""
    if person.get("status") == "pending_fee_approval":
        return {"skipped": True, "reason": "pending_fee_approval"}

    kind = person.get("kind")
    if kind not in ("player", "student"):
        return {"skipped": True, "reason": "not_fee_bearing"}

    audit_buffer: List[dict] = []

    if kind == "student" and person.get("pws_class"):
        result = await _reconcile_pws_student(person, audit_buffer)
    elif kind == "player" and person.get("organization") == "ALPHA":
        result = await _reconcile_alpha_player(person, audit_buffer)
    elif kind == "student" and person.get("organization") == "PWS":
        result = await _reconcile_legacy_pws_student(person, audit_buffer)
    else:
        return {"skipped": True, "reason": "unsupported_profile"}

    await _write_audit_logs(person["id"], user, audit_buffer)
    result["audit_entries"] = len(audit_buffer)
    if changed_keys is not None:
        result["changed_keys"] = sorted(changed_keys)
    return result
