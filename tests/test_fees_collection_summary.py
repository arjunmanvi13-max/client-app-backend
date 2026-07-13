"""Unit tests for fees collection summary helpers."""
from fees_collection_utils import compute_player_fee_status


def test_compute_status_paid_no_unpaid():
    snap = compute_player_fee_status([], [{"period_month": "2026-03", "amount_due": 2500}], "2026-04-15", "2026-04")
    assert snap["fee_status"] == "paid"
    assert snap["amount_due"] == 0


def test_compute_status_overdue():
    unpaid = [{"period_month": "2026-03", "amount_due": 2500, "due_date": "2026-03-05"}]
    snap = compute_player_fee_status(unpaid, [], "2026-04-15", "2026-04")
    assert snap["fee_status"] == "overdue"
    assert snap["overdue_days"] >= 1
    assert snap["amount_due"] == 2500


def test_compute_status_due_current_month():
    unpaid = [{"period_month": "2026-04", "amount_due": 2500, "due_date": "2026-04-20"}]
    snap = compute_player_fee_status(unpaid, [], "2026-04-15", "2026-04")
    assert snap["fee_status"] == "due"
    assert snap["has_current_month_due"] is True


def test_compute_status_paid_ahead():
    paid = [{"period_month": "2026-05", "amount_due": 2500}]
    snap = compute_player_fee_status([], paid, "2026-04-15", "2026-04")
    assert snap["fee_status"] == "paid_ahead"
