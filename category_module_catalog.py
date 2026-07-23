"""Module catalog for category-based access control — mirrors sidebar navigation groups."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from rbac.enums import Permission, UserRole
from user_classification import APPROVED_LOGIN_USER_TYPES

# Super Admin retains full access; configuration UI is read-only for this type.
LOCKED_USER_TYPES = frozenset({UserRole.SUPER_ADMIN.value})

PWS_TYPES = frozenset({
    UserRole.PWS_ADMIN.value,
    UserRole.PWS_ACCOUNTS.value,
    UserRole.PWS_TEACHER.value,
})
ALPHA_TYPES = frozenset({
    UserRole.ALPHA_ADMIN.value,
    UserRole.ALPHA_ACCOUNTS.value,
    UserRole.ALPHA_COACH.value,
})

ModuleDef = Dict[str, Any]


def _mod(
    mid: str,
    label: str,
    *,
    permission_keys: Optional[List[str]] = None,
    rbac: Optional[List[str]] = None,
    user_types: Optional[List[str]] = None,
    pws_only: bool = False,
    alpha_only: bool = False,
    children: Optional[List[ModuleDef]] = None,
) -> ModuleDef:
    out: ModuleDef = {
        "id": mid,
        "label": label,
        "permission_keys": permission_keys or [],
        "rbac_permissions": rbac or [],
    }
    if user_types is not None:
        out["user_types"] = user_types
    if pws_only:
        out["pws_only"] = True
    if alpha_only:
        out["alpha_only"] = True
    if children:
        out["children"] = children
    return out


MODULE_GROUPS: List[Dict[str, Any]] = [
    {
        "id": "management",
        "label": "Management & Insights",
        "modules": [
            _mod("dashboard", "Dashboard", permission_keys=["dashboard_access"], rbac=[Permission.DASHBOARD_ACCESS.value]),
            _mod("reports", "Reports", permission_keys=["access_reports"], rbac=[Permission.RUN_PWS_REPORTS.value, Permission.RUN_ALPHA_REPORTS.value]),
            _mod("approvals", "Approvals", permission_keys=["approve_requests"], rbac=[Permission.APPROVE_REQUESTS.value]),
            _mod("tasks", "Task Tracker", permission_keys=["supervise_tasks"], rbac=[
                Permission.CREATE_TEACHER_TASKS.value,
                Permission.CREATE_COACH_TASKS.value,
                Permission.MANAGE_PWS_TASKS.value,
                Permission.MANAGE_ALPHA_TASKS.value,
                Permission.MANAGE_TEACHER_TASKS.value,
                Permission.MANAGE_COACH_TASKS.value,
            ]),
        ],
    },
    {
        "id": "directory",
        "label": "Directory",
        "modules": [
            _mod("directory-master", "Directory", permission_keys=["view_students", "view_players", "view_staff"], user_types=[
                UserRole.SUPER_ADMIN.value, UserRole.PWS_ADMIN.value, UserRole.ALPHA_ADMIN.value,
                UserRole.PWS_ACCOUNTS.value, UserRole.ALPHA_ACCOUNTS.value,
            ]),
            _mod("staff-coaches", "Staff & Coaches", children=[
                _mod("staff", "Staff", permission_keys=["view_staff"]),
                _mod("coaches", "Coaches", permission_keys=["manage_users"], rbac=[Permission.MANAGE_COACHES.value, Permission.CREATE_USERS.value],
                      user_types=[UserRole.SUPER_ADMIN.value, UserRole.ALPHA_ADMIN.value]),
            ]),
            _mod("teachers", "Teachers", children=[
                _mod("teachers-directory", "Teachers", permission_keys=["manage_users", "view_students"],
                      rbac=[Permission.MANAGE_TEACHERS_MAP_SUBJECTS.value, Permission.CREATE_USERS.value],
                      pws_only=True, user_types=[UserRole.SUPER_ADMIN.value, UserRole.PWS_ADMIN.value]),
                _mod("add-new-teacher", "Add New Teacher", rbac=[Permission.ADD_NEW_TEACHER.value],
                      pws_only=True, user_types=[UserRole.SUPER_ADMIN.value, UserRole.PWS_ADMIN.value]),
            ], pws_only=True, user_types=[UserRole.SUPER_ADMIN.value, UserRole.PWS_ADMIN.value]),
            _mod("students-section", "Students", children=[
                _mod("students", "Students", permission_keys=["view_students", "add_students", "edit_students"], rbac=[Permission.ADD_PWS_STUDENTS.value],
                      pws_only=True, user_types=[UserRole.SUPER_ADMIN.value, UserRole.PWS_ADMIN.value, UserRole.PWS_ACCOUNTS.value]),
            ], pws_only=True, user_types=[UserRole.SUPER_ADMIN.value, UserRole.PWS_ADMIN.value, UserRole.PWS_ACCOUNTS.value]),
            _mod("players-section", "Players", children=[
                _mod("players", "Players", permission_keys=["view_players", "add_players", "edit_players"], rbac=[Permission.MANAGE_PLAYERS.value, Permission.ADD_ALPHA_PLAYERS.value],
                      user_types=[UserRole.SUPER_ADMIN.value, UserRole.ALPHA_ADMIN.value, UserRole.ALPHA_ACCOUNTS.value]),
            ], user_types=[UserRole.SUPER_ADMIN.value, UserRole.ALPHA_ADMIN.value, UserRole.ALPHA_ACCOUNTS.value]),
        ],
    },
    {
        "id": "financials",
        "label": "Financials",
        "modules": [
            _mod("fee-catalog", "Fee Catalogue", permission_keys=["manage_fee_catalog", "edit_fees"], rbac=[Permission.MANAGE_FEES_HEADS.value],
                  user_types=[UserRole.SUPER_ADMIN.value, UserRole.PWS_ADMIN.value, UserRole.ALPHA_ADMIN.value, UserRole.PWS_ACCOUNTS.value, UserRole.ALPHA_ACCOUNTS.value]),
            _mod("collect-fees", "Collect Fees", permission_keys=["collect_fees", "view_fees"], rbac=[Permission.COLLECT_PWS_FEES.value, Permission.COLLECT_ALPHA_FEES.value],
                  user_types=[UserRole.SUPER_ADMIN.value, UserRole.PWS_ADMIN.value, UserRole.ALPHA_ADMIN.value, UserRole.PWS_ACCOUNTS.value, UserRole.ALPHA_ACCOUNTS.value]),
            _mod("defaulters", "Defaulters", permission_keys=["collect_fees", "view_fees"], rbac=[Permission.COLLECT_PWS_FEES.value, Permission.COLLECT_ALPHA_FEES.value],
                  user_types=[UserRole.SUPER_ADMIN.value, UserRole.PWS_ADMIN.value, UserRole.ALPHA_ADMIN.value, UserRole.PWS_ACCOUNTS.value, UserRole.ALPHA_ACCOUNTS.value]),
            _mod("finance-reports", "Finance Reports", permission_keys=["collect_fees", "view_fees", "access_reports"], rbac=[
                Permission.COLLECT_PWS_FEES.value, Permission.COLLECT_ALPHA_FEES.value,
                Permission.RUN_PWS_REPORTS.value, Permission.RUN_ALPHA_REPORTS.value,
            ], user_types=[UserRole.SUPER_ADMIN.value, UserRole.PWS_ADMIN.value, UserRole.ALPHA_ADMIN.value, UserRole.PWS_ACCOUNTS.value, UserRole.ALPHA_ACCOUNTS.value]),
            _mod("invoice-engine", "Invoice Engine", permission_keys=["collect_fees", "view_fees"], rbac=[Permission.COLLECT_PWS_FEES.value], pws_only=True,
                  user_types=[UserRole.SUPER_ADMIN.value, UserRole.PWS_ADMIN.value, UserRole.PWS_ACCOUNTS.value]),
        ],
    },
    {
        "id": "operations",
        "label": "Operations & Logistics",
        "modules": [
            _mod("attendance", "Attendance", children=[
                _mod("attendance-take", "Take Attendance", permission_keys=[
                    "mark_student_attendance", "mark_player_attendance", "mark_staff_attendance", "mark_coach_attendance",
                ], rbac=[
                    Permission.MARK_STUDENT_ATTENDANCE.value,
                    Permission.MARK_PLAYER_ATTENDANCE.value,
                    Permission.MARK_PWS_ATTENDANCE.value,
                    Permission.MARK_ALPHA_ATTENDANCE.value,
                ]),
                _mod("attendance-reports", "Attendance Reports", permission_keys=["view_attendance"], rbac=[
                    Permission.VIEW_ATTENDANCE.value, Permission.RUN_PWS_REPORTS.value, Permission.RUN_ALPHA_REPORTS.value,
                ]),
            ]),
            _mod("hostel", "Hostel", permission_keys=["mark_hostel_attendance"], rbac=[Permission.MARK_HOSTEL_ATTENDANCE.value]),
            _mod("bulk-upload", "Bulk Upload", permission_keys=["bulk_upload"], rbac=[Permission.BULK_UPLOAD_USERS.value],
                  user_types=[UserRole.SUPER_ADMIN.value]),
        ],
    },
    {
        "id": "academics",
        "label": "Academics & Assessments",
        "modules": [
            _mod("marks-entry", "Marks Entry", permission_keys=["enter_academic_marks", "view_academic_marks"],
                  rbac=[Permission.MANAGE_MARKS_ASSESSMENT.value], pws_only=True),
            _mod("marks-setup", "Marks Setup", permission_keys=["manage_academic_structure"],
                  rbac=[Permission.MANAGE_TEACHERS_MAP_SUBJECTS.value], pws_only=True,
                  user_types=[UserRole.SUPER_ADMIN.value, UserRole.PWS_ADMIN.value]),
            _mod("assessments", "Assessments", children=[
                _mod("player-assessments", "Player Assessments", permission_keys=["enter_coach_assessments", "view_coach_assessments"],
                      rbac=[Permission.MANAGE_PLAYER_ASSESSMENT.value, Permission.MANAGE_COACH_ASSESSMENTS_ADMIN.value]),
                _mod("coach-assessments", "Coach Assessments", permission_keys=["manage_coach_assessments"],
                      rbac=[Permission.MANAGE_COACH_ASSESSMENTS_ADMIN.value],
                      user_types=[UserRole.SUPER_ADMIN.value, UserRole.PWS_ADMIN.value, UserRole.ALPHA_ADMIN.value]),
            ]),
            _mod("report-cards", "Report Cards", permission_keys=["enter_academic_marks", "view_academic_marks"],
                  rbac=[Permission.MANAGE_MARKS_ASSESSMENT.value], pws_only=True),
        ],
    },
    {
        "id": "system",
        "label": "System & Settings",
        "modules": [
            _mod("access-control", "Access Control", children=[
                _mod("permissions", "Permissions", permission_keys=["manage_users"], rbac=[Permission.MANAGE_ACCESS.value],
                      user_types=[UserRole.SUPER_ADMIN.value]),
                _mod("manage-users-rosters", "Manage Users & Rosters", permission_keys=["manage_users_rosters"],
                      rbac=[Permission.MANAGE_USERS_ROSTERS.value, Permission.CREATE_USERS.value],
                      user_types=[UserRole.SUPER_ADMIN.value, UserRole.PWS_ADMIN.value, UserRole.ALPHA_ADMIN.value,
                                  UserRole.PWS_ACCOUNTS.value, UserRole.ALPHA_ACCOUNTS.value]),
            ]),
            _mod("academic-structure", "Academic Structure", permission_keys=["manage_academic_structure"],
                  rbac=[Permission.MANAGE_TEACHERS_MAP_SUBJECTS.value, Permission.MANAGE_TEACHERS_MAP_SECTIONS.value], pws_only=True),
            _mod("academy-structure", "ALPHA/PWS Structure", permission_keys=["manage_users"], rbac=[Permission.MANAGE_ACCESS.value],
                  user_types=[UserRole.SUPER_ADMIN.value]),
            _mod("settings", "Settings", permission_keys=["dashboard_access"], rbac=[Permission.DASHBOARD_ACCESS.value],
                  user_types=[t for t in APPROVED_LOGIN_USER_TYPES if t != UserRole.ALPHA_COACH.value]),
            _mod("notifications", "Notifications", permission_keys=["dashboard_access"], rbac=[Permission.DASHBOARD_ACCESS.value],
                  user_types=[t for t in APPROVED_LOGIN_USER_TYPES if t != UserRole.ALPHA_COACH.value]),
        ],
    },
]


def _walk_modules(groups: Optional[List[Dict[str, Any]]] = None):
    groups = groups or MODULE_GROUPS
    for group in groups:
        for mod in group.get("modules", []):
            yield group, mod
            for child in mod.get("children") or []:
                yield group, child


def all_module_ids() -> List[str]:
    return [m["id"] for _, m in _walk_modules()]


def leaf_module_ids(catalog: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Return only leaf module ids (nodes without children) from a catalog."""
    catalog = catalog or MODULE_GROUPS
    out: List[str] = []

    def walk(mod: ModuleDef) -> None:
        children = mod.get("children")
        if children:
            for child in children:
                walk(child)
            return
        out.append(mod["id"])

    for group in catalog:
        for mod in group.get("modules", []):
            walk(mod)
    return out


