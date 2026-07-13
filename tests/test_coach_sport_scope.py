"""Unit tests for coach sport-scoped player visibility."""
from coach_scope import (
    build_coach_player_query,
    coach_assignment_lists,
    normalize_coach_assignments,
)


def test_cricket_coach_filter_excludes_football():
    q = build_coach_player_query(["Balua"], ["Cricket"])
    assert q["sport"] == {"$in": ["Cricket"]}
    assert q["centre"] == {"$in": ["Balua"]}


def test_football_coach_filter():
    q = build_coach_player_query([], ["Football"])
    assert q["sport"] == {"$in": ["Football"]}
    assert "centre" not in q


def test_coach_without_sport_sees_nobody():
    q = build_coach_player_query(["Balua"], [])
    assert q["id"] == {"$in": []}


def test_centre_only_without_sport_sees_nobody():
    """Centre assignment alone must not expose all sports at that centre."""
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


def test_normalize_coach_assignments_from_sports_array():
    doc = normalize_coach_assignments({
        "role": "coach",
        "assigned_sports": ["Football"],
        "assigned_sport": None,
    })
    assert doc["assigned_sports"] == ["Football"]
    assert doc["assigned_sport"] == "Football"


def test_normalize_coach_assignments_from_legacy_single_sport():
    doc = normalize_coach_assignments({
        "role": "coach",
        "assigned_sport": "Cricket",
        "assigned_sports": [],
    })
    assert doc["assigned_sports"] == ["Cricket"]
    assert doc["assigned_sport"] == "Cricket"
