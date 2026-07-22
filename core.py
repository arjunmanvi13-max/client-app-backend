"""Shared core utilities: DB connection, security, models, dependencies."""
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Literal, Dict

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr, field_validator, model_validator

# ------------------ DB ------------------
mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

# ------------------ Logging ------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("pws-alpha")

# ------------------ Security ------------------
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGO = "HS256"
ACCESS_TOKEN_EXPIRES_HOURS = 2     # spec: auto logout after inactivity ~2h

ROLES = [
    "super_admin", "admin", "principal", "vice_principal",
    "pws_accounts", "alpha_accounts",
    "teacher", "coach", "warden", "staff", "student", "player", "parent",
]
MANAGE_KINDS = {"student", "player", "teacher", "coach", "staff"}

# All login emails MUST belong to this domain (org policy).
ALLOWED_EMAIL_DOMAIN = "@prarambhika.com"

def validate_domain_email(email: str) -> str:
    e = (email or "").lower().strip()
    if not e.endswith(ALLOWED_EMAIL_DOMAIN):
        raise HTTPException(400, f"Email must belong to the {ALLOWED_EMAIL_DOMAIN} domain")
    return e

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def format_date_display(value: Optional[str] = None) -> str:
    """User-facing date: dd/mm/yyyy."""
    if not value:
        return "—"
    s = str(value).strip()
    if not s:
        return "—"
    if "T" in s:
        s = s[:10]
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        y, m, d = s[:10].split("-")
        return f"{d}/{m}/{y}"
    return s

def format_datetime_display(value: Optional[str] = None) -> str:
    """User-facing datetime: dd/mm/yyyy, HH:MM."""
    if not value:
        return "—"
    s = str(value).strip()
    if not s:
        return "—"
    if "T" in s:
        date_part, time_part = s.split("T", 1)
        time_part = time_part[:5] if len(time_part) >= 5 else time_part
        return f"{format_date_display(date_part)}, {time_part}"
    return format_date_display(s)

def format_month_display(value: Optional[str] = None) -> str:
    """User-facing month period: mm/yyyy."""
    if not value:
        return "—"
    s = str(value).strip()
    if len(s) >= 7 and s[4:5] == "-":
        y, m = s[:7].split("-")
        return f"{m}/{y}"
    return format_date_display(s)

def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()

