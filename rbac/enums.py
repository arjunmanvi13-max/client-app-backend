"""Canonical RBAC enums — roles, business entities, and permissions."""
from enum import Enum


class BusinessEntity(str, Enum):
    """Which business line a user or record belongs to."""

    PWS = "PWS"
    ALPHA = "ALPHA"
    BOTH = "BOTH"


class UserRole(str, Enum):
    """
    Canonical roles from the SportsSchool OS RBAC spec.

    Legacy DB values (principal, admin, teacher, coach, …) are normalized via
    `normalize_role()` in authorization.py.
    """

    SUPER_ADMIN = "super_admin"

    # Admin (entity-scoped)
    PWS_ADMIN = "pws_admin"           # Principal / Vice Principal
    ALPHA_ADMIN = "alpha_admin"       # Sports / ALPHA admin

    # Accounts (entity-scoped)
    PWS_ACCOUNTS = "pws_accounts"
    ALPHA_ACCOUNTS = "alpha_accounts"

    # Operational staff
    PWS_TEACHER = "pws_teacher"
    ALPHA_COACH = "alpha_coach"

    # Portal / roster roles (no staff permissions in spec)
    WARDEN = "warden"
    STAFF = "staff"
    STUDENT = "student"
    PLAYER = "player"
    PARENT = "parent"


class Permission(str, Enum):
    """Fine-grained permissions from the RBAC specification."""

    # --- Super Admin ---
    CREATE_USERS = "CREATE_USERS"
    MANAGE_FEES_HEADS = "MANAGE_FEES_HEADS"
    MANAGE_ACCESS = "MANAGE_ACCESS"
    BULK_UPLOAD_USERS = "BULK_UPLOAD_USERS"
    TOGGLE_USER_STATUS = "TOGGLE_USER_STATUS"
    ADD_COACHES = "ADD_COACHES"
    ADD_NEW_TEACHER = "ADD_NEW_TEACHER"
    MANAGE_USERS_ROSTERS = "MANAGE_USERS_ROSTERS"

    # --- PWS Admin ---
    MARK_PWS_ATTENDANCE = "MARK_PWS_ATTENDANCE"
    MANAGE_TEACHERS_MAP_SUBJECTS = "MANAGE_TEACHERS_MAP_SUBJECTS"
    CREATE_TEACHER_TASKS = "CREATE_TEACHER_TASKS"
    MANAGE_TEACHERS_MAP_SECTIONS = "MANAGE_TEACHERS_MAP_SECTIONS"

    # --- ALPHA Admin ---
    MARK_ALPHA_ATTENDANCE = "MARK_ALPHA_ATTENDANCE"
    MANAGE_COACHES = "MANAGE_COACHES"
    CREATE_COACH_TASKS = "CREATE_COACH_TASKS"
    MANAGE_PLAYERS = "MANAGE_PLAYERS"

    # --- PWS Accounts ---
    COLLECT_PWS_FEES = "COLLECT_PWS_FEES"
    MANAGE_PWS_TASKS = "MANAGE_PWS_TASKS"
    ADD_PWS_STUDENTS = "ADD_PWS_STUDENTS"
    RUN_PWS_REPORTS = "RUN_PWS_REPORTS"

    # --- ALPHA Accounts ---
    COLLECT_ALPHA_FEES = "COLLECT_ALPHA_FEES"
    MANAGE_ALPHA_TASKS = "MANAGE_ALPHA_TASKS"
    ADD_ALPHA_PLAYERS = "ADD_ALPHA_PLAYERS"
    RUN_ALPHA_REPORTS = "RUN_ALPHA_REPORTS"

    # --- PWS Teachers ---
    MARK_STUDENT_ATTENDANCE = "MARK_STUDENT_ATTENDANCE"
    MANAGE_MARKS_ASSESSMENT = "MANAGE_MARKS_ASSESSMENT"
    MANAGE_TEACHER_TASKS = "MANAGE_TEACHER_TASKS"

    # --- ALPHA Coaches ---
    MARK_PLAYER_ATTENDANCE = "MARK_PLAYER_ATTENDANCE"
    MANAGE_PLAYER_ASSESSMENT = "MANAGE_PLAYER_ASSESSMENT"
    MANAGE_COACH_TASKS = "MANAGE_COACH_TASKS"

    # --- Extended (legacy keys bridged in production) ---
    CORRECT_ATTENDANCE = "CORRECT_ATTENDANCE"
    MARK_HOSTEL_ATTENDANCE = "MARK_HOSTEL_ATTENDANCE"
    MANAGE_COACH_ASSESSMENTS_ADMIN = "MANAGE_COACH_ASSESSMENTS_ADMIN"
    APPROVE_REQUESTS = "APPROVE_REQUESTS"
    VIEW_ATTENDANCE = "VIEW_ATTENDANCE"

    # --- Shared read / dashboard (used across roles) ---
    DASHBOARD_ACCESS = "DASHBOARD_ACCESS"


# Maps stored MongoDB role strings → canonical UserRole
LEGACY_ROLE_ALIASES: dict[str, UserRole] = {
    "super_admin": UserRole.SUPER_ADMIN,
    "admin": UserRole.ALPHA_ADMIN,
    "principal": UserRole.PWS_ADMIN,
    "vice_principal": UserRole.PWS_ADMIN,
    "teacher": UserRole.PWS_TEACHER,
    "coach": UserRole.ALPHA_COACH,
    "warden": UserRole.WARDEN,
    "staff": UserRole.STAFF,
    "student": UserRole.STUDENT,
    "player": UserRole.PLAYER,
    "parent": UserRole.PARENT,
    # Canonical values map to themselves
    "pws_admin": UserRole.PWS_ADMIN,
    "alpha_admin": UserRole.ALPHA_ADMIN,
    "pws_accounts": UserRole.PWS_ACCOUNTS,
    "alpha_accounts": UserRole.ALPHA_ACCOUNTS,
    "pws_teacher": UserRole.PWS_TEACHER,
    "alpha_coach": UserRole.ALPHA_COACH,
}


def default_entity_for_role(role: UserRole) -> BusinessEntity:
    """Primary business entity for a canonical role."""
    if role == UserRole.SUPER_ADMIN:
        return BusinessEntity.BOTH
    if role in (
        UserRole.PWS_ADMIN,
        UserRole.PWS_ACCOUNTS,
        UserRole.PWS_TEACHER,
    ):
        return BusinessEntity.PWS
    if role in (
        UserRole.ALPHA_ADMIN,
        UserRole.ALPHA_ACCOUNTS,
        UserRole.ALPHA_COACH,
    ):
        return BusinessEntity.ALPHA
    return BusinessEntity.PWS
