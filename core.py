"""Shared core utilities: DB connection, security, models, dependencies."""
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Literal

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr

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

ROLES = ["super_admin", "admin", "principal", "vice_principal", "teacher", "coach", "warden", "staff", "student", "player", "parent"]
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
    }

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
    return user["role"] == "super_admin"

# ------------------ Permission System ------------------
PERMISSION_KEYS = [
    # Data Access
    "view_students", "view_players", "view_staff",
    # Attendance
    "mark_student_attendance", "mark_player_attendance", "mark_staff_attendance", "mark_coach_attendance",
    # Management
    "add_players", "edit_players", "toggle_player_status",
    "add_students", "edit_students",
    # Admin
    "access_reports", "dashboard_access", "lifecycle_dashboard", "manage_users",
    # Fees & Bulk
    "view_fees", "collect_fees", "edit_fees", "bulk_upload", "approve_deactivation",
]

PERMISSION_GROUPS = {
    "Data Access": ["view_students", "view_players", "view_staff"],
    "Attendance": ["mark_student_attendance", "mark_player_attendance", "mark_staff_attendance", "mark_coach_attendance"],
    "Management": ["add_players", "edit_players", "toggle_player_status", "add_students", "edit_students"],
    "Admin": ["access_reports", "dashboard_access", "lifecycle_dashboard", "manage_users"],
    "Fees & Bulk": ["view_fees", "collect_fees", "edit_fees", "bulk_upload", "approve_deactivation"],
}


def default_permissions(role: str, coach_type: Optional[str] = None) -> dict:
    """Derive sensible defaults for a role so existing accounts keep working."""
    p = {k: False for k in PERMISSION_KEYS}
    if role in ("super_admin", "admin"):
        for k in PERMISSION_KEYS:
            p[k] = True
        if role == "admin":
            # Admins by default cannot edit fees or approve deactivation — those are super-admin levers
            p["edit_fees"] = False
            p["approve_deactivation"] = False
        return p
    if role in ("principal", "vice_principal"):
        p.update({
            "view_students": True, "view_staff": True,
            "mark_student_attendance": True, "mark_staff_attendance": True,
            "add_students": True, "edit_students": True,
            "access_reports": True, "dashboard_access": True, "lifecycle_dashboard": True,
        })
    elif role == "coach":
        # Head coach gets staff-attendance + edits; assistant coach is restricted. Coaches NEVER see fees.
        p.update({
            "view_players": True,
            "mark_player_attendance": True,
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
            })
    elif role == "teacher":
        p.update({
            "view_students": True,
            "mark_student_attendance": True,
            "dashboard_access": True,
        })
    elif role == "warden":
        p.update({"view_students": True, "view_players": True, "dashboard_access": True})
    elif role in ("student", "player"):
        p.update({"dashboard_access": True})
    elif role == "parent":
        # Parents are view-only on their own children — dashboard_access allows /api/parent/* endpoints.
        p.update({"dashboard_access": True})
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


