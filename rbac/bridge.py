"""Bidirectional bridge between legacy permission keys and canonical Permission enum."""
from rbac.enums import Permission
from rbac.legacy_map import PERMISSION_TO_LEGACY

# legacy snake_case key → Permission enums that grant it
LEGACY_KEY_TO_PERMISSIONS: dict[str, tuple[Permission, ...]] = {}
for perm, keys in PERMISSION_TO_LEGACY.items():
    for key in keys:
        LEGACY_KEY_TO_PERMISSIONS.setdefault(key, [])
        if perm not in LEGACY_KEY_TO_PERMISSIONS[key]:
            LEGACY_KEY_TO_PERMISSIONS[key].append(perm)

# Direct mappings for keys not covered above
LEGACY_KEY_TO_PERMISSIONS.update({
    "correct_attendance": (Permission.CORRECT_ATTENDANCE,),
    "mark_hostel_attendance": (Permission.MARK_HOSTEL_ATTENDANCE,),
    "manage_coach_assessments": (Permission.MANAGE_COACH_ASSESSMENTS_ADMIN,),
    "approve_requests": (Permission.APPROVE_REQUESTS, Permission.TOGGLE_USER_STATUS),
    "approve_deactivation": (Permission.TOGGLE_USER_STATUS,),
    "view_attendance": (Permission.VIEW_ATTENDANCE, Permission.MARK_PWS_ATTENDANCE, Permission.MARK_ALPHA_ATTENDANCE),
    "access_reports": (Permission.RUN_PWS_REPORTS, Permission.RUN_ALPHA_REPORTS),
    "view_fees": (Permission.COLLECT_PWS_FEES, Permission.COLLECT_ALPHA_FEES),
    "collect_fees": (Permission.COLLECT_PWS_FEES, Permission.COLLECT_ALPHA_FEES),
    "edit_fees": (Permission.MANAGE_FEES_HEADS,),
    "view_students": (Permission.ADD_PWS_STUDENTS,),
    "edit_students": (Permission.ADD_PWS_STUDENTS,),
    "view_players": (Permission.MANAGE_PLAYERS, Permission.ADD_ALPHA_PLAYERS),
    "edit_players": (Permission.MANAGE_PLAYERS, Permission.ADD_ALPHA_PLAYERS),
    "add_players": (Permission.MANAGE_PLAYERS, Permission.ADD_ALPHA_PLAYERS),
    "toggle_player_status": (Permission.TOGGLE_USER_STATUS,),
    "supervise_tasks": (
        Permission.CREATE_TEACHER_TASKS,
        Permission.CREATE_COACH_TASKS,
        Permission.MANAGE_PWS_TASKS,
        Permission.MANAGE_ALPHA_TASKS,
        Permission.MANAGE_TEACHER_TASKS,
        Permission.MANAGE_COACH_TASKS,
    ),
    "lifecycle_dashboard": (Permission.DASHBOARD_ACCESS,),
})

# Extend PERMISSION_TO_LEGACY for new enum values
PERMISSION_TO_LEGACY.update({
    Permission.CORRECT_ATTENDANCE: ("correct_attendance",),
    Permission.MARK_HOSTEL_ATTENDANCE: ("mark_hostel_attendance",),
    Permission.MANAGE_COACH_ASSESSMENTS_ADMIN: ("manage_coach_assessments",),
    Permission.APPROVE_REQUESTS: ("approve_requests",),
    Permission.VIEW_ATTENDANCE: ("view_attendance",),
})

