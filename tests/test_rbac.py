"""RBAC authorization unit tests."""
from rbac.authorization import has_permission, normalize_role
from rbac.enums import BusinessEntity, Permission, UserRole


def _user(role: str, **kwargs) -> dict:
    return {
        "id": "u1",
        "role": role,
        "status": "active",
        "organization": "PWS",
        "permissions": {},
        **kwargs,
    }


def test_super_admin_has_create_users():
    u = _user("super_admin")
    assert has_permission(u, Permission.CREATE_USERS)


def test_teacher_has_student_attendance_not_manage_players():
    u = _user("teacher", permissions={"mark_student_attendance": True})
    assert has_permission(u, Permission.MARK_STUDENT_ATTENDANCE)
    assert not has_permission(u, Permission.MANAGE_PLAYERS)


def test_coach_has_player_assessment():
    u = _user("coach", permissions={"enter_coach_assessments": True})
    assert has_permission(u, Permission.MANAGE_PLAYER_ASSESSMENT)


def test_principal_maps_to_pws_admin_permissions():
    u = _user("principal", permissions={
        "mark_student_attendance": True,
        "manage_academic_structure": True,
    })
    assert normalize_role(u["role"]) == UserRole.PWS_ADMIN
    assert has_permission(u, Permission.MARK_PWS_ATTENDANCE)
    assert has_permission(u, Permission.MANAGE_TEACHERS_MAP_SUBJECTS)


def test_deactivated_user_denied():
    u = _user("super_admin", status="deactivated")
    assert not has_permission(u, Permission.CREATE_USERS)


def test_rbac_override_grants():
    u = _user("teacher", permissions_rbac={Permission.MANAGE_PLAYERS.value: True})
    assert has_permission(u, Permission.MANAGE_PLAYERS, use_legacy_fallback=False)


def test_entity_scope_pws_teacher_blocked_on_alpha_fee():
    u = _user("teacher", organization="PWS", permissions={"collect_fees": True})
    assert not has_permission(u, Permission.COLLECT_ALPHA_FEES, entity=BusinessEntity.ALPHA)
