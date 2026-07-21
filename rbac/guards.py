"""Standardized permission guards for FastAPI routers."""
from typing import Optional

from fastapi import HTTPException

from rbac.authorization import assert_permission, has_permission, normalize_role
from rbac.enums import BusinessEntity, Permission, UserRole


def _entity(org: Optional[str]) -> Optional[BusinessEntity]:
    if not org:
        return None
    try:
        return BusinessEntity(org.upper())
    except ValueError:
        return None


def _user_entity(user: dict) -> BusinessEntity:
    return _entity(user.get("organization")) or BusinessEntity.PWS


# --- Super Admin / access ---

def can_manage_access(user: dict) -> bool:
    return has_permission(user, Permission.MANAGE_ACCESS)


def assert_manage_access(user: dict) -> None:
    assert_permission(user, Permission.MANAGE_ACCESS)


def can_create_users(user: dict) -> bool:
    return has_permission(user, Permission.CREATE_USERS)


def can_add_new_teacher(user: dict) -> bool:
    return has_permission(user, Permission.ADD_NEW_TEACHER, entity=BusinessEntity.PWS)


def assert_can_create_login_user(actor: dict, user_type: str) -> None:
    if can_create_users(actor):
        return
    if user_type == UserRole.PWS_TEACHER.value and can_add_new_teacher(actor):
        return
    raise HTTPException(403, "Only Super Admin can create login user accounts")


def assert_can_list_login_users(actor: dict, user_type: Optional[str] = None) -> None:
    if can_create_users(actor):
        return
    if user_type == UserRole.PWS_TEACHER.value and can_add_new_teacher(actor):
        return
    raise HTTPException(403, "Super Admin only")


def can_bulk_upload(user: dict) -> bool:
    return has_permission(user, Permission.BULK_UPLOAD_USERS)


def can_toggle_user_status(user: dict) -> bool:
    return has_permission(user, Permission.TOGGLE_USER_STATUS)


# --- PWS ---

def can_mark_pws_attendance(user: dict) -> bool:
    return has_permission(user, Permission.MARK_PWS_ATTENDANCE, entity=BusinessEntity.PWS)


def can_manage_academic(user: dict) -> bool:
    return (
        has_permission(user, Permission.MANAGE_TEACHERS_MAP_SUBJECTS, entity=BusinessEntity.PWS)
        or has_permission(user, Permission.MANAGE_TEACHERS_MAP_SECTIONS, entity=BusinessEntity.PWS)
    )


def assert_manage_academic(user: dict) -> None:
    if not can_manage_academic(user):
        raise HTTPException(403, "Academic structure management not permitted")


def can_enter_marks(user: dict) -> bool:
    role = normalize_role(user.get("role", ""))
    if role == UserRole.ALPHA_COACH and not has_permission(user, Permission.MANAGE_MARKS_ASSESSMENT):
        return False
    return has_permission(user, Permission.MANAGE_MARKS_ASSESSMENT, entity=BusinessEntity.PWS)


def assert_enter_marks(user: dict) -> None:
    if not can_enter_marks(user):
        raise HTTPException(403, "Marks entry not permitted")


def can_collect_pws_fees(user: dict) -> bool:
    return has_permission(user, Permission.COLLECT_PWS_FEES, entity=BusinessEntity.PWS)


def can_add_pws_students(user: dict) -> bool:
    return has_permission(user, Permission.ADD_PWS_STUDENTS, entity=BusinessEntity.PWS)


def can_run_pws_reports(user: dict) -> bool:
    return has_permission(user, Permission.RUN_PWS_REPORTS, entity=BusinessEntity.PWS)


# --- ALPHA ---

def can_mark_alpha_attendance(user: dict) -> bool:
    return has_permission(user, Permission.MARK_ALPHA_ATTENDANCE, entity=BusinessEntity.ALPHA)


def can_manage_players(user: dict) -> bool:
    return has_permission(user, Permission.MANAGE_PLAYERS, entity=BusinessEntity.ALPHA)


def can_manage_coaches(user: dict) -> bool:
    return has_permission(user, Permission.MANAGE_COACHES, entity=BusinessEntity.ALPHA)


def can_enter_coach_assessments(user: dict) -> bool:
    return has_permission(user, Permission.MANAGE_PLAYER_ASSESSMENT, entity=BusinessEntity.ALPHA)


def assert_enter_coach_assessments(user: dict) -> None:
    if not can_enter_coach_assessments(user):
        raise HTTPException(403, "Coach assessment entry not permitted")


def can_manage_coach_assessments_admin(user: dict) -> bool:
    return has_permission(user, Permission.MANAGE_COACH_ASSESSMENTS_ADMIN, entity=BusinessEntity.ALPHA)


def assert_manage_coach_assessments_admin(user: dict) -> None:
    if not can_manage_coach_assessments_admin(user):
        raise HTTPException(403, "Coach assessment administration not permitted")


def can_collect_alpha_fees(user: dict) -> bool:
    return has_permission(user, Permission.COLLECT_ALPHA_FEES, entity=BusinessEntity.ALPHA)


def can_run_alpha_reports(user: dict) -> bool:
    return has_permission(user, Permission.RUN_ALPHA_REPORTS, entity=BusinessEntity.ALPHA)


# --- Cross-entity ---

def can_view_attendance(user: dict) -> bool:
    return (
        has_permission(user, Permission.VIEW_ATTENDANCE)
        or can_mark_pws_attendance(user)
        or can_mark_alpha_attendance(user)
        or can_run_pws_reports(user)
        or can_run_alpha_reports(user)
    )


def can_correct_attendance(user: dict) -> bool:
    return has_permission(user, Permission.CORRECT_ATTENDANCE)


def can_supervise_tasks(user: dict) -> bool:
    return (
        has_permission(user, Permission.CREATE_TEACHER_TASKS)
        or has_permission(user, Permission.CREATE_COACH_TASKS)
        or has_permission(user, Permission.MANAGE_PWS_TASKS)
        or has_permission(user, Permission.MANAGE_ALPHA_TASKS)
    )


def can_manage_fee_heads(user: dict) -> bool:
    return has_permission(user, Permission.MANAGE_FEES_HEADS)


def can_approve_requests(user: dict) -> bool:
    return has_permission(user, Permission.APPROVE_REQUESTS) or has_permission(user, Permission.TOGGLE_USER_STATUS)


def can_mark_hostel_attendance(user: dict) -> bool:
    return has_permission(user, Permission.MARK_HOSTEL_ATTENDANCE)


def can_manage_player_action(user: dict, action: str) -> bool:
    """Replace coach_can — view/add/edit players."""
    if can_manage_players(user):
        return True
    if normalize_role(user.get("role", "")) != UserRole.ALPHA_COACH:
        return False
    perm_map = {"view": "view_players", "add": "add_players", "edit": "edit_players"}
    legacy = perm_map.get(action, "")
    return legacy in (user.get("coach_permissions") or [])


def assert_manage_player_action(user: dict, action: str) -> None:
    if not can_manage_player_action(user, action):
        raise HTTPException(403, f"You don't have permission to {action} players")


def fees_permission_for_entity(entity: str) -> Permission:
    return Permission.COLLECT_PWS_FEES if entity.lower() == "pws" else Permission.COLLECT_ALPHA_FEES


def can_collect_fees_for(user: dict, entity: str) -> bool:
    ent = BusinessEntity.PWS if entity.lower() == "pws" else BusinessEntity.ALPHA
    return has_permission(user, fees_permission_for_entity(entity), entity=ent)


def effective_permissions_list(user: dict) -> list[str]:
    return [p.value for p in Permission if has_permission(user, p)]
