"""Tests for custom fee override approval workflow."""
import pytest

from fee_override_approval import (
    analyze_fee_overrides,
    can_bypass_fee_approval,
    extract_override_fields,
    strip_fee_overrides_from_person,
)


@pytest.mark.asyncio
async def test_player_transport_triggers_custom_fee():
    person = {
        "kind": "player",
        "player_type": "Daily",
        "sport": "Cricket",
        "centre": "Balua",
        "transport_fee_monthly": 1500,
    }
    differs, defaults, custom = await analyze_fee_overrides(person)
    assert differs is True
    assert defaults.get("transport") == 0
    assert custom.get("transport") == 1500


@pytest.mark.asyncio
async def test_player_default_fees_no_override():
    person = {
        "kind": "player",
        "player_type": "Daily",
        "sport": "Cricket",
        "centre": "Balua",
        "transport_fee_monthly": 0,
    }
    differs, defaults, custom = await analyze_fee_overrides(person)
    assert differs is False
    assert custom == {}
    assert defaults.get("registration") == 3000
    assert defaults.get("monthly") == 2500


@pytest.mark.asyncio
async def test_student_pws_override_detected():
    person = {
        "kind": "student",
        "pws_class": "Class I",
        "pws_student_type": "Day School",
        "transport_enabled": False,
        "pws_fee_overrides": {"Tuition": 900},
    }
    differs, defaults, custom = await analyze_fee_overrides(person)
    assert differs is True
    assert defaults.get("Tuition") == 1300
    assert custom.get("Tuition") == 900


def test_can_bypass_for_super_admin():
    assert can_bypass_fee_approval({"role": "super_admin"}) is True


def test_cannot_bypass_for_admin():
    assert can_bypass_fee_approval({"role": "admin", "permissions": {}}) is False


def test_extract_override_fields_player():
    person = {
        "registration_fee_override": 2500,
        "transport_fee_monthly": 500,
        "monthly_fee_override": None,
    }
    fields = extract_override_fields(person)
    assert fields["registration_fee_override"] == 2500
    assert fields["transport_fee_monthly"] == 500
    assert "monthly_fee_override" not in fields


def test_strip_fee_overrides():
    person = {
        "name": "Test",
        "registration_fee_override": 2500,
        "transport_fee_monthly": 500,
        "hostel_fee_override": 10000,
    }
    clean = strip_fee_overrides_from_person(person)
    assert clean["name"] == "Test"
    assert clean["transport_fee_monthly"] == 0
    assert "registration_fee_override" not in clean
