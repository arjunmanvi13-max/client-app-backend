"""Module access levels and designation permission presets for login users."""
from __future__ import annotations

from typing import Dict, List, Optional

try:
    from core import PERMISSION_KEYS
except Exception:
    PERMISSION_KEYS = [
        "view_students", "view_players", "view_staff",
        "mark_student_attendance", "mark_player_attendance",
        "mark_staff_attendance", "mark_coach_attendance", "mark_teacher_attendance",
        "mark_hostel_attendance", "view_attendance", "correct_attendance",
        "add_players", "edit_players", "toggle_player_status",
        "add_students", "edit_students",
        "access_reports", "dashboard_access", "lifecycle_dashboard",
        "manage_users", "manage_users_rosters", "manage_academic_structure",
        "enter_academic_marks", "view_academic_marks",
        "view_fees", "collect_fees", "edit_fees", "manage_fee_catalog",
        "bulk_upload", "approve_deactivation", "approve_requests", "supervise_tasks",
        "manage_expense_structure", "capture_pws_expenses", "capture_alpha_expenses",
        "timetable_view_all", "timetable_view_own", "timetable_create", "timetable_edit",
        "timetable_delete", "timetable_substitute", "timetable_publish", "timetable_export",
    ]

AccessLevel = str  # none | view | edit | admin

MODULE_MATRIX: List[Dict] = [
    {
        "id": "directory",
        "label": "Directory",
        "view": ["view_students", "view_players", "view_staff"],
        "edit": ["add_students", "edit_students", "add_players", "edit_players"],
        "admin": ["toggle_player_status", "manage_users_rosters"],
    },
    {
        "id": "fees",
        "label": "Fees & Collections",
        "view": ["view_fees"],
        "edit": ["collect_fees"],
        "admin": ["edit_fees", "manage_fee_catalog"],
    },
    {
        "id": "attendance",
        "label": "Attendance",
        "view": ["view_attendance"],
        "edit": [
            "mark_student_attendance", "mark_player_attendance",
            "mark_staff_attendance", "mark_coach_attendance", "mark_teacher_attendance",
        ],
        "admin": ["correct_attendance", "mark_hostel_attendance"],
    },
    {
        "id": "schedules",
        "label": "Schedules",
        "view": ["timetable_view_own"],
        "edit": ["timetable_view_all", "timetable_create", "timetable_edit", "timetable_substitute"],
        "admin": ["timetable_delete", "timetable_publish", "timetable_export"],
    },
    {
        "id": "tasks",
        "label": "Task Trackers",
        "view": ["dashboard_access"],
        "edit": ["supervise_tasks"],
        "admin": ["supervise_tasks"],
    },
    {
        "id": "reports",
        "label": "Reports",
        "view": ["access_reports"],
        "edit": ["access_reports"],
        "admin": ["access_reports", "lifecycle_dashboard"],
    },
    {
        "id": "approvals",
        "label": "Approvals",
        "view": ["dashboard_access"],
        "edit": ["approve_requests"],
        "admin": ["approve_requests", "approve_deactivation"],
    },
    {
        "id": "expenses",
        "label": "Expenses",
        "view": ["capture_pws_expenses", "capture_alpha_expenses"],
        "edit": ["capture_pws_expenses", "capture_alpha_expenses"],
        "admin": ["manage_expense_structure"],
    },
    {
        "id": "academics",
        "label": "Academics",
        "view": ["view_academic_marks"],
        "edit": ["enter_academic_marks"],
        "admin": ["manage_academic_structure"],
    },
]


def _keys_for_level(mod: Dict, level: str) -> List[str]:
    if level == "none":
        return []
    keys = list(mod.get("view") or [])
    if level in ("edit", "admin"):
        keys.extend(mod.get("edit") or [])
    if level == "admin":
        keys.extend(mod.get("admin") or [])
    return keys


def permissions_from_module_access(access: Dict[str, str]) -> dict:
    perms = {k: False for k in PERMISSION_KEYS}
    perms["dashboard_access"] = True
    for mod in MODULE_MATRIX:
        level = (access.get(mod["id"]) or "none").lower()
        if level not in ("none", "view", "edit", "admin"):
            level = "none"
        for key in _keys_for_level(mod, level):
            if key in perms:
                perms[key] = True
    return perms


