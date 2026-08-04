"""Time Table permission checks with Academic Head designation support."""
from __future__ import annotations

from fastapi import HTTPException

from core import is_super_admin
from rbac.authorization import has_permission, normalize_role
from rbac.enums import Permission, UserRole

TIMETABLE_LEGACY_KEYS = {
    "view_all": "timetable_view_all",
    "view_own": "timetable_view_own",
    "create": "timetable_create",
    "edit": "timetable_edit",
    "delete": "timetable_delete",
    "substitute": "timetable_substitute",
    "publish": "timetable_publish",
    "export": "timetable_export",
}


def _designation(user: dict) -> str:
    return (user.get("designation") or "").upper()


def is_academic_head(user: dict) -> bool:
    if is_super_admin(user):
        return True
    return _designation(user) == "ACADEMIC_HEAD"


def is_principal_or_vp(user: dict) -> bool:
    role = normalize_role(user.get("role", ""))
    d = _designation(user)
    return role == UserRole.PWS_ADMIN or d in ("PRINCIPAL", "VICE_PRINCIPAL") or user.get("role") in ("principal", "vice_principal")


def is_pws_teacher(user: dict) -> bool:
    role = normalize_role(user.get("role", ""))
    return role == UserRole.PWS_TEACHER or user.get("role") == "teacher"


def _legacy_perm(user: dict, key: str) -> bool:
    return bool((user.get("permissions") or {}).get(TIMETABLE_LEGACY_KEYS[key]))


def can_timetable_view_all(user: dict) -> bool:
    if is_super_admin(user):
        return True
    if has_permission(user, Permission.TIMETABLE_VIEW_ALL):
        return True
    if _legacy_perm(user, "view_all"):
        return True
    return is_academic_head(user) or is_principal_or_vp(user)


def can_timetable_view_own(user: dict) -> bool:
    if can_timetable_view_all(user):
        return True
    if has_permission(user, Permission.TIMETABLE_VIEW_OWN):
        return True
    if _legacy_perm(user, "view_own"):
        return True
    return is_pws_teacher(user)


def can_timetable_create(user: dict) -> bool:
    if is_super_admin(user):
        return True
    if has_permission(user, Permission.TIMETABLE_CREATE):
        return True
    return _legacy_perm(user, "create") or is_academic_head(user)


def can_timetable_edit(user: dict) -> bool:
    if is_super_admin(user):
        return True
    if has_permission(user, Permission.TIMETABLE_EDIT):
        return True
    return _legacy_perm(user, "edit") or is_academic_head(user)


def can_timetable_delete(user: dict) -> bool:
    if is_super_admin(user):
        return True
    if has_permission(user, Permission.TIMETABLE_DELETE):
        return True
    return _legacy_perm(user, "delete") or is_academic_head(user)


def can_timetable_substitute(user: dict) -> bool:
    if is_super_admin(user):
        return True
    if has_permission(user, Permission.TIMETABLE_SUBSTITUTE):
        return True
    return _legacy_perm(user, "substitute") or is_academic_head(user)


def can_timetable_publish(user: dict) -> bool:
    if is_super_admin(user):
        return True
    if has_permission(user, Permission.TIMETABLE_PUBLISH):
        return True
    return _legacy_perm(user, "publish") or is_academic_head(user)


def can_timetable_export(user: dict, *, own_only: bool = False) -> bool:
    if is_super_admin(user):
        return True
    if own_only and is_pws_teacher(user):
        return can_timetable_view_own(user)
    if has_permission(user, Permission.TIMETABLE_EXPORT):
        return True
    if _legacy_perm(user, "export"):
        return True
    return is_academic_head(user) or is_principal_or_vp(user)


def assert_pws_timetable_access(user: dict) -> None:
    """Reject ALPHA-only and non-PWS roles entirely."""
    from core import user_entity_scope
    from rbac.enums import BusinessEntity

    scope = user_entity_scope(user)
    if scope == BusinessEntity.ALPHA.value:
        raise HTTPException(403, "Time Table is not available for ALPHA users")
    role = normalize_role(user.get("role", ""))
    if role in (UserRole.ALPHA_COACH, UserRole.ALPHA_ADMIN, UserRole.ALPHA_ACCOUNTS):
        raise HTTPException(403, "Time Table is PWS-only")
    if user.get("role") in ("coach", "parent", "student", "player", "warden"):
        raise HTTPException(403, "Time Table access denied")
    if not (can_timetable_view_all(user) or can_timetable_view_own(user)):
        raise HTTPException(403, "Time Table access denied")


def assert_timetable_manage(user: dict) -> None:
    assert_pws_timetable_access(user)
    if not can_timetable_create(user):
        raise HTTPException(403, "Time Table edit permission required")
