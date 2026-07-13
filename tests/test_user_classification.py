"""Unit tests for canonical login user type classification."""
import pytest

from user_classification import (
    APPROVED_LOGIN_USER_TYPES,
    apply_user_type_fields,
    catalog_export,
    is_approved_login_user_type,
    legacy_role_for_user_type,
    migrate_legacy_role,
    organization_for_user_type,
    resolve_user_type,
    validate_user_type_payload,
)
from rbac.enums import UserRole


def test_exactly_seven_approved_login_types():
    assert len(APPROVED_LOGIN_USER_TYPES) == 7


def test_catalog_has_seven_entries():
    assert len(catalog_export()) == 7


def test_legacy_admin_maps_to_alpha_admin():
    ut, desig, review = migrate_legacy_role("admin")
    assert ut == UserRole.ALPHA_ADMIN.value
    assert not review


def test_legacy_principal_maps_to_pws_admin_with_designation():
    ut, desig, review = migrate_legacy_role("principal")
    assert ut == UserRole.PWS_ADMIN.value
    assert desig == "PRINCIPAL"
    assert not review


def test_legacy_coach_maps_to_alpha_coach():
    ut, _, review = migrate_legacy_role("coach")
    assert ut == UserRole.ALPHA_COACH.value
    assert not review


def test_legacy_parent_requires_review():
    ut, _, review = migrate_legacy_role("parent")
    assert ut is None
    assert review is True


def test_legacy_warden_requires_review():
    _, _, review = migrate_legacy_role("warden")
    assert review is True


def test_apply_user_type_sets_organization():
    doc = apply_user_type_fields({}, user_type=UserRole.PWS_TEACHER.value)
    assert doc["organization"] == "PWS"
    assert doc["role"] == "teacher"
    assert doc["user_type"] == UserRole.PWS_TEACHER.value


def test_apply_user_type_pws_admin_designation():
    doc = apply_user_type_fields({}, user_type=UserRole.PWS_ADMIN.value, designation="VICE_PRINCIPAL")
    assert doc["role"] == "vice_principal"
    assert doc["designation"] == "VICE_PRINCIPAL"


def test_validate_rejects_wrong_organization():
    with pytest.raises(ValueError, match="requires organization"):
        validate_user_type_payload(UserRole.PWS_ADMIN.value, organization="ALPHA")


def test_validate_alpha_coach_requires_sport():
    with pytest.raises(ValueError, match="exactly one assigned sport"):
        validate_user_type_payload(UserRole.ALPHA_COACH.value, assigned_sports=[])


def test_validate_alpha_coach_accepts_single_sport():
    validate_user_type_payload(UserRole.ALPHA_COACH.value, assigned_sports=["Cricket"])


def test_resolve_user_type_from_legacy_role():
    assert resolve_user_type({"role": "teacher"}) == UserRole.PWS_TEACHER.value


def test_resolve_user_type_prefers_user_type_field():
    assert resolve_user_type({"role": "teacher", "user_type": UserRole.PWS_TEACHER.value}) == UserRole.PWS_TEACHER.value


def test_rejects_unknown_user_type():
    assert not is_approved_login_user_type("parent")
    assert not is_approved_login_user_type("admin")


def test_super_admin_entity_scope_both():
    assert organization_for_user_type(UserRole.SUPER_ADMIN.value) == "BOTH"


def test_rejects_sports_admin_as_user_type():
    assert not is_approved_login_user_type("sports_admin")
    assert not is_approved_login_user_type("head_coach")


def test_entity_scope_pws_admin():
    from user_classification import entity_scope_for_user_type
    assert entity_scope_for_user_type(UserRole.PWS_ADMIN.value) == "PWS"
    assert entity_scope_for_user_type(UserRole.ALPHA_ADMIN.value) == "ALPHA"


def test_migrate_sports_admin_requires_review():
    _, _, review = migrate_legacy_role("sports_admin")
    assert review is True


def test_create_pws_accounts_payload():
    """PWS Accounts users must persist with PWS scope and legacy role."""
    doc = apply_user_type_fields({}, user_type=UserRole.PWS_ACCOUNTS.value)
    assert doc["user_type"] == UserRole.PWS_ACCOUNTS.value
    assert doc["role"] == "pws_accounts"
    assert doc["organization"] == "PWS"
    validate_user_type_payload(UserRole.PWS_ACCOUNTS.value, organization="PWS")


def test_legacy_role_for_pws_admin_designation():
    assert legacy_role_for_user_type(UserRole.PWS_ADMIN.value, "VICE_PRINCIPAL") == "vice_principal"
