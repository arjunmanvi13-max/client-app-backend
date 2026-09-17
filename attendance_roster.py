"""Role-scoped Take Attendance roster rules (PWS + ALPHA).

Teachers → assigned class/section (plus ALPHA Boarding / Day Boarding in that class).
Coaches → assigned sport / campus / training batch.
Wardens → Hostel + Boarding residents (ALPHA players and PWS boarding students).
Academic leadership → teachers, office staff, academic staff.
Super Admin / Admins → all categories with entity filters.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from academic_class_roster import canonical_player_type
from coach_scope import is_coach_user
from rbac.authorization import has_permission, normalize_role
from rbac.enums import Permission, UserRole

ROSTER_KINDS = ("student", "player", "staff", "teacher", "coach", "hostel")

HOSTEL_PLAYER_TYPES = ("Hostel", "Hostel Only", "Boarding")
PWS_HOSTEL_STUDENT_TYPES = ("Boarding",)

ROLE_TEACHER = "teacher"
ROLE_COACH = "coach"
ROLE_WARDEN = "warden"
ROLE_LEADERSHIP = "academic_leadership"
ROLE_ADMIN = "admin"
ROLE_NONE = "none"

ENTITY_PWS = "PWS"
ENTITY_ALPHA = "ALPHA"
ENTITY_BOTH = "BOTH"

KIND_ENTITY = {
    "student": ENTITY_PWS,
    "teacher": ENTITY_PWS,
    "player": ENTITY_ALPHA,
    "coach": ENTITY_ALPHA,
    "staff": ENTITY_BOTH,
    "hostel": ENTITY_BOTH,
}

COLUMNS_BY_KIND: dict[str, list[tuple[str, str]]] = {
    "student": [("class_name", "Class"), ("section_name", "Section"), ("group", "Batch")],
    "player": [("centre", "Campus"), ("sport", "Sport"), ("group", "Batch"), ("player_type", "Category")],
    "hostel": [("resident_type", "Type"), ("centre", "Facility"), ("organization", "Entity")],
    "staff": [("designation", "Designation"), ("department", "Department"), ("organization", "Entity")],
    "teacher": [("designation", "Designation"), ("department", "Department"), ("organization", "Entity")],
    "coach": [("coach_type", "Type"), ("sport", "Sport"), ("centre", "Campus")],
}

LEADERSHIP_DESIGNATIONS = frozenset({"PRINCIPAL", "VICE_PRINCIPAL", "ACADEMIC_HEAD"})


def _designation(user: dict) -> str:
    return (user.get("designation") or "").upper()


def _is_super_admin(user: dict) -> bool:
    return (user.get("role") or "").strip().lower() == "super_admin"


def _is_admin(user: dict) -> bool:
    return (user.get("role") or "") in ("admin", "super_admin")


def _is_pws_admin(user: dict) -> bool:
    return (user.get("role") or "").strip().lower() in ("pws_admin", "principal", "vice_principal")


def _is_alpha_admin(user: dict) -> bool:
    return (user.get("role") or "").strip().lower() in ("admin", "alpha_admin")


def _is_teacher(user: dict) -> bool:
    return (user.get("role") or "").strip().lower() in ("teacher", "pws_teacher")


def is_academic_leadership_user(user: dict) -> bool:
    if _is_super_admin(user):
        return False
    role = (user.get("role") or "").strip().lower()
    if role in ("principal", "vice_principal"):
        return True
    if _designation(user) in LEADERSHIP_DESIGNATIONS:
        return True
    return False


def is_warden_user(user: dict) -> bool:
    role = (user.get("role") or "").strip().lower()
    if role == "warden":
        return True
    return normalize_role(role) == UserRole.WARDEN


def attendance_role_view(user: dict) -> str:
    """Single primary view for Take Attendance scoping."""
    if _is_teacher(user):
        return ROLE_TEACHER
    if is_coach_user(user):
        return ROLE_COACH
    if is_warden_user(user):
        return ROLE_WARDEN
    if is_academic_leadership_user(user):
        return ROLE_LEADERSHIP
    if _is_super_admin(user) or _is_admin(user) or _is_pws_admin(user) or _is_alpha_admin(user):
        return ROLE_ADMIN
    if has_permission(user, Permission.MARK_HOSTEL_ATTENDANCE):
        return ROLE_WARDEN
    if has_permission(user, Permission.MARK_TEACHER_ATTENDANCE):
        return ROLE_LEADERSHIP
    return ROLE_NONE


def normalize_entity_filter(value: Optional[str]) -> str:
    raw = (value or ENTITY_BOTH).strip().upper()
    if raw in ("PWS", "SCHOOL"):
        return ENTITY_PWS
    if raw in ("ALPHA", "SPORTS"):
        return ENTITY_ALPHA
    return ENTITY_BOTH


def kind_matches_entity(kind: str, entity: str) -> bool:
    mapped = KIND_ENTITY.get(kind, ENTITY_BOTH)
    if entity == ENTITY_BOTH or mapped == ENTITY_BOTH:
        return True
    return mapped == entity


def allowed_roster_kinds(user: dict, *, entity: str = ENTITY_BOTH) -> list[str]:
    view = attendance_role_view(user)
    if view == ROLE_TEACHER:
        kinds = ["student"]
    elif view == ROLE_COACH:
        kinds = ["player"]
        if user.get("coach_type") == "head":
            kinds.extend(["staff", "coach"])
    elif view == ROLE_WARDEN:
        kinds = ["hostel"]
    elif view == ROLE_LEADERSHIP:
        kinds = ["teacher", "staff"]
        if has_permission(user, Permission.MARK_STUDENT_ATTENDANCE) or has_permission(
            user, Permission.MARK_PWS_ATTENDANCE
        ):
            kinds.append("student")
    elif view == ROLE_ADMIN:
        kinds = list(ROSTER_KINDS)
    else:
        kinds = []
        if has_permission(user, Permission.MARK_STUDENT_ATTENDANCE):
            kinds.append("student")
        if has_permission(user, Permission.MARK_PLAYER_ATTENDANCE):
            kinds.append("player")
        if has_permission(user, Permission.MARK_STAFF_ATTENDANCE):
            kinds.append("staff")
        if has_permission(user, Permission.MARK_TEACHER_ATTENDANCE):
            kinds.append("teacher")
        if has_permission(user, Permission.MARK_COACH_ATTENDANCE):
            kinds.append("coach")
        if has_permission(user, Permission.MARK_HOSTEL_ATTENDANCE):
            kinds.append("hostel")

    return [k for k in kinds if kind_matches_entity(k, entity)]


def default_roster_kind(user: dict, kinds: Optional[list[str]] = None) -> Optional[str]:
    options = kinds if kinds is not None else allowed_roster_kinds(user)
    if not options:
        return None
    view = attendance_role_view(user)
    preferred = {
        ROLE_TEACHER: "student",
        ROLE_COACH: "player",
        ROLE_WARDEN: "hostel",
        ROLE_LEADERSHIP: "teacher",
        ROLE_ADMIN: "student",
    }.get(view)
    if preferred and preferred in options:
        return preferred
    return options[0]


def hostel_resident_query(
    *,
    centres: Optional[list[str]] = None,
    organization: Optional[str] = None,
) -> dict:
    """Hostel + Boarding residents across ALPHA players and PWS boarding students."""
    clauses: list[dict[str, Any]] = [
        {"kind": "player", "player_type": {"$in": list(HOSTEL_PLAYER_TYPES)}},
        {"kind": "student", "pws_student_type": {"$in": list(PWS_HOSTEL_STUDENT_TYPES)}},
        {"kind": "student", "is_resident": True},
    ]
    q: dict[str, Any] = {"status": {"$ne": "deactivated"}, "$or": clauses}
    extras: list[dict[str, Any]] = []
    if centres:
        extras.append({
            "$or": [
                {"centre": {"$in": centres}},
                {"kind": "student"},
            ]
        })
    org = (organization or "").upper()
    if org == ENTITY_PWS:
        extras.append({
            "$or": [
                {"kind": "student"},
                {"organization": ENTITY_PWS},
            ]
        })
    elif org == ENTITY_ALPHA:
        extras.append({"kind": "player"})
    if extras:
        return {"$and": [q, *extras]}
    return q


def warden_centres(user: dict) -> list[str]:
    return [c for c in (user.get("assigned_centres") or []) if c]


def columns_for_kind(kind: str) -> list[dict[str, str]]:
    return [{"key": key, "label": label} for key, label in COLUMNS_BY_KIND.get(kind, [])]


def origin_tag(person: dict) -> Optional[str]:
    if person.get("kind") == "player":
        ptype = canonical_player_type(person.get("player_type"))
        if ptype in ("Boarding", "Day Boarding"):
            return f"ALPHA · {ptype}"
        if ptype in ("Hostel", "Hostel Only"):
            return f"ALPHA · {ptype}"
    if person.get("kind") == "student" and (
        person.get("pws_student_type") == "Boarding" or person.get("is_resident")
    ):
        return "PWS · Boarding"
    return None


def _field_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if v)
    return str(value).strip()


def meta_line_for(kind: str, person: dict) -> str:
    parts: list[str] = []
    for key, _label in COLUMNS_BY_KIND.get(kind, []):
        text = _field_text(person.get(key))
        if text:
            parts.append(text)
    return " · ".join(parts) if parts else "—"


def serialize_roster_person(kind: str, person: dict) -> dict:
    ptype = canonical_player_type(person.get("player_type")) or person.get("pws_student_type")
    row = {
        "id": person.get("id"),
        "name": person.get("name") or "—",
        "kind": person.get("kind") or kind,
        "organization": person.get("organization"),
        "class_name": person.get("class_name"),
        "section_name": person.get("section_name") or person.get("section"),
        "group": person.get("group"),
        "sport": person.get("sport") or _field_text(person.get("assigned_sports")),
        "player_type": person.get("player_type"),
        "centre": person.get("centre") or _field_text(person.get("assigned_centres")),
        "department": person.get("department"),
        "designation": person.get("designation"),
        "coach_type": person.get("coach_type"),
        "resident_type": ptype or ("Hostel" if kind == "hostel" else None),
        "origin_tag": origin_tag(person),
    }
    row["meta_line"] = meta_line_for(kind, {**person, **row})
    return row


def roster_summary(people: Iterable[dict], marks: dict[str, str]) -> dict[str, int]:
    total = 0
    present = absent = late = leave = unmarked = 0
    for person in people:
        total += 1
        status = marks.get(person["id"])
        if status == "present":
            present += 1
        elif status == "absent":
            absent += 1
        elif status == "late":
            late += 1
        elif status == "leave":
            leave += 1
        else:
            unmarked += 1
    return {
        "total": total,
        "present": present,
        "absent": absent,
        "late": late,
        "leave": leave,
        "unmarked": unmarked,
    }


def assert_kind_allowed(user: dict, kind: str, *, entity: str = ENTITY_BOTH) -> None:
    from fastapi import HTTPException

    allowed = allowed_roster_kinds(user, entity=entity)
    if kind not in allowed:
        raise HTTPException(403, f"Your role cannot view {kind} attendance")
