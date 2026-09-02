"""ALPHA campus catalogue — Defense Colony is Daily-only with Cricket and Football."""
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
