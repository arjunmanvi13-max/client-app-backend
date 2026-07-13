"""Authorization utilities and FastAPI dependency guards."""
from typing import Optional, Union

from fastapi import Depends, HTTPException

from rbac.enums import BusinessEntity, LEGACY_ROLE_ALIASES, Permission, UserRole
from rbac.legacy_map import PERMISSION_TO_LEGACY
from rbac.role_permissions import permissions_for_role


def normalize_role(raw_role: str) -> UserRole:
    """Map a stored MongoDB role string to canonical UserRole."""
    key = (raw_role or "").strip().lower()
    if key in LEGACY_ROLE_ALIASES:
        return LEGACY_ROLE_ALIASES[key]
    try:
        return UserRole(key)
    except ValueError:
        return UserRole.STAFF


def resolve_user_entity(user: dict) -> BusinessEntity:
    org = (user.get("organization") or "PWS").upper()
    try:
        return BusinessEntity(org)
    except ValueError:
        return BusinessEntity.PWS


def entity_allows(user_entity: BusinessEntity, required: Optional[BusinessEntity]) -> bool:
    if required is None:
        return True
    if user_entity == BusinessEntity.BOTH:
        return True
    return user_entity == required


def _legacy_permission_granted(user: dict, permission: Permission) -> bool:
    """Check legacy user.permissions{} with role defaults when map is empty."""
    legacy_keys = PERMISSION_TO_LEGACY.get(permission, ())
    if not legacy_keys:
        return False
    perms = user.get("permissions")
    if not perms:
        try:
            from core import default_permissions  # noqa: WPS433 — runtime only (FastAPI)
            perms = default_permissions(user.get("role", ""), user.get("coach_type"))
        except Exception:
            perms = {}
    return any(bool(perms.get(key)) for key in legacy_keys)


def has_permission(
    user: dict,
    permission: Union[Permission, str],
    *,
    entity: Optional[BusinessEntity] = None,
    use_legacy_fallback: bool = True,
) -> bool:
    """
    Return True if the user may perform `permission`.

    Evaluation order:
    1. Inactive users → False
    2. Super Admin → True (all permissions)
    3. Explicit RBAC override on user.permissions_rbac[permission]
    4. Role default from ROLE_PERMISSIONS
    5. (optional) Legacy snake_case permissions map
    """
    if not user or user.get("status") == "deactivated" or user.get("is_active") is False:
        return False

    if isinstance(permission, str):
        try:
            permission = Permission(permission)
        except ValueError:
            return False

    role = normalize_role(user.get("role", ""))
    if role == UserRole.SUPER_ADMIN:
        return entity_allows(resolve_user_entity(user), entity)

    if entity and not entity_allows(resolve_user_entity(user), entity):
        return False

    rbac_overrides = user.get("permissions_rbac") or {}
    if permission.value in rbac_overrides:
        return bool(rbac_overrides[permission.value])

    if permission in permissions_for_role(role):
        return True

    if use_legacy_fallback:
        return _legacy_permission_granted(user, permission)

    return False


def assert_permission(
    user: dict,
    permission: Permission,
    *,
    entity: Optional[BusinessEntity] = None,
) -> None:
    if not has_permission(user, permission, entity=entity):
        raise HTTPException(
            403,
            f"Missing permission: {permission.value}"
            + (f" for entity {entity.value}" if entity else ""),
        )


def require_permission(
    permission: Permission,
    *,
    entity: Optional[BusinessEntity] = None,
):
    """
    FastAPI dependency factory.

    Usage:
        @router.post("/fees/collect")
        async def collect(user: dict = Depends(require_permission(Permission.COLLECT_PWS_FEES, entity=BusinessEntity.PWS))):
            ...
    """
    from core import get_current_user  # noqa: WPS433

    async def _dep(user: dict = Depends(get_current_user)) -> dict:
        assert_permission(user, permission, entity=entity)
        return user

    return _dep


def require_any_permission(*permissions: Permission, entity: Optional[BusinessEntity] = None):
    """FastAPI dependency — user needs at least one of the listed permissions."""
    from core import get_current_user  # noqa: WPS433

    async def _dep(user: dict = Depends(get_current_user)) -> dict:
        if any(has_permission(user, p, entity=entity) for p in permissions):
            return user
        names = ", ".join(p.value for p in permissions)
        raise HTTPException(403, f"Requires one of: {names}")

    return _dep


def list_effective_permissions(user: dict) -> list[str]:
    """All Permission values the user effectively holds."""
    return [p.value for p in Permission if has_permission(user, p)]
