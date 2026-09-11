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

LOGIN_TIERS: Tuple[str, ...] = ("super_admin", "admin", "staff")

PWS_ADMIN_DESIGNATIONS = ("PRINCIPAL", "VICE_PRINCIPAL", "ACADEMIC_HEAD", "EVENT_COORDINATOR")

PWS_DESIGNATIONS = (
    "PRINCIPAL",
    "VICE_PRINCIPAL",
    "ACADEMIC_HEAD",
    "EVENT_COORDINATOR",
    "PWS_OFFICE_STAFF",
    "PWS_ACCOUNTS",
    "HOD",
    "TEACHER",
)
ALPHA_DESIGNATIONS = (
    "WARDEN",
    "COACH",
    "ALPHA_ACCOUNTS",
    "ALPHA_OFFICE_STAFF",
)
ALL_DESIGNATIONS = PWS_DESIGNATIONS + ALPHA_DESIGNATIONS

DESIGNATION_LABELS = {
    "PRINCIPAL": "Principal",
    "VICE_PRINCIPAL": "Vice Principal",
    "ACADEMIC_HEAD": "Academic Head",
    "EVENT_COORDINATOR": "Event Co-ordinator",
    "PWS_OFFICE_STAFF": "PWS Office Staff",
    "PWS_ACCOUNTS": "PWS Accounts",
    "HOD": "HOD",
    "TEACHER": "Teacher",
    "WARDEN": "Warden",
    "COACH": "Coaches",
    "ALPHA_ACCOUNTS": "ALPHA Accounts",
    "ALPHA_OFFICE_STAFF": "ALPHA Office Staff",
}

# Designation → canonical user_type used by existing RBAC.
DESIGNATION_TO_USER_TYPE: Dict[str, str] = {
    "PRINCIPAL": UserRole.PWS_ADMIN.value,
    "VICE_PRINCIPAL": UserRole.PWS_ADMIN.value,
    "ACADEMIC_HEAD": UserRole.PWS_ADMIN.value,
    "EVENT_COORDINATOR": UserRole.PWS_ADMIN.value,
    "PWS_OFFICE_STAFF": UserRole.PWS_ADMIN.value,
    "PWS_ACCOUNTS": UserRole.PWS_ACCOUNTS.value,
    "HOD": UserRole.PWS_TEACHER.value,
    "TEACHER": UserRole.PWS_TEACHER.value,
    "WARDEN": UserRole.ALPHA_ADMIN.value,
    "COACH": UserRole.ALPHA_COACH.value,
    "ALPHA_ACCOUNTS": UserRole.ALPHA_ACCOUNTS.value,
    "ALPHA_OFFICE_STAFF": UserRole.ALPHA_ADMIN.value,
}

USER_TYPE_TO_LOGIN_TIER: Dict[str, str] = {
    UserRole.SUPER_ADMIN.value: "super_admin",
    UserRole.PWS_ADMIN.value: "admin",
    UserRole.ALPHA_ADMIN.value: "admin",
    UserRole.PWS_ACCOUNTS.value: "admin",
    UserRole.ALPHA_ACCOUNTS.value: "admin",
    UserRole.PWS_TEACHER.value: "staff",
    UserRole.ALPHA_COACH.value: "staff",
}

LOGIN_TIER_CATALOG: List[Dict[str, Any]] = [
    {
        "code": "super_admin",
        "displayName": "Super Admin",
        "description": "Full system control across PWS and ALPHA",
        "manageDescription": "Platform owner — all modules, both entities",
        "icon": "shield",
        "tint": "#0F172A",
    },
    {
        "code": "login_admin",
        "tier": "admin",
        "displayName": "Admin",
        "description": "Entity administrators with designation-based access",
        "manageDescription": "Assign entity, designation, and module access",
        "icon": "briefcase",
        "tint": "#1B3B6F",
    },
    {
        "code": "login_staff",
        "tier": "staff",
        "displayName": "Staff",
        "description": "Operational staff with scoped module access",
        "manageDescription": "Assign entity, designation, and module access",
        "icon": "users",
        "tint": "#00A8E8",
    },
]

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
        "description": "PWS administration — Principal, Vice Principal, Academic Head, Event Coordinator",
        "manageDescription": "PWS administration — Principal, Vice Principal, Academic Head, Event Coordinator",
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
        "displayName": "PWS Teachers",
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
        "displayName": "ALPHA Coaches",
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
    "ACADEMIC_HEAD": "pws_admin",
    "EVENT_COORDINATOR": "pws_admin",
    "PWS_OFFICE_STAFF": "pws_admin",
    "PWS_ACCOUNTS": "pws_accounts",
    "HOD": "teacher",
    "TEACHER": "teacher",
    "WARDEN": "admin",
    "COACH": "coach",
    "ALPHA_ACCOUNTS": "alpha_accounts",
    "ALPHA_OFFICE_STAFF": "admin",
}