def verify_password(p: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(p.encode(), h.encode())
    except Exception:
        return False

def create_token(user_id: str, email: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "exp": now_utc() + timedelta(hours=ACCESS_TOKEN_EXPIRES_HOURS),
        "iat": now_utc(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

def directory_user(u: dict) -> dict:
    """Minimal info safe for any authenticated user."""
    return {
        "id": u["id"],
        "name": u["name"],
        "role": u["role"],
        "organization": u.get("organization", "PWS"),
        "department": u.get("department"),
        "status": u.get("status", "active"),
    }


DEACTIVATED_USER_STATUS = "deactivated"


def active_status_filter(include_deactivated: bool = False) -> dict:
    """Mongo filter: active login/roster records only (omit when include_deactivated)."""
    if include_deactivated:
        return {}
    return {"status": {"$ne": DEACTIVATED_USER_STATUS}}


def merge_mongo_query(*clauses: dict) -> dict:
    parts = [c for c in clauses if c]
    if not parts:
        return {}
    if len(parts) == 1:
        return parts[0]
    return {"$and": list(parts)}


def is_login_user_active(u: dict) -> bool:
    return (
        u.get("status", "active") != DEACTIVATED_USER_STATUS
        and u.get("is_active", True) is not False
    )


async def assert_assignable_user_ids(user_ids: list) -> None:
    """Reject task/assignment targets that are deactivated login accounts."""
    ids = list(dict.fromkeys(i for i in user_ids if i))
    if not ids:
        return
    inactive = await db.users.find(
        {"id": {"$in": ids}, "status": DEACTIVATED_USER_STATUS},
        {"_id": 0, "id": 1, "name": 1},
    ).to_list(len(ids))
    if inactive:
        names = ", ".join(u.get("name") or u["id"] for u in inactive)
        raise HTTPException(400, f"Cannot assign to inactive account(s): {names}")


async def get_current_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    token = auth[7:]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0})
    if not user:
        raise HTTPException(401, "User not found")
    if user.get("status") == "deactivated":
        raise HTTPException(403, "Account deactivated")
    if user.get("requires_user_type_review"):
        raise HTTPException(
            403,
            "Your account requires an approved user type assignment. Please contact the Super Admin.",
        )
    return user

def require_roles(*roles):
    async def dep(user: dict = Depends(get_current_user)):
        if user["role"] not in roles and user["role"] != "super_admin":
            raise HTTPException(403, f"Requires one of roles: {roles}")
        return user
    return dep

def is_admin(user: dict) -> bool:
    return user["role"] in ("admin", "super_admin")

def is_super_admin(user: dict) -> bool:
    if user.get("role") == "super_admin":
        return True
    try:
        from user_classification import resolve_user_type
        from rbac.enums import UserRole
        return resolve_user_type(user) == UserRole.SUPER_ADMIN.value
    except Exception:
        return False


def is_principal_user(user: dict) -> bool:
    """PWS Principal — not Vice Principal or generic PWS Admin without designation."""
    legacy = (user.get("role") or "").strip().lower()
    if legacy == "principal":
        return True
    designation = (user.get("designation") or "").strip().upper()
    return designation == "PRINCIPAL"


def normalize_aadhaar_number(raw: Optional[str]) -> str:
    return "".join(ch for ch in (raw or "") if ch.isalnum()).upper()


def mask_aadhaar_number(raw: Optional[str]) -> Optional[str]:
    normalized = normalize_aadhaar_number(raw)
    if len(normalized) != 12:
        return None
    return f"XXXX-XXXX-{normalized[-4:]}"


TEACHER_QUALIFICATIONS = frozenset({"B.Ed", "Bachelor's Degree", "Master's Degree", "Other"})

# ------------------ Permission System ------------------
PERMISSION_KEYS = [
    # Data Access
    "view_students", "view_players", "view_staff",
    # Attendance
    "mark_student_attendance", "mark_player_attendance", "mark_staff_attendance", "mark_coach_attendance",
    "mark_hostel_attendance", "view_attendance", "correct_attendance",
    # Management
    "add_players", "edit_players", "toggle_player_status",
    "add_students", "edit_students",
    # Admin
    "access_reports", "dashboard_access", "lifecycle_dashboard", "manage_users", "manage_users_rosters", "manage_academic_structure",
    "enter_academic_marks", "view_academic_marks",
    "enter_coach_assessments", "manage_coach_assessments", "view_coach_assessments",
    "enter_coach_assessments", "manage_coach_assessments", "view_coach_assessments",
    # Fees & Bulk
    "view_fees", "collect_fees", "edit_fees", "manage_fee_catalog", "bulk_upload", "approve_deactivation",
    "approve_requests", "supervise_tasks",
]

PERMISSION_GROUPS = {
    "Data Access": ["view_students", "view_players", "view_staff"],
    "Attendance": ["mark_student_attendance", "mark_player_attendance", "mark_staff_attendance", "mark_coach_attendance", "mark_hostel_attendance", "view_attendance", "correct_attendance"],
    "Management": ["add_players", "edit_players", "toggle_player_status", "add_students", "edit_students"],
    "Admin": ["access_reports", "dashboard_access", "lifecycle_dashboard", "manage_users", "manage_academic_structure", "enter_academic_marks", "view_academic_marks", "manage_coach_assessments", "enter_coach_assessments", "view_coach_assessments", "supervise_tasks", "approve_requests"],
    "Fees & Bulk": ["view_fees", "collect_fees", "edit_fees", "manage_fee_catalog", "bulk_upload", "approve_deactivation", "approve_requests"],
}


def default_permissions(role: str, coach_type: Optional[str] = None) -> dict:
    """Derive sensible defaults for a role so existing accounts keep working."""
    p = {k: False for k in PERMISSION_KEYS}
    if role in ("super_admin", "admin"):
        for k in PERMISSION_KEYS:
            p[k] = True
        if role == "admin":
            # Admins by default cannot edit fees or approve sensitive requests — super-admin levers
            p["edit_fees"] = False
            p["approve_deactivation"] = False
            p["approve_requests"] = False
        return p
    if role in ("principal", "vice_principal"):
        p.update({
            "view_students": True, "view_staff": True,
            "mark_student_attendance": True, "mark_staff_attendance": True,
            "add_students": True, "edit_students": True,
            "manage_academic_structure": True,
            "enter_academic_marks": True, "view_academic_marks": True,
            "view_fees": True, "collect_fees": True, "manage_fee_catalog": True,
            "access_reports": True, "dashboard_access": True, "lifecycle_dashboard": True,
            "view_attendance": True, "correct_attendance": True,
            "supervise_tasks": True,
        })
    elif role == "coach":
        # Head coach gets staff-attendance + edits; assistant coach is restricted. Coaches NEVER see fees.
        p.update({
            "view_players": True,
            "mark_player_attendance": True,
            "enter_coach_assessments": True,
            "dashboard_access": True,
        })
        if coach_type == "head":
            p.update({
                "view_staff": True,
                "mark_staff_attendance": True,
                "mark_coach_attendance": True,
                "add_players": True, "edit_players": True,
                "toggle_player_status": False,
                "access_reports": True,
                "view_attendance": True,
            })
    elif role == "teacher":
        p.update({
            "mark_student_attendance": True,
            "enter_academic_marks": True,
            "dashboard_access": True,
        })
    elif role == "warden":
        p.update({
            "view_students": True, "view_players": True, "dashboard_access": True,
            "mark_hostel_attendance": True, "view_attendance": True,
        })
    elif role in ("pws_accounts", "alpha_accounts"):
        p.update({
            "view_fees": True, "collect_fees": True, "dashboard_access": True,
            "access_reports": True, "supervise_tasks": True,
        })
        if role == "pws_accounts":
            p.update({"view_students": True, "add_students": True})
        else:
            p.update({"view_players": True, "add_players": True})
    elif role in ("student", "player"):
        p.update({"dashboard_access": True})
    elif role == "parent":
        # Parents are view-only on their own children — dashboard_access allows /api/parent/* endpoints.
        p.update({"dashboard_access": True, "view_coach_assessments": True})
    return p


PERMISSION_TEMPLATES = {
    "principal": {
        "label": "Principal / Vice Principal",
        "category": "Admin",
        "organization": "PWS",
        "permissions": default_permissions("principal"),
    },
    "head_coach": {
        "label": "Head Coach (ALPHA)",
        "category": "Admin",
        "organization": "ALPHA",
        "permissions": default_permissions("coach", "head"),
    },
    "assistant_coach": {
        "label": "Assistant Coach (ALPHA)",
        "category": "Employee",
        "organization": "ALPHA",
        "permissions": default_permissions("coach", "assistant"),
    },
    "teacher": {
        "label": "Teacher (PWS)",
        "category": "Employee",
        "organization": "PWS",
        "permissions": default_permissions("teacher"),
    },
}


def role_category(user: dict) -> str:
    """Derived label per spec: Sports Admin / Admin / Employee (not stored)."""
    if is_super_admin(user):
        return "Super Admin"
    role = user.get("role")
    coach_type = user.get("coach_type")
    if role == "admin":
        return "Sports Admin"
    if role in ("principal", "vice_principal"):
        return "Admin"
    if role == "coach" and coach_type == "head":
        return "Admin"
    return "Employee"


def role_display(role: str, user_type: Optional[str] = None, designation: Optional[str] = None) -> str:
    """Human-friendly label from canonical user type when available."""
    try:
        from user_classification import CATALOG_BY_CODE, resolve_user_type
        ut = user_type or resolve_user_type({"role": role, "user_type": user_type})
        if ut and ut in CATALOG_BY_CODE:
            label = CATALOG_BY_CODE[ut]["displayName"]
            if ut == "pws_admin" and designation:
                des = designation.replace("_", " ").title()
                return f"{label} · {des}"
            return label
    except Exception:
        pass
    return {
        "admin": "ALPHA Admin",
        "super_admin": "Super Admin",
        "principal": "PWS Admin · Principal",
        "vice_principal": "PWS Admin · Vice Principal",
        "pws_accounts": "PWS Accounts",
        "alpha_accounts": "ALPHA Accounts",
        "teacher": "PWS Teacher",
        "coach": "ALPHA Coach",
        "pws_admin": "PWS Admin",
        "alpha_admin": "ALPHA Admin",
        "pws_teacher": "PWS Teacher",
        "alpha_coach": "ALPHA Coach",
        "warden": "Warden",
        "staff": "Staff",
        "student": "Student",
        "player": "Player",
        "parent": "Parent",
    }.get(role or "", role or "")


def is_sports_admin(user: dict) -> bool:
    """Admin restricted to ALPHA-only operations."""
    return user.get("role") == "admin"


def get_perm(user: dict, key: str) -> bool:
    """Read effective permission — legacy map + RBAC role grants."""
    if is_super_admin(user):
        return True
    perms = user.get("permissions")
    if not perms:
        perms = default_permissions(user.get("role", ""), user.get("coach_type"))
    if perms.get(key):
        return True
    try:
        from rbac.bridge import LEGACY_KEY_TO_PERMISSIONS
        from rbac.authorization import has_permission
        for perm in LEGACY_KEY_TO_PERMISSIONS.get(key, ()):
            if has_permission(user, perm, use_legacy_fallback=False):
                return True
    except Exception:
        pass
    return False


def assert_perm(user: dict, key: str) -> None:
    if not get_perm(user, key):
        raise HTTPException(403, f"Permission '{key}' is not granted for your account")


def has_any_manage_rights(user: dict) -> bool:
    return is_admin(user) or bool(user.get("can_manage"))

def assert_can_manage(user: dict, kind: str) -> None:
    if is_admin(user):
        return
    if kind not in MANAGE_KINDS:
        raise HTTPException(400, "Invalid kind")
    if kind in (user.get("can_manage") or []):
        return
    raise HTTPException(403, f"You don't have permission to manage {kind}s")

def coach_can(user: dict, action: str) -> bool:
    """For player CRUD — RBAC MANAGE_PLAYERS + legacy coach_permissions."""
    try:
        from rbac.guards import can_manage_player_action
        return can_manage_player_action(user, action)
    except Exception:
        if is_admin(user):
            return True
        if user.get("role") != "coach":
            return False
        perm_map = {"view": "view_players", "add": "add_players", "edit": "edit_players"}
        return perm_map.get(action, "") in (user.get("coach_permissions") or [])

def assert_player_action(user: dict, action: str) -> None:
    if not coach_can(user, action):
        raise HTTPException(403, f"You don't have permission to {action} players")


# ------------------ Entity foundation (PWS / ALPHA / Both) ------------------
INSTITUTIONS = ("PWS", "ALPHA", "BOTH")
FEE_ENTITIES = ("pws", "alpha")


def resolve_user_institution(user: dict, requested: Optional[str] = None) -> str:
    """Effective institution scope for list/query endpoints."""
    if is_super_admin(user):
        v = (requested or "BOTH").upper()
        return v if v in INSTITUTIONS else "BOTH"
    if is_sports_admin(user) or user.get("role") == "coach":
        return "ALPHA"
    if user.get("role") in ("principal", "vice_principal", "teacher"):
        return "PWS"
    if user.get("role") == "warden":
        return "BOTH"
    org = (user.get("organization") or "PWS").upper()
    if org == "BOTH":
        v = (requested or "BOTH").upper()
        return v if v in INSTITUTIONS else "BOTH"
    return org if org in ("PWS", "ALPHA") else "PWS"


def institution_to_fee_entity(inst: str) -> Optional[str]:
    if inst == "PWS":
        return "pws"
    if inst == "ALPHA":
        return "alpha"
    return None


def fee_entity_filter(inst: str) -> dict:
    """Mongo filter for fees collection (lowercase entity_id)."""
    if inst == "PWS":
        return {"entity_id": "pws"}
    if inst == "ALPHA":
        return {"$or": [{"entity_id": "alpha"}, {"entity_id": {"$exists": False}}]}
    return {}


def derive_person_entities(person: dict) -> List[str]:
    """Compute entity participation for a person record."""
    raw = person.get("entities") or []
    cleaned = sorted({str(e).upper() for e in raw if str(e).upper() in ("PWS", "ALPHA")})
    if cleaned:
        return cleaned
    org = (person.get("organization") or "").upper()
    if org == "BOTH":
        return ["PWS", "ALPHA"]
    kind = person.get("kind")
    if kind in ("student", "teacher"):
        return ["PWS"] if org in ("", "PWS", "BOTH") else [org]
    if kind in ("player", "coach"):
        return ["ALPHA"] if org in ("", "ALPHA", "BOTH") else [org]
    if org in ("PWS", "ALPHA"):
        return [org]
    return ["PWS"]


def person_entity_filter(inst: str) -> dict:
    """Mongo filter: persons participating in the given institution."""
    if inst == "BOTH":
        return {}
    pws_legacy = {
        "entities": {"$exists": False},
        "$or": [
            {"organization": {"$in": ["PWS", "BOTH"]}},
            {"kind": {"$in": ["student", "teacher"]}},
        ],
    }
    alpha_legacy = {
        "entities": {"$exists": False},
        "$or": [
            {"organization": "ALPHA"},
            {"kind": {"$in": ["player", "coach"]}},
        ],
    }
    if inst == "PWS":
        return {"$or": [{"entities": "PWS"}, {"entities": {"$in": ["PWS"]}}, pws_legacy]}
    return {"$or": [{"entities": "ALPHA"}, {"entities": {"$in": ["ALPHA"]}}, alpha_legacy]}


def person_participates_in(person: dict, inst: str) -> bool:
    if inst == "BOTH":
        return True
    return inst in derive_person_entities(person)


def assert_person_entity_access(user: dict, person: dict) -> None:
    """Raise 404 if user cannot see this person (entity isolation)."""
    inst = resolve_user_institution(user)
    if inst == "BOTH":
        return
    if not person_participates_in(person, inst):
        raise HTTPException(404, "Person not found")
    if is_sports_admin(user) and person.get("kind") in ("student", "teacher"):
        raise HTTPException(404, "Person not found")


def attendance_entity_for_kind(kind: Optional[str]) -> Optional[str]:
    if kind == "student":
        return "pws"
    if kind in ("player", "coach"):
        return "alpha"
    if kind == "hostel":
        return "both"
    return None


def attendance_entity_filter(inst: str) -> dict:
    """Mongo filter for attendance records."""
    if inst == "BOTH":
        return {}
    ent = institution_to_fee_entity(inst)
    if not ent:
        return {}
    legacy_pws = {"entity_id": {"$exists": False}, "kind": "student"}
    legacy_alpha = {"entity_id": {"$exists": False}, "kind": {"$in": ["player", "coach"]}}
    if inst == "PWS":
        return {"$or": [{"entity_id": "pws"}, legacy_pws]}
    return {"$or": [{"entity_id": "alpha"}, legacy_alpha]}



# ------------------ Notifications (delegates to notifications_service) ------------------
from notifications_service import (
    NOTIFICATION_TYPES,
    normalize_notification,
    notification_filter_for_user,
    send_notification,
    send_to_role,
    unread_count_for_user,
)


async def notify_user(
    user_id: str,
    *,
    ntype: str,
    title: str,
    message: str,
    ref_id: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> str:
    return await send_notification(
        user_id,
        ntype=ntype,
        title=title,
        message=message,
        ref_id=ref_id,
        entity_id=entity_id,
    )


async def notify_role(
    role: str,
    *,
    ntype: str,
    title: str,
    message: str,
    ref_id: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> int:
    return await send_to_role(
        role,
        ntype=ntype,
        title=title,
        message=message,
        ref_id=ref_id,
        entity_id=entity_id,
    )


def resolve_user_type_safe(u: dict) -> Optional[str]:
    try:
        from user_classification import resolve_user_type
        return resolve_user_type(u)
    except Exception:
        return None


def public_user(u: dict) -> dict:
    perms = u.get("permissions") or default_permissions(u.get("role", ""), u.get("coach_type"))
    rbac_effective: list[str] = []
    role_canonical = u.get("role", "")
    try:
        from rbac.authorization import list_effective_permissions, normalize_role
        from rbac.guards import effective_permissions_list
        rbac_effective = effective_permissions_list(u)
        role_canonical = normalize_role(u.get("role", "")).value
    except Exception:
        pass
    out = {
        "id": u["id"],
        "email": u.get("email"),
        "name": u["name"],
        "role": u["role"],
        "role_canonical": role_canonical,
        "role_display": role_display(u["role"], u.get("user_type"), u.get("designation")),
        "role_category": role_category(u),
        "organization": u.get("organization", "PWS"),
        "department": u.get("department"),
        "phone": u.get("phone"),
        "can_manage": u.get("can_manage", []),
        "coach_permissions": u.get("coach_permissions", []),
        "coach_type": u.get("coach_type"),
        "assigned_sport": u.get("assigned_sport"),
        "assigned_centres": u.get("assigned_centres", []),
        "assigned_sports": u.get("assigned_sports", []),
        "linked_person_ids": u.get("linked_person_ids", []),
        "mobile": u.get("mobile"),
        "is_password_set": bool(u.get("is_password_set", bool(u.get("password_hash")))),
        "must_change_password": bool(u.get("must_change_password", False)),
        "status": u.get("status", "active"),
        "is_active": u.get("status", "active") == "active" and u.get("is_active", True) is not False,
        "person_id": u.get("person_id"),
        "permissions": perms,
        "permissions_rbac": u.get("permissions_rbac") or {},
        "effective_permissions": rbac_effective,
        "created_at": u.get("created_at"),
        "sport_assignment_status": u.get("sport_assignment_status"),
        "user_type": u.get("user_type") or resolve_user_type_safe(u),
        "designation": u.get("designation"),
        "teacher_designation": u.get("teacher_designation"),
        "date_of_joining": u.get("date_of_joining"),
        "date_of_birth": u.get("date_of_birth"),
        "address": u.get("address"),
        "personal_email": u.get("personal_email"),
        "qualification": u.get("qualification"),
        "qualification_other": u.get("qualification_other"),
        "last_job": u.get("last_job"),
        "guardian_name": u.get("guardian_name"),
        "guardian_mobile": u.get("guardian_mobile"),
        "reference_name": u.get("reference_name"),
        "reference_mobile": u.get("reference_mobile"),
        "has_login_account": bool(u.get("has_login_account", u.get("email"))),
        "aadhaar_number_masked": mask_aadhaar_number(u.get("aadhaar_number")),
        "entity_scope": u.get("entity_scope"),
        "legacy_role": u.get("legacy_role"),
        "requires_user_type_review": bool(u.get("requires_user_type_review")),
    }
    try:
        from user_classification import CATALOG_BY_CODE
        ut = out.get("user_type")
        if ut and ut in CATALOG_BY_CODE:
            out["user_type_display"] = CATALOG_BY_CODE[ut]["displayName"]
            out["user_type_meta"] = CATALOG_BY_CODE[ut]
    except Exception:
        pass
    if u.get("role") == "coach":
        try:
            from coach_scope import resolve_coach_data_scope
            out["coach_scope"] = resolve_coach_data_scope(u)
        except Exception:
            pass
    return out

# ------------------ Models ------------------
class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict

class UserCreate(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    name: str
    mobile: Optional[str] = None
    user_type: Literal[
        "super_admin", "pws_admin", "alpha_admin",
        "pws_accounts", "alpha_accounts", "pws_teacher", "alpha_coach",
    ]
    designation: Optional[Literal["PRINCIPAL", "VICE_PRINCIPAL"]] = None
    role: Optional[str] = None  # ignored — derived from user_type
    organization: Optional[Literal["PWS", "ALPHA", "BOTH"]] = None
    department: Optional[str] = None
    phone: Optional[str] = None
    can_manage: List[Literal["student", "player", "teacher", "coach", "staff"]] = []
    coach_permissions: List[Literal["view_players", "add_players", "edit_players"]] = []
    coach_type: Optional[Literal["head", "assistant"]] = None
    assigned_sport: Optional[str] = None
    assigned_centres: List[Literal["Balua", "Harding Park"]] = []
    assigned_sports: List[Literal["Cricket", "Football"]] = []
    linked_person_ids: List[str] = []
    permissions: Optional[dict] = None
    date_of_joining: Optional[str] = None
    address: Optional[str] = None
    teacher_designation: Optional[Literal["CLASS_TEACHER", "TEACHER"]] = None

class DirectoryTeacherCreate(BaseModel):
    name: str
    date_of_birth: str
    address: str
    mobile: str
    personal_email: EmailStr
    aadhaar_number: str
    qualification: Literal["B.Ed", "Bachelor's Degree", "Master's Degree", "Other"]
    qualification_other: Optional[str] = None
    last_job: str
    guardian_name: str
    guardian_mobile: str
    reference_name: str
    reference_mobile: str

    @field_validator("name", "address", "last_job", "guardian_name", "reference_name")
    @classmethod
    def _strip_required_text(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("This field is required")
        return s

    @field_validator("aadhaar_number")
    @classmethod
    def _validate_aadhaar(cls, v: str) -> str:
        normalized = normalize_aadhaar_number(v)
        if len(normalized) != 12:
            raise ValueError("Aadhaar number must be exactly 12 alphanumeric characters")
        return normalized

    @field_validator("date_of_birth")
    @classmethod
    def _validate_dob(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("Date of birth is required")
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return s
        import re
        m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", s)
        if not m:
            raise ValueError("Date of birth must be YYYY-MM-DD or DD/MM/YYYY")
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    @model_validator(mode="after")
    def _validate_qualification_other(self):
        if self.qualification == "Other":
            other = (self.qualification_other or "").strip()
            if not other:
                raise ValueError("Specify qualification when Other is selected")
            self.qualification_other = other
        else:
            self.qualification_other = None
        return self

class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    mobile: Optional[str] = None
    user_type: Optional[Literal[
        "super_admin", "pws_admin", "alpha_admin",
        "pws_accounts", "alpha_accounts", "pws_teacher", "alpha_coach",
    ]] = None
    designation: Optional[Literal["PRINCIPAL", "VICE_PRINCIPAL"]] = None
    role: Optional[str] = None  # ignored when user_type supplied
    organization: Optional[Literal["PWS", "ALPHA", "BOTH"]] = None
    department: Optional[str] = None
    phone: Optional[str] = None
    password: Optional[str] = None
    can_manage: Optional[List[Literal["student", "player", "teacher", "coach", "staff"]]] = None
    coach_permissions: Optional[List[Literal["view_players", "add_players", "edit_players"]]] = None
    coach_type: Optional[Literal["head", "assistant"]] = None
    assigned_sport: Optional[str] = None
    assigned_centres: Optional[List[Literal["Balua", "Harding Park"]]] = None
    assigned_sports: Optional[List[Literal["Cricket", "Football"]]] = None
    linked_person_ids: Optional[List[str]] = None
    permissions: Optional[dict] = None
    date_of_joining: Optional[str] = None
    date_of_birth: Optional[str] = None
    address: Optional[str] = None
    personal_email: Optional[EmailStr] = None
    aadhaar_number: Optional[str] = None
    qualification: Optional[Literal["B.Ed", "Bachelor's Degree", "Master's Degree", "Other"]] = None
    qualification_other: Optional[str] = None
    last_job: Optional[str] = None
    guardian_name: Optional[str] = None
    guardian_mobile: Optional[str] = None
    reference_name: Optional[str] = None
    reference_mobile: Optional[str] = None
    teacher_designation: Optional[Literal["CLASS_TEACHER", "TEACHER"]] = None

class PersonCreate(BaseModel):
    name: str
    kind: Literal["student", "player", "teacher", "coach", "staff"]
    group: Optional[str] = None
    section_id: Optional[str] = None
    sport: Optional[str] = None
    organization: Literal["PWS", "ALPHA", "BOTH"] = "PWS"
    entities: List[Literal["PWS", "ALPHA"]] = []
    is_resident: bool = False
    # Student enrollment
    admission_number: Optional[str] = None
    roll_number: Optional[str] = None
    gender: Optional[Literal["Male", "Female", "Other"]] = None
    email: Optional[str] = None
    address: Optional[str] = None
    guardian_name: Optional[str] = None
    guardian_phone: Optional[str] = None
    father_name: Optional[str] = None
    mother_name: Optional[str] = None
    age: Optional[int] = None
    dob: Optional[str] = None  # ISO date YYYY-MM-DD (auto-computes age)
    skill_level: Optional[Literal["Beginner", "Intermediate", "Advanced"]] = None
    mobile: Optional[str] = None
    locality: Optional[str] = None
    city: Optional[str] = None
    slot: Optional[Literal["Morning", "Evening", "Both"]] = None
    player_id: Optional[str] = None
    assigned_coach_id: Optional[str] = None  # deprecated — players are centre-based
    centre: Optional[Literal["Balua", "Harding Park"]] = None
    player_type: Optional[Literal["Daily", "Day Boarding", "Hostel", "Hostel Only", "Boarding"]] = None
    date_of_admission: Optional[str] = None  # ISO date string YYYY-MM-DD
    status: Literal["active", "deactivated"] = "active"
    parent_user_ids: List[str] = []  # parent users linked to this child
    transport_fee_monthly: Optional[int] = 0   # ALPHA player optional monthly transport
    # PWS 2026-27 fee profile
    pws_student_type: Optional[Literal["Day School", "Boarding", "Day Boarding"]] = None
    pws_class: Optional[str] = None
    transport_enabled: bool = False
    transport_distance: Optional[Literal["Up to 5 km", "Over 5 km"]] = None
    pws_fee_overrides: Optional[Dict[str, int]] = None
    hostel_fee_override: Optional[int] = None  # ALPHA hostel player optional manual override
    monthly_fee_override: Optional[int] = None  # Super Admin only — override rate-card monthly at admission
    registration_fee_override: Optional[int] = None  # Super Admin only — override rate-card registration
    # Staff
    employee_id: Optional[str] = None
    department: Optional[str] = None

class PersonUpdate(BaseModel):
    name: Optional[str] = None
    group: Optional[str] = None
    section_id: Optional[str] = None
    sport: Optional[str] = None
    organization: Optional[Literal["PWS", "ALPHA", "BOTH"]] = None
    entities: Optional[List[Literal["PWS", "ALPHA"]]] = None
    is_resident: Optional[bool] = None
    admission_number: Optional[str] = None
    roll_number: Optional[str] = None
    gender: Optional[Literal["Male", "Female", "Other"]] = None
    email: Optional[str] = None
    address: Optional[str] = None
    guardian_name: Optional[str] = None
    guardian_phone: Optional[str] = None
    father_name: Optional[str] = None
    mother_name: Optional[str] = None
    age: Optional[int] = None
    dob: Optional[str] = None
    skill_level: Optional[Literal["Beginner", "Intermediate", "Advanced"]] = None
    mobile: Optional[str] = None
    locality: Optional[str] = None
    city: Optional[str] = None
    slot: Optional[Literal["Morning", "Evening", "Both"]] = None
    player_id: Optional[str] = None
    assigned_coach_id: Optional[str] = None  # deprecated
    centre: Optional[Literal["Balua", "Harding Park"]] = None
    player_type: Optional[Literal["Daily", "Day Boarding", "Hostel", "Hostel Only", "Boarding"]] = None
    date_of_admission: Optional[str] = None
    status: Optional[Literal["active", "deactivated"]] = None
    parent_user_ids: Optional[List[str]] = None
    transport_fee_monthly: Optional[int] = None
    pws_student_type: Optional[Literal["Day School", "Boarding", "Day Boarding"]] = None
    pws_class: Optional[str] = None
    transport_enabled: Optional[bool] = None
    transport_distance: Optional[Literal["Up to 5 km", "Over 5 km"]] = None
    pws_fee_overrides: Optional[Dict[str, int]] = None
    hostel_fee_override: Optional[int] = None
    monthly_fee_override: Optional[int] = None
    registration_fee_override: Optional[int] = None
    employee_id: Optional[str] = None
    department: Optional[str] = None

TASK_STATUSES = ("open", "in_progress", "blocked", "completed", "cancelled")
TASK_STATUS_ALIASES = {
    "assigned": "open",
    "delayed": "blocked",
    "reviewed": "completed",
}

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    entity_id: Optional[Literal["pws", "alpha", "both"]] = None
    priority: Literal["low", "medium", "high"] = "medium"
    due_date: Optional[datetime] = None
    deadline: Optional[datetime] = None  # legacy alias for due_date
    assignee_id: Optional[str] = None
    assignee_ids: List[str] = []
    department: Optional[str] = None
    follow_up_required: bool = False

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    entity_id: Optional[Literal["pws", "alpha", "both"]] = None
    priority: Optional[Literal["low", "medium", "high"]] = None
    due_date: Optional[datetime] = None
    deadline: Optional[datetime] = None
    assignee_id: Optional[str] = None
    assignee_ids: Optional[List[str]] = None
    status: Optional[Literal["open", "in_progress", "blocked", "completed", "cancelled", "assigned", "delayed", "reviewed"]] = None
    completion_remark: Optional[str] = None
    proof_url: Optional[str] = None
    follow_up_required: Optional[bool] = None

class CommentIn(BaseModel):
    text: str

class AttendanceMark(BaseModel):
    person_id: str
    status: Literal["present", "absent", "late", "leave"]

class AttendanceBatch(BaseModel):
    date: str
    kind: Literal["student", "player", "teacher", "coach", "staff"]
    group: Optional[str] = None
    section_id: Optional[str] = None
    sport: Optional[str] = None
    session: Optional[str] = "morning"
    marks: List[AttendanceMark]

class AttendanceCorrectionIn(BaseModel):
    record_id: str
    status: Literal["present", "absent", "late", "leave"]
    reason: str

class GatePassCreate(BaseModel):
    resident_id: str
    reason: str
    out_time: datetime
    expected_return: datetime
    destination: Optional[str] = None

class GatePassDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    note: Optional[str] = None

class RollCallEntry(BaseModel):
    resident_id: str
    present: bool
    note: Optional[str] = None

class RollCallIn(BaseModel):
    date: str
    session: Literal["morning", "night", "evening"]
    entries: List[RollCallEntry]
