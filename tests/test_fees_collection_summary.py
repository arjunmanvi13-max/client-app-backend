"""Unit tests for fees collection summary helpers."""
from fees_collection_utils import compute_player_fee_status, expand_player_type_filter, normalize_person_date


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


def test_expand_player_type_filter_hostel_aliases():
    from fees_collection_utils import expand_player_type_filter

    assert expand_player_type_filter(None) is None
    assert expand_player_type_filter("") is None
    assert expand_player_type_filter("Daily,Day Boarding") == ["Daily", "Day Boarding"]
    assert expand_player_type_filter("Hostel Only") == ["Hostel", "Hostel Only"]


def test_directory_dates_normalize_to_iso_month():
    assert normalize_person_date("09/02/2025") == "2025-02-09"
    assert normalize_person_date("09/02/2025")[:7] == "2025-02"
    assert normalize_person_date("2025-02-09") == "2025-02-09"
    assert normalize_person_date("") == ""
    assert normalize_person_date("", fallback="2026-09-21") == "2026-09-21"