def designations_for_entity(entity: Optional[str]) -> Tuple[str, ...]:
    key = (entity or "").upper()
    if key == "PWS":
        return PWS_DESIGNATIONS
    if key == "ALPHA":
        return ALPHA_DESIGNATIONS
    return ALL_DESIGNATIONS


def user_type_from_designation(designation: Optional[str], *, login_tier: Optional[str] = None) -> str:
    if login_tier == "super_admin":
        return UserRole.SUPER_ADMIN.value
    code = (designation or "").upper()
    if code in DESIGNATION_TO_USER_TYPE:
        return DESIGNATION_TO_USER_TYPE[code]
    if login_tier == "staff":
        return UserRole.PWS_TEACHER.value
    return UserRole.PWS_ADMIN.value


def resolve_login_tier(user: dict) -> str:
    stored = (user.get("login_tier") or "").strip().lower()
    if stored in LOGIN_TIERS:
        return stored
    ut = resolve_user_type(user)
    if ut:
        return USER_TYPE_TO_LOGIN_TIER.get(ut, "staff")
    return "staff"


def login_tier_list_query(tier: str) -> dict:
    """Mongo filter for a login-tier list (Admin / Staff / Super Admin)."""
    t = (tier or "").strip().lower()
    if t in ("login_admin", "org_admin"):
        t = "admin"
    if t in ("login_staff",):
        t = "staff"
    if t == "super_admin":
        return {
            "$or": [
                {"login_tier": "super_admin"},
                {"user_type": UserRole.SUPER_ADMIN.value},
                {"role": "super_admin"},
            ]
        }
    types = [code for code, mapped in USER_TYPE_TO_LOGIN_TIER.items() if mapped == t]
    return {
        "$or": [
            {"login_tier": t},
            {"user_type": {"$in": types}},
        ]
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
    if designation:
        mapped = DESIGNATION_TO_LEGACY_ROLE.get(designation.upper())
        if mapped:
            return mapped
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


def apply_user_type_fields(
    doc: dict,
    *,
    user_type: str,
    designation: Optional[str] = None,
    entity_scope: Optional[str] = None,
    login_tier: Optional[str] = None,
) -> dict:
    if not is_approved_login_user_type(user_type):
        raise ValueError(f"Invalid user type: {user_type}")
    meta = CATALOG_BY_CODE[user_type]
    doc["user_type"] = user_type
    scope = (entity_scope or "").upper() if entity_scope else meta["entityScope"]
    if scope not in ("PWS", "ALPHA", "BOTH"):
        scope = meta["entityScope"]
    doc["organization"] = scope
    doc["entity_scope"] = scope
    doc["login_tier"] = (login_tier or USER_TYPE_TO_LOGIN_TIER.get(user_type, "staff")).lower()
    if doc["login_tier"] not in LOGIN_TIERS:
        doc["login_tier"] = USER_TYPE_TO_LOGIN_TIER.get(user_type, "staff")
    desig = (designation or "").upper() or None
    if desig:
        allowed = designations_for_entity(scope)
        if desig not in allowed and desig not in ALL_DESIGNATIONS:
            raise ValueError(f"Invalid designation for entity {scope}: {desig}")
        doc["designation"] = desig
    elif user_type == UserRole.PWS_ADMIN.value:
        doc["designation"] = "PRINCIPAL"
        desig = "PRINCIPAL"
    else:
        doc.pop("designation", None)
    doc["role"] = legacy_role_for_user_type(user_type, desig)
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
    entity_scope: Optional[str] = None,
) -> None:
    if not is_approved_login_user_type(user_type):
        raise ValueError(
            f"User type must be one of: {', '.join(APPROVED_LOGIN_USER_TYPES)}"
        )
    scope = (entity_scope or organization or "").upper() or None
    if scope and scope not in ("PWS", "ALPHA", "BOTH"):
        raise ValueError(f"Invalid entity: {scope}")
    if designation:
        allowed = designations_for_entity(scope or "BOTH")
        if designation.upper() not in allowed:
            raise ValueError(
                f"Designation {designation} is not valid for entity {scope or 'BOTH'}"
            )
    if not entity_scope:
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


def login_tier_catalog_export() -> Dict[str, Any]:
    return {
        "loginTiers": list(LOGIN_TIER_CATALOG),
        "pwsDesignations": [{"code": c, "label": DESIGNATION_LABELS[c]} for c in PWS_DESIGNATIONS],
        "alphaDesignations": [{"code": c, "label": DESIGNATION_LABELS[c]} for c in ALPHA_DESIGNATIONS],
        "accessLevels": [
            {"code": "none", "label": "No Access"},
            {"code": "view", "label": "View Only"},
            {"code": "edit", "label": "Edit / Manage"},
            {"code": "admin", "label": "Full Admin"},
        ],
    }
