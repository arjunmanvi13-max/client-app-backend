from attendance_roster import (
    allowed_roster_kinds,
    attendance_role_view,
    default_roster_kind,
    hostel_resident_query,
    kind_matches_entity,
    meta_line_for,
    origin_tag,
    roster_summary,
    serialize_roster_person,
)


def test_teacher_view_is_students_only():
    user = {"role": "teacher", "id": "t1"}
    assert attendance_role_view(user) == "teacher"
    assert allowed_roster_kinds(user) == ["student"]
    assert default_roster_kind(user) == "student"
    pws = {"role": "pws_teacher", "id": "t2"}
    assert attendance_role_view(pws) == "teacher"
    assert allowed_roster_kinds(pws) == ["student"]


def test_coach_view_is_players_head_coach_extra_tabs():
    asst = {"role": "coach", "coach_type": "assistant", "assigned_sports": ["Cricket"]}
    assert attendance_role_view(asst) == "coach"
    assert allowed_roster_kinds(asst) == ["player"]
    head = {"role": "coach", "coach_type": "head", "assigned_sports": ["Football"]}
    assert allowed_roster_kinds(head) == ["player", "staff", "coach"]


def test_warden_view_is_hostel_only():
    user = {"role": "warden"}
    assert attendance_role_view(user) == "warden"
    assert allowed_roster_kinds(user) == ["hostel"]
    assert default_roster_kind(user) == "hostel"


def test_leadership_defaults_to_teachers():
    principal = {"role": "principal", "designation": "PRINCIPAL"}
    assert attendance_role_view(principal) == "academic_leadership"
    kinds = allowed_roster_kinds(principal)
    assert kinds[0] == "teacher"
    assert "staff" in kinds
    assert default_roster_kind(principal) == "teacher"
    vp = {"role": "vice_principal", "designation": "VICE_PRINCIPAL"}
    assert attendance_role_view(vp) == "academic_leadership"
    head = {"role": "pws_admin", "designation": "ACADEMIC_HEAD"}
    assert attendance_role_view(head) == "academic_leadership"


def test_super_admin_all_kinds_and_entity_filter():
    admin = {"role": "super_admin"}
    assert attendance_role_view(admin) == "admin"
    kinds = allowed_roster_kinds(admin)
    for k in ("student", "player", "staff", "teacher", "coach", "hostel"):
        assert k in kinds
    pws_only = allowed_roster_kinds(admin, entity="PWS")
    assert "student" in pws_only and "teacher" in pws_only
    assert "player" not in pws_only and "coach" not in pws_only
    alpha_only = allowed_roster_kinds(admin, entity="ALPHA")
    assert "player" in alpha_only and "coach" in alpha_only
    assert "student" not in alpha_only
    assert kind_matches_entity("staff", "PWS")
    assert kind_matches_entity("hostel", "ALPHA")


def test_hostel_query_includes_boarding_and_hostel_not_daily():
    q = hostel_resident_query()
    player = next(c for c in q["$or"] if c.get("kind") == "player")
    types = player["player_type"]["$in"]
    assert "Boarding" in types
    assert "Hostel Only" in types
    assert "Hostel" in types
    assert "Daily" not in types
    assert "Day Boarding" not in types
    assert any(c.get("pws_student_type") for c in q["$or"] if c.get("kind") == "student")


def test_hostel_query_alpha_entity_is_players_only():
    q = hostel_resident_query(organization="ALPHA")
    assert q["$and"][-1] == {"kind": "player"}


def test_serialize_student_and_player_meta():
    student = serialize_roster_person(
        "student",
        {"id": "s1", "name": "Ada", "kind": "student", "class_name": "Std 9", "section_name": "A", "group": "9-A"},
    )
    assert "Std 9" in student["meta_line"]
    player = serialize_roster_person(
        "player",
        {
            "id": "p1",
            "name": "Rohit",
            "kind": "player",
            "centre": "Balua",
            "sport": "Cricket",
            "group": "U16",
            "player_type": "Boarding",
        },
    )
    assert player["origin_tag"] == "ALPHA · Boarding"
    assert "Balua" in player["meta_line"]
    assert origin_tag({"kind": "player", "player_type": "Daily"}) is None


def test_roster_summary_unmarked():
    people = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    summary = roster_summary(people, {"a": "present", "b": "absent"})
    assert summary["present"] == 1
    assert summary["absent"] == 1
    assert summary["unmarked"] == 1
    assert summary["total"] == 3
    assert meta_line_for("staff", {"designation": "Clerk", "department": "Office", "organization": "PWS"}) == "Clerk · Office · PWS"
