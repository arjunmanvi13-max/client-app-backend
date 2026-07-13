"""Unit tests for PWS 2026-27 fee structure config."""
from pws_fee_structure import (
    PWS_CLASSES,
    annual_amount,
    build_pws_fee_schedule,
    exam_amount,
    pe_amount,
    resolve_category_amounts,
    security_amount,
    transport_amount,
    tuition_amount,
)


def test_tuition_by_class_band():
    assert tuition_amount("Nursery") == 1300
    assert tuition_amount("UKG") == 1300
    assert tuition_amount("Class I") == 1800
    assert tuition_amount("Class III") == 1800
    assert tuition_amount("Class IV") == 2000
    assert tuition_amount("Class VI") == 2000
    assert tuition_amount("Class VII") == 2300
    assert tuition_amount("Class IX") == 3000
    assert tuition_amount("Class X") == 3000


def test_security_and_annual_tiers():
    assert security_amount("Class III") == 2000
    assert security_amount("Class IV") == 3000
    assert annual_amount("Class IV") == 5000
    assert annual_amount("Class V") == 6000


def test_pe_and_exam_amounts():
    assert pe_amount("UKG") == 500
    assert pe_amount("Class II") == 750
    assert pe_amount("Class V") == 1000
    assert exam_amount("Class IV") == 1000
    assert exam_amount("Class V") == 1500


def test_transport_distances():
    assert transport_amount("Up to 5 km") == 2500
    assert transport_amount("Over 5 km") == 3000
    assert transport_amount(None) == 0


def test_resolve_category_amounts_without_transport():
    amounts = resolve_category_amounts("Class V", transport_enabled=False)
    assert amounts["Registration"] == 1000
    assert amounts["Admission Charges"] == 10000
    assert amounts["Tuition"] == 2000
    assert "Transport" not in amounts


def test_resolve_category_amounts_with_overrides():
    amounts = resolve_category_amounts(
        "Class V",
        transport_enabled=True,
        transport_distance="Up to 5 km",
        overrides={"Tuition": 1500},
    )
    assert amounts["Tuition"] == 1500
    assert amounts["Transport"] == 2500


def test_schedule_includes_one_time_and_recurring():
    schedule = build_pws_fee_schedule("Class V", "2026-04-01", True, "Up to 5 km")
    categories = {s.category for s in schedule}
    assert "Registration" in categories
    assert "Tuition" in categories
    assert "Transport" in categories
    assert "Physical Education" in categories
    assert "Exam Fee" in categories

    april = [s for s in schedule if s.period_month == "2026-04"]
    assert any(s.category == "Registration" for s in april)
    assert any(s.category == "Tuition" for s in april)
    assert any(s.category == "Physical Education" for s in april)

    september = [s for s in schedule if s.period_month == "2026-09"]
    assert any(s.category == "Exam Fee" for s in september)
    assert any(s.category == "Physical Education" for s in september)


def test_schedule_starts_from_admission_month():
    schedule = build_pws_fee_schedule("Class I", "2026-06-15", False)
    periods = {s.period_month for s in schedule if s.category == "Tuition"}
    assert "2026-04" not in periods
    assert "2026-06" in periods
    assert min(periods) == "2026-06"


def test_all_classes_have_tuition():
    for cls in PWS_CLASSES:
        assert tuition_amount(cls) > 0
        amounts = resolve_category_amounts(cls, transport_enabled=False)
        assert amounts["Tuition"] == tuition_amount(cls)
