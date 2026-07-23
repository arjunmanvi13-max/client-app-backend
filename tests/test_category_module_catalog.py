"""Unit tests for category module catalog (no API required)."""
from category_module_catalog import (
    LOCKED_USER_TYPES,
    default_enabled_map,
    derive_permissions_from_modules,
    filter_catalog_for_user_type,
    leaf_module_ids,
    permissions_catalog,
    all_module_ids,
)
from rbac.enums import Permission, UserRole
from user_classification import APPROVED_LOGIN_USER_TYPES


def test_seven_user_types_have_defaults():
    types = [
        UserRole.SUPER_ADMIN.value,
        UserRole.PWS_ADMIN.value,
        UserRole.ALPHA_ADMIN.value,
        UserRole.PWS_ACCOUNTS.value,
        UserRole.ALPHA_ACCOUNTS.value,
        UserRole.PWS_TEACHER.value,
        UserRole.ALPHA_COACH.value,
    ]
    leaf_ids = set(leaf_module_ids())
    for ut in types:
        m = default_enabled_map(ut)
        assert isinstance(m, dict)
        assert set(m.keys()) == leaf_ids


def test_all_user_types_share_full_permissions_catalog():
    catalog = permissions_catalog()
    leaf_ids = leaf_module_ids(catalog)
    for ut in APPROVED_LOGIN_USER_TYPES:
        enabled = default_enabled_map(ut)
        assert set(enabled.keys()) == set(leaf_ids)


def test_super_admin_locked_all_on():
    m = default_enabled_map(UserRole.SUPER_ADMIN.value)
    assert all(m.values())


def test_pws_teacher_no_financials_modules():
    catalog = filter_catalog_for_user_type(UserRole.PWS_TEACHER.value)
    group_ids = {g["id"] for g in catalog}
    assert "financials" not in group_ids


def test_alpha_coach_no_system_settings():
    catalog = filter_catalog_for_user_type(UserRole.ALPHA_COACH.value)
    group_ids = {g["id"] for g in catalog}
    assert "system" not in group_ids


def test_alpha_coach_has_attendance_and_assessments():
    catalog = filter_catalog_for_user_type(UserRole.ALPHA_COACH.value)
    group_ids = {g["id"] for g in catalog}
    assert "operations" in group_ids
    assert "academics" in group_ids


def test_derive_permissions_enables_dashboard():
    modules = default_enabled_map(UserRole.PWS_TEACHER.value)
    legacy, rbac = derive_permissions_from_modules(UserRole.PWS_TEACHER.value, modules)
    assert legacy.get("dashboard_access") is True
    assert rbac.get("DASHBOARD_ACCESS") is True


def test_clear_all_disables_permissions():
    modules = {k: False for k in all_module_ids()}
    legacy, rbac = derive_permissions_from_modules(UserRole.PWS_ACCOUNTS.value, modules)
    assert not any(legacy.values())
    assert not rbac


def test_super_admin_in_locked_set():
    assert UserRole.SUPER_ADMIN.value in LOCKED_USER_TYPES


def test_teachers_module_in_flat_directory_catalog():
    ids = all_module_ids()
    assert "teachers-directory" in ids
    assert "add-new-teacher" not in ids
    assert "staff-coaches" not in ids
    assert "students-players" not in ids
    directory = next(g for g in permissions_catalog() if g["id"] == "directory")
    module_ids = [m["id"] for m in directory["modules"]]
    assert module_ids == ["directory-master", "staff", "coaches", "teachers-directory", "students", "players"]
    teachers = next(m for m in directory["modules"] if m["id"] == "teachers-directory")
    assert Permission.ADD_NEW_TEACHER.value in (teachers.get("rbac_permissions") or [])


def test_manage_users_rosters_module_in_system_settings():
    ids = all_module_ids()
    assert "manage-users-rosters" in ids
    assert "manage-users-rosters" in leaf_module_ids()
    defaults = default_enabled_map(UserRole.PWS_ADMIN.value)
    assert defaults.get("manage-users-rosters") is False
    super_defaults = default_enabled_map(UserRole.SUPER_ADMIN.value)
    assert super_defaults.get("manage-users-rosters") is True