def module_applicable(mod: ModuleDef, user_type: str) -> bool:
    if user_type in LOCKED_USER_TYPES:
        return True
    allowed = mod.get("user_types")
    if allowed is not None and user_type not in allowed:
        return False
    if mod.get("pws_only") and user_type in ALPHA_TYPES:
        return False
    if mod.get("alpha_only") and user_type in PWS_TYPES:
        return False
    return True


def filter_catalog_for_user_type(user_type: str) -> List[Dict[str, Any]]:
    """Return module groups with only applicable modules for the user type."""
    out: List[Dict[str, Any]] = []
    for group in MODULE_GROUPS:
        modules: List[ModuleDef] = []
        for mod in group["modules"]:
            children = mod.get("children")
            if children:
                filtered_children = [c for c in children if module_applicable(c, user_type)]
                if not filtered_children:
                    continue
                parent = {**mod, "children": filtered_children}
                if module_applicable(mod, user_type) or filtered_children:
                    modules.append(parent)
            elif module_applicable(mod, user_type):
                modules.append(mod)
        if modules:
            out.append({**group, "modules": modules})
    return out


# Default enabled modules per canonical user type (matches current product defaults).
DEFAULT_ENABLED_MODULES: Dict[str, Set[str]] = {
    UserRole.SUPER_ADMIN.value: set(all_module_ids()),
    UserRole.PWS_ADMIN.value: {
        "dashboard", "reports", "approvals", "tasks",
        "directory-master", "staff", "teachers-directory", "students",
        "attendance-take", "attendance-reports", "hostel",
        "academic-structure", "marks-entry", "marks-setup", "report-cards", "coach-assessments",
        "settings", "notifications",
    },
    UserRole.ALPHA_ADMIN.value: {
        "dashboard", "reports", "approvals", "tasks",
        "directory-master", "staff", "coaches", "players",
        "attendance-take", "attendance-reports",
        "player-assessments", "coach-assessments",
        "settings", "notifications",
    },
    UserRole.PWS_ACCOUNTS.value: {
        "dashboard", "reports", "tasks",
        "directory-master", "students",
        "fee-catalog", "collect-fees", "defaulters", "finance-reports", "invoice-engine",
        "settings", "notifications",
    },
    UserRole.ALPHA_ACCOUNTS.value: {
        "dashboard", "reports", "tasks",
        "directory-master", "players",
        "fee-catalog", "collect-fees", "defaulters", "finance-reports",
        "settings", "notifications",
    },
    UserRole.PWS_TEACHER.value: {
        "dashboard", "tasks",
        "attendance-take", "marks-entry", "report-cards",
        "settings", "notifications",
    },
    UserRole.ALPHA_COACH.value: {
        "dashboard", "tasks",
        "attendance-take", "player-assessments",
    },
}


