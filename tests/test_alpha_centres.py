"""ALPHA campus catalogue — Defense Colony is Daily-only with Cricket and Football."""
from alpha_centre_rules import (
    apply_defense_colony_rates,
    defense_colony_advanced_slot_error,
    DEFENSE_COLONY_ADVANCED_SLOT_ERROR,
)
from bulk_ingest import CENTRES, DAILY_ONLY_CENTRES, PLAYER_SPEC, SPORTS, _cross_field_errors


def test_defense_colony_is_listed_with_both_sports():
    assert "Defense Colony" in CENTRES
    assert SPORTS == ("Cricket", "Football")


def test_defense_colony_allows_daily_only():
    assert "Defense Colony" in DAILY_ONLY_CENTRES
    errs = _cross_field_errors(PLAYER_SPEC, {"centre": "Defense Colony", "player_type": "Hostel"})
    assert any("Defense Colony" in e and "Daily" in e for e in errs)
    assert _cross_field_errors(PLAYER_SPEC, {"centre": "Defense Colony", "player_type": "Daily"}) == []
    assert _cross_field_errors(PLAYER_SPEC, {"centre": "Balua", "player_type": "Hostel"}) == []


def test_defense_colony_fee_overlay_by_skill():
    generic = {"registration": 3000, "monthly": 2500}
    assert apply_defense_colony_rates(generic, "Defense Colony", "Beginner") == {
        "registration": 7500, "monthly": 3500,
    }
    assert apply_defense_colony_rates(generic, "Defense Colony", "Intermediate") == {
        "registration": 7500, "monthly": 3500,
    }
    assert apply_defense_colony_rates(generic, "Defense Colony", "Advanced") == {
        "registration": 7500, "monthly": 8000,
    }
    assert apply_defense_colony_rates(generic, "Balua", "Advanced") == generic
    assert apply_defense_colony_rates(generic, "Harding Park", "Beginner") == generic


def test_defense_colony_advanced_morning_only():
    assert defense_colony_advanced_slot_error("Defense Colony", "Advanced", "Evening") == (
        DEFENSE_COLONY_ADVANCED_SLOT_ERROR
    )
    assert defense_colony_advanced_slot_error("Defense Colony", "Advanced", "Both")
    assert defense_colony_advanced_slot_error("Defense Colony", "Advanced", "Morning") is None
    assert defense_colony_advanced_slot_error("Defense Colony", "Beginner", "Evening") is None
    assert defense_colony_advanced_slot_error("Harding Park", "Advanced", "Evening") is None
    errs = _cross_field_errors(PLAYER_SPEC, {
        "centre": "Defense Colony",
        "player_type": "Daily",
        "skill_level": "Advanced",
        "slot": "Evening",
    })
    assert any("Morning" in e for e in errs)
    assert _cross_field_errors(PLAYER_SPEC, {
        "centre": "Defense Colony",
        "player_type": "Daily",
        "skill_level": "Intermediate",
        "slot": "Evening",
    }) == []
