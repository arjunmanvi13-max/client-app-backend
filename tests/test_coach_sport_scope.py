"""Unit tests for strict single-sport coach scope."""
import pytest

from coach_scope import (
    ALLOWED_COACH_SPORT_NAMES,
    ERR_MULTI_SPORT,
    ERR_SPORT_ACCESS,
    assert_coach_sport_assigned,
    build_coach_player_query,
    coach_assignment_lists,
    coach_player_query_for_user,
    is_coach_user,
    normalize_coach_assignments,
    resolve_coach_data_scope,
    sport_record,
    validate_coach_sport_param,
)


def test_cricket_coach_filter_excludes_football():
    q = build_coach_player_query(["Balua"], ["Cricket"])
    assert q["sport"] == "Cricket"
    assert q["centre"] == {"$in": ["Balua"]}


def test_football_coach_filter():
    q = build_coach_player_query([], ["Football"])
    assert q["sport"] == "Football"
    assert "centre" not in q


def test_coach_without_sport_sees_nobody():
    q = build_coach_player_query(["Balua"], [])
    assert q["id"] == {"$in": []}


def test_centre_only_without_sport_sees_nobody():
    q = build_coach_player_query(["Balua"], [])
    assert q["id"] == {"$in": []}


def test_assignment_lists_fallback_to_legacy_assigned_sport():
    centres, sports = coach_assignment_lists({
        "assigned_centres": ["Balua"],
        "assigned_sport": "Cricket",
        "assigned_sports": [],
    })
    assert sports == ["Cricket"]
    assert centres == ["Balua"]


def test_normalize_single_sport_coach():
    doc = normalize_coach_assignments({
        "role": "coach",
        "assigned_sports": ["Football"],
        "assigned_sport": None,
    })
    assert doc["assigned_sports"] == ["Football"]
    assert doc["assigned_sport"] == "Football"
    assert doc["sport_assignment_status"] == "ok"


def test_normalize_rejects_multi_sport_coach():
    with pytest.raises(ValueError, match="exactly one sport"):
        normalize_coach_assignments({
            "role": "coach",
            "assigned_sports": ["Cricket", "Football"],
        })


def test_sport_record_by_name_and_code():
    assert sport_record("Cricket")["code"] == "cricket"
    assert sport_record("football")["name"] == "Football"


def test_resolve_coach_data_scope_cricket():
    scope = resolve_coach_data_scope({
        "role": "coach",
        "assigned_sports": ["Cricket"],
        "assigned_sport": "Cricket",
        "sport_assignment_status": "ok",
    })
    assert scope["is_coach"] is True
    assert scope["entity_id"] == "alpha"
    assert scope["assigned_sport"]["name"] == "Cricket"
    assert scope["sport_locked"] is True


def test_resolve_coach_data_scope_ambiguous():
    scope = resolve_coach_data_scope({
        "role": "coach",
        "assigned_sports": ["Cricket", "Football"],
        "sport_assignment_status": "ambiguous",
    })
    assert scope["requires_sport_assignment"] is True
    assert scope["assigned_sport"] is None


def test_validate_coach_sport_param_rejects_other_sport():
    user = {"role": "coach", "assigned_sports": ["Cricket"], "assigned_sport": "Cricket", "sport_assignment_status": "ok"}
    with pytest.raises(PermissionError, match=ERR_SPORT_ACCESS):
        validate_coach_sport_param(user, "Football", is_admin_fn=lambda u: False)


def test_validate_coach_sport_param_applies_assigned_when_absent():
    user = {"role": "coach", "assigned_sports": ["Football"], "assigned_sport": "Football", "sport_assignment_status": "ok"}
    assert validate_coach_sport_param(user, None, is_admin_fn=lambda u: False) == "Football"


def test_admin_bypasses_sport_validation():
    user = {"role": "admin"}
    assert validate_coach_sport_param(user, "Football", is_admin_fn=lambda u: u.get("role") == "admin") == "Football"


def test_assert_coach_sport_assigned_missing():
    with pytest.raises(ValueError):
        assert_coach_sport_assigned({"role": "coach", "assigned_sports": [], "sport_assignment_status": "required"})


def test_coach_player_query_single_sport():
    q = coach_player_query_for_user({
        "role": "coach",
        "assigned_sports": ["Cricket"],
        "assigned_centres": ["Balua"],
        "sport_assignment_status": "ok",
    })
    assert q["sport"] == "Cricket"


def test_coach_player_query_ambiguous_sees_nobody():
    q = coach_player_query_for_user({
        "role": "coach",
        "assigned_sports": ["Cricket", "Football"],
        "sport_assignment_status": "ambiguous",
    })
    assert q["id"] == {"$in": []}


def test_allowed_sport_names():
    assert set(ALLOWED_COACH_SPORT_NAMES) == {"Cricket", "Football"}


def test_is_coach_user_variants():
    assert is_coach_user({"role": "coach"})
    assert is_coach_user({"role": "alpha_coach"})
    assert is_coach_user({"user_type": "alpha_coach", "role": "coach"})
    assert not is_coach_user({"role": "admin"})