def role_display(role: str) -> str:
    """Human-friendly label. admin -> Sports Admin (per ALPHA-restriction spec)."""
    return {
        "admin": "Sports Admin",
        "super_admin": "Super Admin",
        "principal": "Principal",
        "vice_principal": "Vice Principal",
        "teacher": "Teacher",
        "coach": "Coach",
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
    """Read effective permission. Super admin always True; otherwise stored map (with default fallback)."""
    if is_super_admin(user):
        return True
    perms = user.get("permissions")
    if not perms:
        perms = default_permissions(user.get("role", ""), user.get("coach_type"))
    return bool(perms.get(key, False))


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
    """For player CRUD, coaches need granular permission. action in {'view','add','edit'}."""
    if is_admin(user):
        return True
    if user.get("role") != "coach":
        return False
    perm_map = {"view": "view_players", "add": "add_players", "edit": "edit_players"}
    return perm_map.get(action, "") in (user.get("coach_permissions") or [])

def assert_player_action(user: dict, action: str) -> None:
    if not coach_can(user, action):
        raise HTTPException(403, f"You don't have permission to {action} players")

def public_user(u: dict) -> dict:
    perms = u.get("permissions") or default_permissions(u.get("role", ""), u.get("coach_type"))
    return {
        "id": u["id"],
        "email": u.get("email"),
        "name": u["name"],
        "role": u["role"],
        "role_display": role_display(u["role"]),
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
        "person_id": u.get("person_id"),
        "permissions": perms,
        "created_at": u.get("created_at"),
    }

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
    mobile: Optional[str] = None     # 10-digit Indian mobile, login id
    role: Literal["super_admin", "admin", "principal", "vice_principal", "teacher", "coach", "warden", "student", "player", "parent"]
    organization: Literal["PWS", "ALPHA", "BOTH"] = "PWS"
    department: Optional[str] = None
    phone: Optional[str] = None
    can_manage: List[Literal["student", "player", "teacher", "coach", "staff"]] = []
    coach_permissions: List[Literal["view_players", "add_players", "edit_players"]] = []
    coach_type: Optional[Literal["head", "assistant"]] = None
    assigned_sport: Optional[str] = None
    assigned_centres: List[Literal["Balua", "Harding Park"]] = []
    assigned_sports: List[Literal["Cricket", "Football"]] = []
    linked_person_ids: List[str] = []  # used by parent role
    permissions: Optional[dict] = None  # tick-box permission map at creation (admins only)

class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    mobile: Optional[str] = None
    role: Optional[Literal["super_admin", "admin", "principal", "vice_principal", "teacher", "coach", "warden", "student", "player", "parent"]] = None
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

class PersonCreate(BaseModel):
    name: str
    kind: Literal["student", "player", "teacher", "coach", "staff"]
    group: Optional[str] = None
    sport: Optional[str] = None
    organization: Literal["PWS", "ALPHA", "BOTH"] = "PWS"
    is_resident: bool = False
    father_name: Optional[str] = None
    age: Optional[int] = None
    dob: Optional[str] = None  # ISO date YYYY-MM-DD (auto-computes age)
    skill_level: Optional[Literal["Beginner", "Intermediate", "Advanced"]] = None
    mobile: Optional[str] = None
    locality: Optional[str] = None
    city: Optional[str] = None
    slot: Optional[Literal["Morning", "Evening", "Both"]] = None
    assigned_coach_id: Optional[str] = None  # deprecated — players are centre-based
    centre: Optional[Literal["Balua", "Harding Park"]] = None
    player_type: Optional[Literal["Daily", "Day Boarding", "Hostel", "Hostel Only", "Boarding"]] = None
    date_of_admission: Optional[str] = None  # ISO date string YYYY-MM-DD
    status: Literal["active", "deactivated"] = "active"
    parent_user_ids: List[str] = []  # parent users linked to this child
    transport_fee_monthly: Optional[int] = 0   # ALPHA player optional monthly transport
    hostel_fee_override: Optional[int] = None  # ALPHA hostel player optional manual override
    monthly_fee_override: Optional[int] = None  # Super Admin only — override rate-card monthly at admission
    registration_fee_override: Optional[int] = None  # Super Admin only — override rate-card registration

class PersonUpdate(BaseModel):
    name: Optional[str] = None
    group: Optional[str] = None
    sport: Optional[str] = None
    organization: Optional[Literal["PWS", "ALPHA", "BOTH"]] = None
    is_resident: Optional[bool] = None
    father_name: Optional[str] = None
    age: Optional[int] = None
    dob: Optional[str] = None
    skill_level: Optional[Literal["Beginner", "Intermediate", "Advanced"]] = None
    mobile: Optional[str] = None
    locality: Optional[str] = None
    city: Optional[str] = None
    slot: Optional[Literal["Morning", "Evening", "Both"]] = None
    assigned_coach_id: Optional[str] = None  # deprecated
    centre: Optional[Literal["Balua", "Harding Park"]] = None
    player_type: Optional[Literal["Daily", "Day Boarding", "Hostel", "Hostel Only", "Boarding"]] = None
    date_of_admission: Optional[str] = None
    status: Optional[Literal["active", "deactivated"]] = None
    parent_user_ids: Optional[List[str]] = None
    transport_fee_monthly: Optional[int] = None
    hostel_fee_override: Optional[int] = None
    monthly_fee_override: Optional[int] = None
    registration_fee_override: Optional[int] = None

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    priority: Literal["low", "medium", "high"] = "medium"
    deadline: Optional[datetime] = None
    assignee_ids: List[str] = []
    department: Optional[str] = None
    follow_up_required: bool = False

class TaskUpdate(BaseModel):
    status: Optional[Literal["assigned", "in_progress", "completed", "delayed", "reviewed"]] = None
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
    sport: Optional[str] = None
    session: Optional[str] = None
    marks: List[AttendanceMark]

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
    session: Literal["morning", "night"]
    entries: List[RollCallEntry]
