from academic_class_roster import (
    academic_class_roster_query,
    is_pws_linked_player,
    is_pws_linked_player_type,
)


def test_linked_player_types():
    assert is_pws_linked_player_type("Boarding")
    assert is_pws_linked_player_type("Day Boarding")
    assert not is_pws_linked_player_type("Daily")
    assert not is_pws_linked_player_type("Hostel Only")
    assert not is_pws_linked_player_type("Hostel")
    assert is_pws_linked_player({"kind": "player", "player_type": "Day Boarding"})
    assert not is_pws_linked_player({"kind": "student", "player_type": "Boarding"})


def test_roster_query_single_section():
    q = academic_class_roster_query(section_id="sec-1")
    assert q["status"] == {"$ne": "deactivated"}
    student = next(c for c in q["$or"] if c.get("kind") == "student")
    assert student["section_id"] == "sec-1"
    player = next(c for c in q["$or"] if c.get("kind") == "player")
    assert player["section_id"] == "sec-1"
    assert "Boarding" in player["player_type"]["$in"]
    assert "Day Boarding" in player["player_type"]["$in"]
    assert "Daily" not in player["player_type"]["$in"]


def test_roster_query_assigned_sections():
    q = academic_class_roster_query(section_ids=["a", "b"])
    student = next(c for c in q["$or"] if c.get("kind") == "student")
    assert student["section_id"] == {"$in": ["a", "b"]}


def test_roster_query_matches_legacy_group_label():
    q = academic_class_roster_query(section_id="sec-1", section_labels=["9-A"])
    labels = [c["group"] for c in q["$or"] if c.get("kind") == "student" and "group" in c]
    assert "9-A" in labels


def test_empty_sections_match_nothing():
    q = academic_class_roster_query()
    assert q == {"id": {"$in": []}}
