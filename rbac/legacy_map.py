"""Bridge canonical Permission enum ↔ legacy snake_case permission keys on user docs."""
from rbac.enums import Permission

# New spec permission → existing PERMISSION_KEYS used in MongoDB user.permissions
PERMISSION_TO_LEGACY: dict[Permission, tuple[str, ...]] = {
    Permission.CREATE_USERS: ("manage_users",),
    Permission.MANAGE_FEES_HEADS: ("manage_fee_catalog",),
    Permission.MANAGE_ACCESS: ("manage_users",),  # Super Admin module access panel
    Permission.BULK_UPLOAD_USERS: ("bulk_upload",),
    Permission.TOGGLE_USER_STATUS: ("toggle_player_status", "approve_deactivation"),
    Permission.ADD_COACHES: ("manage_users",),

    Permission.MARK_PWS_ATTENDANCE: (
        "mark_student_attendance",
        "mark_staff_attendance",
        "view_attendance",
    ),
    Permission.MANAGE_TEACHERS_MAP_SUBJECTS: ("manage_academic_structure",),
    Permission.CREATE_TEACHER_TASKS: ("supervise_tasks",),
    Permission.MANAGE_TEACHERS_MAP_SECTIONS: ("manage_academic_structure",),

    Permission.MARK_ALPHA_ATTENDANCE: (
        "mark_player_attendance",
        "mark_coach_attendance",
        "mark_staff_attendance",
        "view_attendance",
    ),
    Permission.MANAGE_COACHES: ("manage_users",),
    Permission.CREATE_COACH_TASKS: ("supervise_tasks",),
    Permission.MANAGE_PLAYERS: ("add_players", "edit_players", "view_players"),

    Permission.COLLECT_PWS_FEES: ("collect_fees", "view_fees"),
    Permission.MANAGE_PWS_TASKS: ("supervise_tasks",),
    Permission.ADD_PWS_STUDENTS: ("add_students", "edit_students", "view_students"),
    Permission.RUN_PWS_REPORTS: ("access_reports",),

    Permission.COLLECT_ALPHA_FEES: ("collect_fees", "view_fees"),
    Permission.MANAGE_ALPHA_TASKS: ("supervise_tasks",),
    Permission.ADD_ALPHA_PLAYERS: ("add_players", "edit_players", "view_players"),
    Permission.RUN_ALPHA_REPORTS: ("access_reports",),

    Permission.MARK_STUDENT_ATTENDANCE: ("mark_student_attendance", "view_attendance"),
    Permission.MANAGE_MARKS_ASSESSMENT: (
        "enter_academic_marks",
        "view_academic_marks",
    ),
    Permission.MANAGE_TEACHER_TASKS: ("supervise_tasks",),

    Permission.MARK_PLAYER_ATTENDANCE: ("mark_player_attendance", "view_attendance"),
    Permission.MANAGE_PLAYER_ASSESSMENT: (
        "enter_coach_assessments",
        "view_coach_assessments",
    ),
    Permission.MANAGE_COACH_TASKS: ("supervise_tasks",),

    Permission.DASHBOARD_ACCESS: ("dashboard_access",),
}
