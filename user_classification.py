"""Central login user type classification — single source of truth."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from rbac.enums import BusinessEntity, UserRole, default_entity_for_role

# Approved login user types only (staff accounts with app access).
APPROVED_LOGIN_USER_TYPES: Tuple[str, ...] = (
    UserRole.SUPER_ADMIN.value,
    UserRole.PWS_ADMIN.value,
    UserRole.ALPHA_ADMIN.value,
    UserRole.PWS_ACCOUNTS.value,
    UserRole.ALPHA_ACCOUNTS.value,
    UserRole.PWS_TEACHER.value,
    UserRole.ALPHA_COACH.value,
)

PWS_ADMIN_DESIGNATIONS = ("PRINCIPAL", "VICE_PRINCIPAL")

USER_TYPE_CATALOG: List[Dict[str, Any]] = [
    {
        "code": UserRole.SUPER_ADMIN.value,
        "displayName": "Super Admin",
        "entityScope": BusinessEntity.BOTH.value,
        "category": "Administration",
        "description": "Full system control across PWS and ALPHA",
        "manageDescription": "Full system control across PWS and ALPHA",
        "allowedDesignations": [],
        "requiresAssignedSport": False,
        "requiresAssignedVenue": False,
    },
    {
        "code": UserRole.PWS_ADMIN.value,
        "displayName": "PWS Admin",
        "entityScope": BusinessEntity.PWS.value,
        "category": "Administration",
        "description": "PWS administration — Principal and Vice Principal",
        "manageDescription": "PWS administration — Principal and Vice Principal",
        "allowedDesignations": list(PWS_ADMIN_DESIGNATIONS),
        "requiresAssignedSport": False,
        "requiresAssignedVenue": False,
    },
    {
        "code": UserRole.ALPHA_ADMIN.value,
        "displayName": "ALPHA Admin",
        "entityScope": BusinessEntity.ALPHA.value,
        "category": "Administration",
        "description": "ALPHA operations — players, coaches and attendance",
        "manageDescription": "ALPHA operations — players, coaches and attendance",
        "allowedDesignations": [],
        "requiresAssignedSport": False,
        "requiresAssignedVenue": False,
    },
    {
        "code": UserRole.PWS_ACCOUNTS.value,
        "displayName": "PWS Accounts",
        "entityScope": BusinessEntity.PWS.value,
        "category": "Accounts",
        "description": "PWS student fees, tasks and reports",
        "manageDescription": "PWS student fees, tasks and reports",
        "allowedDesignations": [],
        "requiresAssignedSport": False,
        "requiresAssignedVenue": False,
    },
    {
        "code": UserRole.ALPHA_ACCOUNTS.value,
        "displayName": "ALPHA Accounts",
        "entityScope": BusinessEntity.ALPHA.value,
        "category": "Accounts",
        "description": "ALPHA player fees, tasks and reports",
        "manageDescription": "ALPHA player fees, tasks and reports",
        "allowedDesignations": [],
        "requiresAssignedSport": False,
        "requiresAssignedVenue": False,
    },
    {
        "code": UserRole.PWS_TEACHER.value,
        "displayName": "PWS Teacher",
        "entityScope": BusinessEntity.PWS.value,
        "category": "Teaching",
        "description": "PWS student attendance and marks",
        "manageDescription": "PWS student attendance and marks",
        "allowedDesignations": [],
        "requiresAssignedSport": False,
        "requiresAssignedVenue": False,
    },
    {
        "code": UserRole.ALPHA_COACH.value,
        "displayName": "ALPHA Coach",
        "entityScope": BusinessEntity.ALPHA.value,
        "category": "Coaching",
        "description": "ALPHA player attendance and assessments",
        "manageDescription": "ALPHA player attendance and assessments",
        "allowedDesignations": [],
        "requiresAssignedSport": True,
        "requiresAssignedVenue": True,
    },
]

CATALOG_BY_CODE = {item["code"]: item for item in USER_TYPE_CATALOG}

# Legacy stored role -> canonical user_type (deterministic migration).
LEGACY_ROLE_TO_USER_TYPE: Dict[str, str] = {
    "super_admin": UserRole.SUPER_ADMIN.value,
    "admin": UserRole.ALPHA_ADMIN.value,
    "principal": UserRole.PWS_ADMIN.value,
    "vice_principal": UserRole.PWS_ADMIN.value,
    "pws_accounts": UserRole.PWS_ACCOUNTS.value,
    "alpha_accounts": UserRole.ALPHA_ACCOUNTS.value,
    "teacher": UserRole.PWS_TEACHER.value,
    "coach": UserRole.ALPHA_COACH.value,
    "pws_admin": UserRole.PWS_ADMIN.value,
    "alpha_admin": UserRole.ALPHA_ADMIN.value,
    "pws_teacher": UserRole.PWS_TEACHER.value,
    "alpha_coach": UserRole.ALPHA_COACH.value,
}

# Roles that cannot be auto-migrated — require Super Admin review.
UNMAPPED_LEGACY_ROLES = frozenset({
    "warden", "staff", "student", "player", "parent", "accounts", "sports_admin",
})

# Canonical user_type -> legacy role stored for backward-compatible authorization.
USER_TYPE_TO_LEGACY_ROLE: Dict[str, str] = {
    UserRole.SUPER_ADMIN.value: "super_admin",
    UserRole.PWS_ADMIN.value: "principal",
    UserRole.ALPHA_ADMIN.value: "admin",
    UserRole.PWS_ACCOUNTS.value: "pws_accounts",
    UserRole.ALPHA_ACCOUNTS.value: "alpha_accounts",
    UserRole.PWS_TEACHER.value: "teacher",
    UserRole.ALPHA_COACH.value: "coach",
}

DESIGNATION_TO_LEGACY_ROLE: Dict[str, str] = {
    "PRINCIPAL": "principal",
    "VICE_PRINCIPAL": "vice_principal",
}


def is_approved_login_user_type(user_type: Optional[str]) -> bool:
    return (user_type or "") in APPROVED_LOGIN_USER_TYPES


def resolve_user_type(user: dict) -> Optional[str]:
    """Return canonical user_type for a user document."""
    ut = user.get("user_type")
    if ut and is_approved_login_user_type(ut):
        return ut
    legacy = (user.get("role") or "").strip().lower()
    if legacy in LEGACY_ROLE_TO_USER_TYPE:
        return LEGACY_ROLE_TO_USER_TYPE[legacy]
    if legacy in {v.value for v in UserRole} and is_approved_login_user_type(legacy):
        return legacy
    return None


def entity_scope_for_user_type(user_type: str) -> str:
    meta = CATALOG_BY_CODE.get(user_type)
    if meta:
        return meta["entityScope"]
    try:
        return default_entity_for_role(UserRole(user_type)).value
    except ValueError:
        return BusinessEntity.PWS.value


def organization_for_user_type(user_type: str) -> str:
    scope = entity_scope_for_user_type(user_type)
    return scope if scope in ("PWS", "ALPHA", "BOTH") else "PWS"


def legacy_role_for_user_type(user_type: str, designation: Optional[str] = None) -> str:
    if user_type == UserRole.PWS_ADMIN.value and designation:
        return DESIGNATION_TO_LEGACY_ROLE.get(designation.upper(), "principal")
    return USER_TYPE_TO_LEGACY_ROLE.get(user_type, user_type)


def designation_from_legacy_role(role: str) -> Optional[str]:
    if role == "principal":
        return "PRINCIPAL"
    if role == "vice_principal":
        return "VICE_PRINCIPAL"
    return None


def migrate_legacy_role(role: str) -> Tuple[Optional[str], Optional[str], bool]:
    """Returns (user_type, designation, requires_review)."""
    key = (role or "").strip().lower()
    if key in LEGACY_ROLE_TO_USER_TYPE:
        ut = LEGACY_ROLE_TO_USER_TYPE[key]
        desig = designation_from_legacy_role(key) if ut == UserRole.PWS_ADMIN.value else None
        return ut, desig, False
    if key in UNMAPPED_LEGACY_ROLES or key not in LEGACY_ROLE_TO_USER_TYPE:
        return None, None, True
    return None, None, True


def apply_user_type_fields(doc: dict, *, user_type: str, designation: Optional[str] = None) -> dict:
    if not is_approved_login_user_type(user_type):
        raise ValueError(f"Invalid user type: {user_type}")
    meta = CATALOG_BY_CODE[user_type]
    doc["user_type"] = user_type
    doc["organization"] = organization_for_user_type(user_type)
    doc["entity_scope"] = meta["entityScope"]
    doc["role"] = legacy_role_for_user_type(user_type, designation)
    if user_type == UserRole.PWS_ADMIN.value:
        doc["designation"] = (designation or "PRINCIPAL").upper()
        if doc["designation"] not in PWS_ADMIN_DESIGNATIONS:
            raise ValueError("PWS Admin designation must be PRINCIPAL or VICE_PRINCIPAL")
        doc["role"] = legacy_role_for_user_type(user_type, doc["designation"])
    else:
        doc.pop("designation", None)
    doc["requires_user_type_review"] = False
    if not doc.get("legacy_role"):
        doc["legacy_role"] = doc.get("role")
    return doc


def validate_user_type_payload(
    user_type: str,
    *,
    designation: Optional[str] = None,
    assigned_sports: Optional[List[str]] = None,
    organization: Optional[str] = None,
) -> None:
    if not is_approved_login_user_type(user_type):
        raise ValueError(
            f"User type must be one of: {', '.join(APPROVED_LOGIN_USER_TYPES)}"
        )
    expected_org = organization_for_user_type(user_type)
    if organization and organization != expected_org:
        raise ValueError(
            f"User type {user_type} requires organization {expected_org}, not {organization}"
        )
    meta = CATALOG_BY_CODE[user_type]
    if meta.get("requiresAssignedSport"):
        sports = [s for s in (assigned_sports or []) if s]
        if len(sports) != 1:
            raise ValueError("ALPHA Coach requires exactly one assigned sport (Cricket or Football)")


def catalog_export() -> List[Dict[str, Any]]:
    return list(USER_TYPE_CATALOG)
