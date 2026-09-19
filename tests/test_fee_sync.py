"""Tests for directory → financials fee sync."""
import os

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017/test")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("JWT_SECRET", "test-secret")

import uuid

import pytest

from core import db
from fee_sync import (
    SETTLED_ROW_GUARD,
    compute_amount_due,
    drop_unpaid_fees_before_admission,
    fee_related_keys_changed,
)


def test_fee_related_keys_changed_detects_override():
    target = {"monthly_fee_override": 2500, "transport_fee_monthly": 0}
    upd = {"monthly_fee_override": 3000}
    assert fee_related_keys_changed(upd, target) == {"monthly_fee_override"}


def test_fee_related_keys_changed_ignores_unchanged():
    target = {"transport_enabled": True, "transport_distance": "Up to 5 km"}
    upd = {"transport_enabled": True, "name": "Updated Name"}
    assert fee_related_keys_changed(upd, target) == set()


def test_fee_related_keys_changed_detects_skill_and_centre():
    target = {"centre": "Balua", "skill_level": "Beginner", "sport": "Cricket"}
    upd = {"centre": "Defense Colony", "skill_level": "Advanced"}
    assert fee_related_keys_changed(upd, target) == {"centre", "skill_level"}


def test_compute_amount_due_first_month_discount():
    person = {"date_of_admission": "2026-04-20"}
    fee = {"fee_type": "Monthly", "period_month": "2026-04", "amount": 2000, "amount_due": 1000}
    # Admission after 15th → half month
    assert compute_amount_due(fee, 2000, person) == 1000


def test_compute_amount_due_preserves_partial_payment():
    person = {"date_of_admission": "2026-04-01"}
    fee = {
        "fee_type": "Monthly",
        "period_month": "2026-05",
        "amount": 2000,
        "amount_due": 1200,
        "discount_applied": 0,
    }
    # ₹800 already paid toward ₹2000 → new ₹2500 rate leaves ₹1700 due
    assert compute_amount_due(fee, 2500, person) == 1700


def test_compute_amount_due_applies_discount():
    person = {"date_of_admission": "2026-04-01"}
    fee = {
        "fee_type": "Monthly",
        "period_month": "2026-05",
        "amount": 2000,
        "amount_due": 1500,
        "discount_applied": 500,
    }
    assert compute_amount_due(fee, 2000, person) == 1500


def test_settled_row_guard_protects_receipted_and_discounted_rows():
    assert SETTLED_ROW_GUARD["discount_applied"] == {"$not": {"$gt": 0}}
    assert SETTLED_ROW_GUARD["receipt_number"] == {"$in": [None, ""]}
    assert SETTLED_ROW_GUARD["batch_id"] == {"$in": [None, ""]}


@pytest.mark.asyncio
async def test_drop_before_admission_never_deletes_a_row_that_just_got_paid():
    """The delete must re-assert status, or a concurrent collection loses its receipt."""
    person = {"id": str(uuid.uuid4()), "date_of_admission": "2026-06-10"}
    stale = {
        "id": str(uuid.uuid4()),
        "player_id": person["id"],
        "fee_type": "Monthly",
        "period_month": "2026-04",
        "status": "unpaid",
        "amount": 3000,
        "amount_due": 3000,
    }
    await db.fees.insert_one(dict(stale))
    try:
        await db.fees.update_one({"id": stale["id"]}, {"$set": {"status": "paid"}})
        entries = []
        await drop_unpaid_fees_before_admission(person, entries)
        survivor = await db.fees.find_one({"id": stale["id"]}, {"_id": 0, "status": 1})
        assert survivor is not None, "a paid fee row was deleted by the pre-admission sweep"
        assert entries == [], "a row that was not deleted must not be reported as deleted"
    finally:
        await db.fees.delete_many({"player_id": person["id"]})


@pytest.mark.asyncio
async def test_drop_before_admission_keeps_rows_carrying_a_concession():
    person = {"id": str(uuid.uuid4()), "date_of_admission": "2026-06-10"}
    discounted = {
        "id": str(uuid.uuid4()),
        "player_id": person["id"],
        "fee_type": "Monthly",
        "period_month": "2026-04",
        "status": "unpaid",
        "amount": 3000,
        "amount_due": 2000,
        "discount_applied": 1000,
    }
    await db.fees.insert_one(dict(discounted))
    try:
        await drop_unpaid_fees_before_admission(person, [])
        kept = await db.fees.find_one({"id": discounted["id"]}, {"_id": 0, "id": 1})
        assert kept is not None, "an approved concession was destroyed with no audit trail"
    finally:
        await db.fees.delete_many({"player_id": person["id"]})


@pytest.mark.asyncio
async def test_drop_before_admission_removes_plain_stale_rows():
    person = {"id": str(uuid.uuid4()), "date_of_admission": "2026-06-10"}
    stale = {
        "id": str(uuid.uuid4()),
        "player_id": person["id"],
        "fee_type": "Monthly",
        "period_month": "2026-04",
        "status": "unpaid",
        "amount": 3000,
        "amount_due": 3000,
    }
    current = {**stale, "id": str(uuid.uuid4()), "period_month": "2026-06"}
    await db.fees.insert_many([dict(stale), dict(current)])
    try:
        entries = []
        removed = await drop_unpaid_fees_before_admission(person, entries)
        assert removed == 1
        assert await db.fees.find_one({"id": stale["id"]}) is None
        assert await db.fees.find_one({"id": current["id"]}) is not None
        assert [e["action"] for e in entries] == ["removed_before_admission"]
    finally:
        await db.fees.delete_many({"player_id": person["id"]})