RBAC_PERMISSION_GROUPS: dict[str, list[str]] = {
    "Super Admin": [
        Permission.CREATE_USERS.value,
        Permission.MANAGE_FEES_HEADS.value,
        Permission.MANAGE_ACCESS.value,
        Permission.BULK_UPLOAD_USERS.value,
        Permission.TOGGLE_USER_STATUS.value,
        Permission.ADD_COACHES.value,
        Permission.ADD_NEW_TEACHER.value,
    ],
    "PWS Admin": [
        Permission.MARK_PWS_ATTENDANCE.value,
        Permission.MANAGE_TEACHERS_MAP_SUBJECTS.value,
        Permission.CREATE_TEACHER_TASKS.value,
        Permission.MANAGE_TEACHERS_MAP_SECTIONS.value,
    ],
    "ALPHA Admin": [
        Permission.MARK_ALPHA_ATTENDANCE.value,
        Permission.MANAGE_COACHES.value,
        Permission.CREATE_COACH_TASKS.value,
        Permission.MANAGE_PLAYERS.value,
        Permission.MANAGE_COACH_ASSESSMENTS_ADMIN.value,
    ],
    "PWS Accounts": [
        Permission.COLLECT_PWS_FEES.value,
        Permission.MANAGE_PWS_TASKS.value,
        Permission.ADD_PWS_STUDENTS.value,
        Permission.RUN_PWS_REPORTS.value,
    ],
    "ALPHA Accounts": [
        Permission.COLLECT_ALPHA_FEES.value,
        Permission.MANAGE_ALPHA_TASKS.value,
        Permission.ADD_ALPHA_PLAYERS.value,
        Permission.RUN_ALPHA_REPORTS.value,
    ],
    "PWS Teachers": [
        Permission.MARK_STUDENT_ATTENDANCE.value,
        Permission.MANAGE_MARKS_ASSESSMENT.value,
        Permission.MANAGE_TEACHER_TASKS.value,
    ],
    "ALPHA Coaches": [
        Permission.MARK_PLAYER_ATTENDANCE.value,
        Permission.MANAGE_PLAYER_ASSESSMENT.value,
        Permission.MANAGE_COACH_TASKS.value,
    ],
    "Extended": [
        Permission.CORRECT_ATTENDANCE.value,
        Permission.MARK_HOSTEL_ATTENDANCE.value,
        Permission.APPROVE_REQUESTS.value,
        Permission.VIEW_ATTENDANCE.value,
        Permission.DASHBOARD_ACCESS.value,
    ],
}

RBAC_PERMISSION_LABELS: dict[str, str] = {
    Permission.CREATE_USERS.value: "Create users",
    Permission.MANAGE_FEES_HEADS.value: "Manage fee heads & defaults",
    Permission.MANAGE_ACCESS.value: "Manage module access",
    Permission.BULK_UPLOAD_USERS.value: "Bulk upload users",
    Permission.TOGGLE_USER_STATUS.value: "Activate / deactivate users",
    Permission.ADD_COACHES.value: "Add coaches",
    Permission.ADD_NEW_TEACHER.value: "Add new teacher",
    Permission.MARK_PWS_ATTENDANCE.value: "Mark PWS attendance",
    Permission.MANAGE_TEACHERS_MAP_SUBJECTS.value: "Map teachers to subjects",
    Permission.CREATE_TEACHER_TASKS.value: "Create teacher tasks",
    Permission.MANAGE_TEACHERS_MAP_SECTIONS.value: "Map teachers to sections",
    Permission.MARK_ALPHA_ATTENDANCE.value: "Mark ALPHA attendance",
    Permission.MANAGE_COACHES.value: "Manage coaches",
    Permission.CREATE_COACH_TASKS.value: "Create coach tasks",
    Permission.MANAGE_PLAYERS.value: "Manage players",
    Permission.COLLECT_PWS_FEES.value: "Collect PWS fees",
    Permission.MANAGE_PWS_TASKS.value: "Manage PWS tasks",
    Permission.ADD_PWS_STUDENTS.value: "Add PWS students",
    Permission.RUN_PWS_REPORTS.value: "Run PWS reports",
    Permission.COLLECT_ALPHA_FEES.value: "Collect ALPHA fees",
    Permission.MANAGE_ALPHA_TASKS.value: "Manage ALPHA tasks",
    Permission.ADD_ALPHA_PLAYERS.value: "Add ALPHA players",
    Permission.RUN_ALPHA_REPORTS.value: "Run ALPHA reports",
    Permission.MARK_STUDENT_ATTENDANCE.value: "Mark student attendance",
    Permission.MANAGE_MARKS_ASSESSMENT.value: "Marks & assessment",
    Permission.MANAGE_TEACHER_TASKS.value: "Manage teacher tasks",
    Permission.MARK_PLAYER_ATTENDANCE.value: "Mark player attendance",
    Permission.MANAGE_PLAYER_ASSESSMENT.value: "Player assessments",
    Permission.MANAGE_COACH_TASKS.value: "Manage coach tasks",
    Permission.CORRECT_ATTENDANCE.value: "Correct attendance records",
    Permission.MARK_HOSTEL_ATTENDANCE.value: "Mark hostel attendance",
    Permission.MANAGE_COACH_ASSESSMENTS_ADMIN.value: "Administer coach assessments",
    Permission.APPROVE_REQUESTS.value: "Approve requests",
    Permission.VIEW_ATTENDANCE.value: "View attendance",
    Permission.DASHBOARD_ACCESS.value: "Dashboard access",
}
