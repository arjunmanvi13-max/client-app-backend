"""
RBAC module — roles, permissions, schemas, and authorization guards.

Quick start::

    from rbac import Permission, BusinessEntity, has_permission, require_permission

    @router.post("/players")
    async def add_player(user: dict = Depends(require_permission(Permission.MANAGE_PLAYERS, entity=BusinessEntity.ALPHA))):
        ...
"""
from rbac.authorization import (
    assert_permission,
    has_permission,
    list_effective_permissions,
    normalize_role,
    require_any_permission,
    require_permission,
    resolve_user_entity,
)
from rbac.enums import BusinessEntity, Permission, UserRole
from rbac.role_permissions import ROLE_PERMISSIONS, permissions_for_role
from rbac.schemas import (
    CoachSportAssignment,
    FeeHeadDocument,
    MONGODB_INDEXES,
    PersonDocument,
    PlayerEnrollment,
    StudentEnrollment,
    TeacherSectionAssignment,
    TeacherSubjectAssignment,
    UserDocument,
)

__all__ = [
    "BusinessEntity",
    "Permission",
    "UserRole",
    "ROLE_PERMISSIONS",
    "permissions_for_role",
    "has_permission",
    "assert_permission",
    "require_permission",
    "require_any_permission",
    "normalize_role",
    "resolve_user_entity",
    "list_effective_permissions",
    "UserDocument",
    "PersonDocument",
    "TeacherSubjectAssignment",
    "TeacherSectionAssignment",
    "CoachSportAssignment",
    "StudentEnrollment",
    "PlayerEnrollment",
    "FeeHeadDocument",
    "MONGODB_INDEXES",
]
