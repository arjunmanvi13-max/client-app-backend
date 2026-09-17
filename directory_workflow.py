"""Directory categories, Admin designations, and reusable permission sets."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from rbac.enums import UserRole

DIRECTORY_CATEGORIES: Tuple[str, ...] = ("admins", "teachers", "students", "players")

ADMIN_DESIGNATIONS: Tuple[str, ...] = (
    "PRINCIPAL",
    "VICE_PRINCIPAL",
    "ACADEMIC_HEAD",
    "EVENT_COORDINATOR",
    "OPERATIONS_ADMIN",
    "ACCOUNTS",
    "COACH",
    "WARDEN",
)

ADMIN_DESIGNATION_LABELS: Dict[str, str] = {
    "PRINCIPAL": "Principal",
    "VICE_PRINCIPAL": "Vice Principal",
    "ACADEMIC_HEAD": "Academic Head",
    "EVENT_COORDINATOR": "Event Co-ordinator",
    "OPERATIONS_ADMIN": "Operations Admin",
    "ACCOUNTS": "Accounts",
    "COACH": "Coach",
    "WARDEN": "Warden",
}

# Legacy designation codes still stored on older records.
DESIGNATION_ALIASES: Dict[str, str] = {
    "PWS_OFFICE_STAFF": "OPERATIONS_ADMIN",
    "ALPHA_OFFICE_STAFF": "OPERATIONS_ADMIN",
    "PWS_ACCOUNTS": "ACCOUNTS",
    "ALPHA_ACCOUNTS": "ACCOUNTS",
}

PERMISSION_SET_CODES: Tuple[str, ...] = (
    "super_admin",
    "principal",
    "vice_principal",
    "academic_head",
    "event_coordinator",
    "operations_admin",
    "accounts",
    "coach",
    "warden",
    "teacher",
    "student",
    "player",
)

PERMISSION_SET_CATALOG: List[Dict[str, Any]] = [
    {"code": "super_admin", "name": "Super Admin", "description": "Full access across PWS and ALPHA", "scope": "BOTH", "categories": ["admins"], "designations": [], "locked": True, "compat_user_type": UserRole.SUPER_ADMIN.value},
    {"code": "principal", "name": "Principal", "description": "School leadership across PWS and ALPHA", "scope": "BOTH", "categories": ["admins"], "designations": ["PRINCIPAL"], "locked": False, "compat_user_type": UserRole.PWS_ADMIN.value},
    {"code": "vice_principal", "name": "Vice Principal", "description": "PWS school leadership", "scope": "PWS", "categories": ["admins"], "designations": ["VICE_PRINCIPAL"], "locked": False, "compat_user_type": UserRole.PWS_ADMIN.value},
    {"code": "academic_head", "name": "Academic Head", "description": "PWS academics leadership", "scope": "PWS", "categories": ["admins"], "designations": ["ACADEMIC_HEAD"], "locked": False, "compat_user_type": UserRole.PWS_ADMIN.value},
    {"code": "event_coordinator", "name": "Event Co-ordinator", "description": "PWS events and operations", "scope": "PWS", "categories": ["admins"], "designations": ["EVENT_COORDINATOR"], "locked": False, "compat_user_type": UserRole.PWS_ADMIN.value},
    {"code": "operations_admin", "name": "Operations Admin", "description": "Day-to-day PWS or ALPHA operations", "scope": "BOTH", "categories": ["admins"], "designations": ["OPERATIONS_ADMIN"], "locked": False, "compat_user_type": UserRole.PWS_ADMIN.value},
    {"code": "accounts", "name": "Accounts", "description": "Fees, invoices, and finance", "scope": "BOTH", "categories": ["admins"], "designations": ["ACCOUNTS"], "locked": False, "compat_user_type": UserRole.PWS_ACCOUNTS.value},
    {"code": "coach", "name": "Coach", "description": "ALPHA coaching and player attendance", "scope": "ALPHA", "categories": ["admins"], "designations": ["COACH"], "locked": False, "compat_user_type": UserRole.ALPHA_COACH.value},
    {"code": "warden", "name": "Warden", "description": "ALPHA hostel operations", "scope": "ALPHA", "categories": ["admins"], "designations": ["WARDEN"], "locked": False, "compat_user_type": UserRole.ALPHA_ADMIN.value},
    {"code": "teacher", "name": "Teacher", "description": "PWS classroom teaching", "scope": "PWS", "categories": ["teachers"], "designations": ["TEACHER", "HOD"], "locked": False, "compat_user_type": UserRole.PWS_TEACHER.value},
    {"code": "student", "name": "Student", "description": "Restricted student self-service", "scope": "PWS", "categories": ["students"], "designations": [], "locked": False, "compat_user_type": UserRole.PWS_TEACHER.value},
    {"code": "player", "name": "Player", "description": "Restricted player self-service", "scope": "ALPHA", "categories": ["players"], "designations": [], "locked": False, "compat_user_type": UserRole.ALPHA_COACH.value},
]

PERMISSION_SET_BY_CODE = {item["code"]: item for item in PERMISSION_SET_CATALOG}

DESIGNATION_TO_PERMISSION_SET: Dict[str, str] = {
    "PRINCIPAL": "principal",
    "VICE_PRINCIPAL": "vice_principal",
    "ACADEMIC_HEAD": "academic_head",
    "EVENT_COORDINATOR": "event_coordinator",
    "OPERATIONS_ADMIN": "operations_admin",
    "ACCOUNTS": "accounts",
    "COACH": "coach",
    "WARDEN": "warden",
    "TEACHER": "teacher",
    "HOD": "teacher",
}

ROLE_TO_PERMISSION_SET: Dict[str, str] = {
    "super_admin": "super_admin",
    "principal": "principal",
    "vice_principal": "vice_principal",
    "teacher": "teacher",
    "pws_teacher": "teacher",
    "coach": "coach",
    "alpha_coach": "coach",
    "pws_accounts": "accounts",
    "alpha_accounts": "accounts",
    "warden": "warden",
}

USER_TYPE_TO_PERMISSION_SET: Dict[str, str] = {
    UserRole.SUPER_ADMIN.value: "super_admin",
    UserRole.PWS_TEACHER.value: "teacher",
    UserRole.ALPHA_COACH.value: "coach",
    UserRole.PWS_ACCOUNTS.value: "accounts",
    UserRole.ALPHA_ACCOUNTS.value: "accounts",
}


def canonicalize_designation(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    key = str(raw).strip().upper()
    return DESIGNATION_ALIASES.get(key, key)


def permission_set_for_designation(designation: Optional[str]) -> Optional[str]:
    canon = canonicalize_designation(designation)
    if not canon:
        return None
    return DESIGNATION_TO_PERMISSION_SET.get(canon)


def permission_set_for_user(user: dict) -> Optional[str]:
    """Resolve the reusable permission set for a login user, including legacy records."""
    stored = (user.get("permission_set") or "").strip().lower()
    if stored in PERMISSION_SET_BY_CODE:
        return stored
    from_desig = permission_set_for_designation(user.get("designation"))
    if from_desig:
        return from_desig
    ut = (user.get("user_type") or "").strip().lower()
    if ut in USER_TYPE_TO_PERMISSION_SET:
        return USER_TYPE_TO_PERMISSION_SET[ut]
    role = (user.get("role") or "").strip().lower()
    if role in ROLE_TO_PERMISSION_SET:
        return ROLE_TO_PERMISSION_SET[role]
    legacy = (user.get("legacy_role") or "").strip().lower()
    if legacy in ROLE_TO_PERMISSION_SET:
        return ROLE_TO_PERMISSION_SET[legacy]
    # Historical PWS Admin logins without a designation defaulted to Principal.
    if ut == UserRole.PWS_ADMIN.value or role == "pws_admin":
        return "principal"
    if ut == UserRole.ALPHA_ADMIN.value or role in ("alpha_admin", "admin"):
        return "operations_admin"
    return None


def designations_for_permission_set(code: str) -> List[str]:
    meta = PERMISSION_SET_BY_CODE.get(code) or {}
    codes = list(meta.get("designations") or [])
    extra = [alias for alias, canon in DESIGNATION_ALIASES.items() if canon in codes]
    return list(dict.fromkeys(codes + extra))


def roles_for_permission_set(code: str) -> List[str]:
    return [role for role, mapped in ROLE_TO_PERMISSION_SET.items() if mapped == code]


def users_matching_permission_set_filter(code: str) -> dict:
    """Mongo filter for live users who should receive this permission set."""
    designations = designations_for_permission_set(code)
    roles = roles_for_permission_set(code)
    clauses: List[dict] = [{"permission_set": code}]
    if designations:
        clauses.append({
            "$expr": {
                "$in": [
                    {"$toUpper": {"$ifNull": ["$designation", ""]}},
                    designations,
                ]
            }
        })
    if roles:
        clauses.append({"role": {"$in": roles}})
        clauses.append({"legacy_role": {"$in": roles}})
    if code == "principal":
        clauses.append({
            "user_type": UserRole.PWS_ADMIN.value,
            "$or": [
                {"designation": {"$exists": False}},
                {"designation": None},
                {"designation": ""},
                {"designation": "PRINCIPAL"},
            ],
        })
        clauses.append({
            "role": "pws_admin",
            "$or": [
                {"designation": {"$exists": False}},
                {"designation": None},
                {"designation": ""},
                {"designation": "PRINCIPAL"},
            ],
        })
    return {
        "status": {"$ne": "deactivated"},
        "$or": clauses,
    }


def user_type_for_admin(designation: Optional[str], organization: Optional[str]) -> str:
    """Compatibility user_type derived from Admin designation and institution scope."""
    canon = canonicalize_designation(designation) or ""
    org = (organization or "PWS").upper()
    if org == "BOTH":
        org = "PWS" if canon in ("PRINCIPAL", "VICE_PRINCIPAL", "ACADEMIC_HEAD", "EVENT_COORDINATOR") else org
    if canon in ("PRINCIPAL", "VICE_PRINCIPAL", "ACADEMIC_HEAD", "EVENT_COORDINATOR"):
        return UserRole.PWS_ADMIN.value
    if canon == "OPERATIONS_ADMIN":
        return UserRole.ALPHA_ADMIN.value if org == "ALPHA" else UserRole.PWS_ADMIN.value
    if canon == "ACCOUNTS":
        return UserRole.ALPHA_ACCOUNTS.value if org == "ALPHA" else UserRole.PWS_ACCOUNTS.value
    if canon == "COACH":
        return UserRole.ALPHA_COACH.value
    if canon == "WARDEN":
        return UserRole.ALPHA_ADMIN.value
    if canon in ("TEACHER", "HOD"):
        return UserRole.PWS_TEACHER.value
    return UserRole.PWS_ADMIN.value


def directory_category_for_user(user: dict) -> str:
    ut = (user.get("user_type") or "").lower()
    role = (user.get("role") or "").lower()
    if ut == UserRole.PWS_TEACHER.value or role in ("teacher", "pws_teacher"):
        return "teachers"
    if ut == UserRole.SUPER_ADMIN.value or role == "super_admin":
        return "admins"
    return "admins"


def directory_category_for_person(person: dict) -> Optional[str]:
    kind = (person.get("kind") or "").lower()
    if kind == "student":
        return "students"
    if kind == "player":
        return "players"
    if kind == "staff":
        return "admins"
    return None


def admin_designations_for_scope(scope: str) -> List[str]:
    scope = (scope or "BOTH").upper()
    pws = ["PRINCIPAL", "VICE_PRINCIPAL", "ACADEMIC_HEAD", "EVENT_COORDINATOR", "OPERATIONS_ADMIN", "ACCOUNTS"]
    alpha = ["OPERATIONS_ADMIN", "ACCOUNTS", "COACH", "WARDEN"]
    if scope == "PWS":
        return pws
    if scope == "ALPHA":
        return alpha
    return list(ADMIN_DESIGNATIONS)
