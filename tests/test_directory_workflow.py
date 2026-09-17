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
