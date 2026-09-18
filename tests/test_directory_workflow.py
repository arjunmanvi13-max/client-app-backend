from directory_workflow import (
    ADMIN_DESIGNATIONS,
    DIRECTORY_CATEGORIES,
    PERMISSION_SET_CODES,
    canonicalize_designation,
    permission_set_for_designation,
    user_type_for_admin,
)
from rbac.enums import UserRole


def test_four_directory_categories():
    assert DIRECTORY_CATEGORIES == ("admins", "teachers", "students", "players")


def test_admin_designations_include_coach_accounts_warden():
    assert "COACH" in ADMIN_DESIGNATIONS
    assert "WARDEN" in ADMIN_DESIGNATIONS
    assert "ACCOUNTS" in ADMIN_DESIGNATIONS
    assert "OPERATIONS_ADMIN" in ADMIN_DESIGNATIONS


def test_coach_permission_set_lives_under_admins():
    from directory_workflow import PERMISSION_SET_BY_CODE
    assert PERMISSION_SET_BY_CODE["coach"]["categories"] == ["admins"]


def test_twelve_permission_sets():
    assert len(PERMISSION_SET_CODES) == 12
    assert PERMISSION_SET_CODES[0] == "super_admin"


def test_legacy_office_staff_aliases_to_operations_admin():
    assert canonicalize_designation("PWS_OFFICE_STAFF") == "OPERATIONS_ADMIN"
    assert permission_set_for_designation("ALPHA_OFFICE_STAFF") == "operations_admin"


def test_accounts_user_type_follows_institution():
    assert user_type_for_admin("ACCOUNTS", "PWS") == UserRole.PWS_ACCOUNTS.value
    assert user_type_for_admin("ACCOUNTS", "ALPHA") == UserRole.ALPHA_ACCOUNTS.value
    assert user_type_for_admin("COACH", "ALPHA") == UserRole.ALPHA_COACH.value
    assert user_type_for_admin("PRINCIPAL", "PWS") == UserRole.PWS_ADMIN.value


def test_permission_set_inferred_for_legacy_principal():
    from directory_workflow import permission_set_for_user, users_matching_permission_set_filter
    assert permission_set_for_user({"role": "principal", "organization": "PWS"}) == "principal"
    assert permission_set_for_user({"designation": "PRINCIPAL", "role": "pws_admin"}) == "principal"
    q = users_matching_permission_set_filter("principal")
    assert q["status"]["$ne"] == "deactivated"
    assert {"permission_set": "principal"} in q["$or"]
    assert permission_set_for_user({"role": "pws_admin", "user_type": "pws_admin"}) == "principal"


def test_principal_scope_is_both_without_player_flag():
    from core import resolve_user_institution
    from directory_workflow import PERMISSION_SET_BY_CODE
    assert PERMISSION_SET_BY_CODE["principal"]["scope"] == "BOTH"
    user = {"role": "principal", "organization": "PWS", "status": "active", "permissions": {"view_students": True}}
    assert resolve_user_institution(user) == "BOTH"