def default_enabled_map(user_type: str) -> Dict[str, bool]:
    """Module id → enabled for a user type (catalog-filtered)."""
    catalog = filter_catalog_for_user_type(user_type)
    defaults = DEFAULT_ENABLED_MODULES.get(user_type, set())
    enabled: Dict[str, bool] = {}
    for _, mod in _walk_modules(catalog):
        enabled[mod["id"]] = mod["id"] in defaults
    return enabled


def derive_permissions_from_modules(
    user_type: str,
    modules: Dict[str, bool],
) -> tuple[Dict[str, bool], Dict[str, bool]]:
    """Build legacy permissions + RBAC overrides from enabled module ids."""
    permission_keys = [
        "view_students", "view_players", "view_staff",
        "mark_student_attendance", "mark_player_attendance", "mark_staff_attendance", "mark_coach_attendance",
        "mark_hostel_attendance", "view_attendance", "correct_attendance",
        "add_players", "edit_players", "toggle_player_status",
        "add_students", "edit_students",
        "access_reports", "dashboard_access", "lifecycle_dashboard", "manage_users", "manage_users_rosters", "manage_academic_structure",
        "enter_academic_marks", "view_academic_marks",
        "enter_coach_assessments", "manage_coach_assessments", "view_coach_assessments",
        "view_fees", "collect_fees", "edit_fees", "manage_fee_catalog", "bulk_upload", "approve_deactivation",
        "approve_requests", "supervise_tasks",
    ]
    legacy = {k: False for k in permission_keys}
    rbac: Dict[str, bool] = {}

    catalog = filter_catalog_for_user_type(user_type)
    for _, mod in _walk_modules(catalog):
        if not modules.get(mod["id"]):
            continue
        for key in mod.get("permission_keys") or []:
            if key in legacy:
                legacy[key] = True
        for perm in mod.get("rbac_permissions") or []:
            rbac[perm] = True

    # Dashboard is required for any app access unless explicitly all off
    if any(modules.values()):
        legacy["dashboard_access"] = True
        rbac[Permission.DASHBOARD_ACCESS.value] = True

    return legacy, rbac
