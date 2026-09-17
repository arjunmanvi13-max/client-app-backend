from academic_class_roster import (
    academic_class_roster_query,
    group_aliases_for_section,
    is_pws_linked_player,
    is_pws_linked_player_type,
)


def _student_groups(query: dict) -> list[str]:
    groups: list[str] = []
    for clause in query.get("$or") or []:
        if clause.get("kind") != "student" or "group" not in clause:
            continue
        value = clause["group"]
        if isinstance(value, dict):
            groups.extend(value.get("$in") or [])
        else:
            groups.append(value)
    return groups


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
    groups = _student_groups(q)
    assert "9-A" in groups
    assert "Std 9-A" in groups
    assert "Class IX-A" in groups


def test_roster_query_matches_pws_class_and_section_letter():
    q = academic_class_roster_query(section_docs=[{
        "id": "sec-3a",
        "label": "3-A",
        "grade_name": "3",
        "name": "A",
    }])
    assert any(
        c.get("kind") == "student" and c.get("pws_class") == "Class III" and c.get("section_name") == "A"
        for c in q["$or"]
    )
    groups = _student_groups(q)
    assert "3-A" in groups
    assert "Std 3-A" in groups
    aliases = group_aliases_for_section("3-A", "3", "A")
    assert "Class III-A" in aliases


def test_empty_sections_match_nothing():
    q = academic_class_roster_query()
    assert q == {"id": {"$in": []}}
