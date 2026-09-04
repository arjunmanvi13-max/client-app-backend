"""Tests for directory → financials fee sync."""
import os

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017/test")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("JWT_SECRET", "test-secret")

from fee_sync import compute_amount_due, fee_related_keys_changed, unpaid_fee_is_before_admission


def test_fee_related_keys_changed_detects_override():
    target = {"monthly_fee_override": 2500, "transport_fee_monthly": 0}
    upd = {"monthly_fee_override": 3000}
    assert fee_related_keys_changed(upd, target) == {"monthly_fee_override"}


def test_fee_related_keys_changed_ignores_unchanged():
    target = {"transport_enabled": True, "transport_distance": "Up to 5 km"}
    upd = {"transport_enabled": True, "name": "Updated Name"}
    assert fee_related_keys_changed(upd, target) == set()


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


def test_unpaid_fee_is_before_admission_month():
    person_adm = "2026-06-10"
    assert unpaid_fee_is_before_admission(
        {"fee_type": "Monthly", "period_month": "2015-06"}, person_adm
    )
    assert unpaid_fee_is_before_admission(
        {"fee_type": "Registration", "period_month": "2015-06", "due_date": "2015-06-05"},
        person_adm,
    )
    assert not unpaid_fee_is_before_admission(
        {"fee_type": "Monthly", "period_month": "2026-06"}, person_adm
    )
    assert not unpaid_fee_is_before_admission(
        {"fee_type": "Monthly", "period_month": "2026-07"}, person_adm
    )

