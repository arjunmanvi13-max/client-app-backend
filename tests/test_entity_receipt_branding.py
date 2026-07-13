"""Tests for entity-specific receipt branding."""
from pathlib import Path

import pytest

from entity_receipt_branding import (
    ENTITY_RECEIPT_BRANDING,
    branding_for_receipt_response,
    entity_id_from_fee_batch,
    entity_id_from_person,
    fee_head_label,
    format_receipt_number_display,
    infer_entity_id_from_fee,
    resolve_entity_branding,
)


def test_pws_branding_config():
    branding = resolve_entity_branding("pws")
    assert branding["display_name"] == "Prarambhika World School"
    assert branding["address_lines"] == ["Balua Ahmedpur", "Patna 801113"]
    assert branding["entity_code"] == "PWS"
    assert branding["logo_filename"] == "prarambhika-world-school-logo.png"
    assert Path(branding["logo_path"]).name == "prarambhika-world-school-logo.png"


def test_alpha_branding_config():
    branding = resolve_entity_branding("alpha")
    assert branding["display_name"] == "ALPHA Sports Academy"
    assert branding["address_lines"] == []
    assert branding["entity_code"] == "ALPHA"
    assert branding["logo_filename"] == "alpha-sports-logo.png"
    assert "alpha-sports-logo" in branding["logo_path"]


def test_branding_response_payload():
    payload = branding_for_receipt_response("pws")
    assert payload["entityCode"] == "PWS"
    assert payload["displayName"] == "Prarambhika World School"
    assert payload["addressLines"] == ["Balua Ahmedpur", "Patna 801113"]
    assert payload["receiptPrefix"] == "PWS"


def test_entity_from_legacy_pws_fee_without_entity_id():
    """Legacy PWS rows missing entity_id must not default to ALPHA."""
    fees = [{"fee_type": "Monthly", "student_id": "stu-1", "category": "Day Scholar"}]
    player = {"kind": "student", "organization": "PWS", "id": "stu-1"}
    assert entity_id_from_fee_batch(fees, player) == "pws"


def test_entity_from_legacy_fee_inferred_from_player():
    fees = [{"fee_type": "Monthly", "amount_due": 5000}]
    player = {"kind": "student", "organization": "PWS"}
    assert entity_id_from_fee_batch(fees, player) == "pws"


def test_entity_from_person_student_is_pws():
    assert entity_id_from_person({"kind": "student", "organization": "PWS"}) == "pws"


def test_entity_from_person_player_is_alpha():
    assert entity_id_from_person({"kind": "player", "organization": "ALPHA"}) == "alpha"


def test_infer_entity_id_from_fee_student_id():
    assert infer_entity_id_from_fee({"student_id": "x"}) == "pws"


def test_mismatch_fee_and_player_raises():
    fees = [{"entity_id": "alpha", "fee_type": "Monthly"}]
    player = {"kind": "student", "organization": "PWS"}
    with pytest.raises(ValueError, match="does not match"):
        entity_id_from_fee_batch(fees, player)


def test_entity_from_fee_batch_alpha():
    fees = [{"entity_id": "alpha"}, {"entity_id": "alpha"}]
    assert entity_id_from_fee_batch(fees) == "alpha"


def test_entity_from_fee_batch_pws():
    fees = [{"entity_id": "pws"}]
    assert entity_id_from_fee_batch(fees) == "pws"


def test_entity_from_fee_batch_rejects_mixed():
    fees = [{"entity_id": "pws"}, {"entity_id": "alpha"}]
    with pytest.raises(ValueError, match="multiple entities"):
        entity_id_from_fee_batch(fees)


def test_receipt_number_format_display():
    assert format_receipt_number_display("PWS-2026-000123", "pws") == "PWS-2026-000123"
    assert format_receipt_number_display("RCP-ALPHA-2026-000045", "alpha") == "ALPHA-2026-000045"


def test_fee_head_labels_by_entity():
    assert fee_head_label("Monthly", "pws") == "Monthly Tuition Fee"
    assert fee_head_label("Monthly", "alpha") == "Monthly Coaching Fee"
    assert fee_head_label("Registration", "alpha") == "Registration Fee"


def test_no_cross_entity_logo_filenames():
    pws = ENTITY_RECEIPT_BRANDING["pws"]["logo_filename"]
    alpha = ENTITY_RECEIPT_BRANDING["alpha"]["logo_filename"]
    assert "prarambhika" in pws.lower()
    assert "alpha" in alpha.lower()
    assert pws != alpha


def test_pdf_render_includes_pws_branding():
    pytest.importorskip("reportlab")
    from fee_receipt_pdf import render_batch_receipt_pdf

    branding = resolve_entity_branding("pws")
    pdf = render_batch_receipt_pdf(
        "batch-test",
        [{"fee_type": "Monthly", "period_month": "2026-01", "amount_due": 5000, "payment_mode": "Cash"}],
        {"name": "Test Student"},
        branding=branding,
        receipt_number="PWS-2026-000001",
        person_label="Student",
        person_lines=["Class I"],
        format_month=lambda m: m or "-",
        format_date=lambda d: d or "-",
        format_datetime=lambda d: d or "-",
    )
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 500


def test_pdf_render_includes_alpha_branding():
    pytest.importorskip("reportlab")
    from fee_receipt_pdf import render_batch_receipt_pdf

    branding = resolve_entity_branding("alpha")
    pdf = render_batch_receipt_pdf(
        "batch-test",
        [{"fee_type": "Monthly", "period_month": "2026-01", "amount_due": 2500, "payment_mode": "Cash"}],
        {"name": "Test Player"},
        branding=branding,
        receipt_number="ALPHA-2026-000001",
        person_label="Player",
        person_lines=["Balua · Football"],
        format_month=lambda m: m or "-",
        format_date=lambda d: d or "-",
        format_datetime=lambda d: d or "-",
    )
    assert pdf.startswith(b"%PDF")
    assert b"Prarambhika" not in pdf