def infer_module_access(perms: Optional[dict]) -> Dict[str, str]:
    p = perms or {}
    out: Dict[str, str] = {}
    for mod in MODULE_MATRIX:
        admin_keys = mod.get("admin") or []
        edit_keys = mod.get("edit") or []
        view_keys = mod.get("view") or []
        if admin_keys and all(p.get(k) for k in admin_keys):
            out[mod["id"]] = "admin"
        elif edit_keys and all(p.get(k) for k in edit_keys):
            out[mod["id"]] = "edit"
        elif view_keys and any(p.get(k) for k in view_keys):
            out[mod["id"]] = "view"
        else:
            out[mod["id"]] = "none"
    return out


# Designation → recommended module access (Super Admin may override).
DESIGNATION_PRESETS: Dict[str, Dict[str, str]] = {
    "PRINCIPAL": {
        "directory": "admin", "fees": "edit", "attendance": "admin", "schedules": "admin",
        "tasks": "admin", "reports": "admin", "approvals": "admin", "expenses": "edit", "academics": "admin",
    },
    "VICE_PRINCIPAL": {
        "directory": "edit", "fees": "view", "attendance": "admin", "schedules": "admin",
        "tasks": "admin", "reports": "admin", "approvals": "edit", "expenses": "view", "academics": "admin",
    },
    "ACADEMIC_HEAD": {
        "directory": "edit", "fees": "none", "attendance": "edit", "schedules": "admin",
        "tasks": "edit", "reports": "view", "approvals": "none", "expenses": "none", "academics": "admin",
    },
    "EVENT_COORDINATOR": {
        "directory": "view", "fees": "none", "attendance": "view", "schedules": "edit",
        "tasks": "edit", "reports": "view", "approvals": "none", "expenses": "edit", "academics": "view",
    },
    "PWS_OFFICE_STAFF": {
        "directory": "view", "fees": "view", "attendance": "view", "schedules": "view",
        "tasks": "edit", "reports": "view", "approvals": "none", "expenses": "none", "academics": "view",
    },
    "PWS_ACCOUNTS": {
        "directory": "view", "fees": "admin", "attendance": "none", "schedules": "none",
        "tasks": "edit", "reports": "admin", "approvals": "edit", "expenses": "admin", "academics": "none",
    },
    "HOD": {
        "directory": "view", "fees": "none", "attendance": "edit", "schedules": "edit",
        "tasks": "edit", "reports": "view", "approvals": "none", "expenses": "none", "academics": "edit",
    },
    "TEACHER": {
        "directory": "view", "fees": "none", "attendance": "edit", "schedules": "view",
        "tasks": "view", "reports": "none", "approvals": "none", "expenses": "none", "academics": "edit",
    },
    "WARDEN": {
        "directory": "view", "fees": "none", "attendance": "admin", "schedules": "view",
        "tasks": "edit", "reports": "view", "approvals": "edit", "expenses": "view", "academics": "none",
    },
    "COACH": {
        "directory": "view", "fees": "none", "attendance": "edit", "schedules": "view",
        "tasks": "view", "reports": "none", "approvals": "none", "expenses": "none", "academics": "none",
    },
    "ALPHA_ACCOUNTS": {
        "directory": "view", "fees": "admin", "attendance": "none", "schedules": "none",
        "tasks": "edit", "reports": "admin", "approvals": "edit", "expenses": "admin", "academics": "none",
    },
    "ALPHA_OFFICE_STAFF": {
        "directory": "view", "fees": "view", "attendance": "view", "schedules": "view",
        "tasks": "edit", "reports": "view", "approvals": "none", "expenses": "none", "academics": "none",
    },
}


def preset_for_designation(designation: Optional[str]) -> Dict[str, str]:
    key = (designation or "").upper()
    return dict(DESIGNATION_PRESETS.get(key) or {m["id"]: "none" for m in MODULE_MATRIX})
