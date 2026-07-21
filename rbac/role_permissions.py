"""Static role → permission grants from the RBAC specification."""
from rbac.enums import Permission, UserRole

ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.SUPER_ADMIN: frozenset({
        Permission.CREATE_USERS,
        Permission.MANAGE_FEES_HEADS,
        Permission.MANAGE_ACCESS,
        Permission.BULK_UPLOAD_USERS,
        Permission.TOGGLE_USER_STATUS,
        Permission.ADD_COACHES,
        Permission.ADD_NEW_TEACHER,
        Permission.MANAGE_COACH_ASSESSMENTS_ADMIN,
        Permission.APPROVE_REQUESTS,
        Permission.CORRECT_ATTENDANCE,
        Permission.DASHBOARD_ACCESS,
    }),

    UserRole.PWS_ADMIN: frozenset({
        Permission.MARK_PWS_ATTENDANCE,
        Permission.MANAGE_TEACHERS_MAP_SUBJECTS,
        Permission.CREATE_TEACHER_TASKS,
        Permission.MANAGE_TEACHERS_MAP_SECTIONS,
        Permission.APPROVE_REQUESTS,
        Permission.VIEW_ATTENDANCE,
        Permission.CORRECT_ATTENDANCE,
        Permission.DASHBOARD_ACCESS,
    }),

    UserRole.ALPHA_ADMIN: frozenset({
        Permission.MARK_ALPHA_ATTENDANCE,
        Permission.MANAGE_COACHES,
        Permission.CREATE_COACH_TASKS,
        Permission.MANAGE_PLAYERS,
        Permission.MANAGE_COACH_ASSESSMENTS_ADMIN,
        Permission.VIEW_ATTENDANCE,
        Permission.CORRECT_ATTENDANCE,
        Permission.DASHBOARD_ACCESS,
    }),

    UserRole.PWS_ACCOUNTS: frozenset({
        Permission.COLLECT_PWS_FEES,
        Permission.MANAGE_PWS_TASKS,
        Permission.ADD_PWS_STUDENTS,
        Permission.RUN_PWS_REPORTS,
        Permission.DASHBOARD_ACCESS,
    }),

    UserRole.ALPHA_ACCOUNTS: frozenset({
        Permission.COLLECT_ALPHA_FEES,
        Permission.MANAGE_ALPHA_TASKS,
        Permission.ADD_ALPHA_PLAYERS,
        Permission.RUN_ALPHA_REPORTS,
        Permission.DASHBOARD_ACCESS,
    }),

    UserRole.PWS_TEACHER: frozenset({
        Permission.MARK_STUDENT_ATTENDANCE,
        Permission.MANAGE_MARKS_ASSESSMENT,
        Permission.MANAGE_TEACHER_TASKS,
        Permission.DASHBOARD_ACCESS,
    }),

    UserRole.ALPHA_COACH: frozenset({
        Permission.MARK_PLAYER_ATTENDANCE,
        Permission.MANAGE_PLAYER_ASSESSMENT,
        Permission.MANAGE_COACH_TASKS,
        Permission.DASHBOARD_ACCESS,
    }),

    UserRole.WARDEN: frozenset({
        Permission.MARK_HOSTEL_ATTENDANCE,
        Permission.VIEW_ATTENDANCE,
        Permission.DASHBOARD_ACCESS,
    }),
    UserRole.STAFF: frozenset({Permission.DASHBOARD_ACCESS}),
    UserRole.STUDENT: frozenset({Permission.DASHBOARD_ACCESS}),
    UserRole.PLAYER: frozenset({Permission.DASHBOARD_ACCESS}),
    UserRole.PARENT: frozenset({Permission.DASHBOARD_ACCESS}),
}


def permissions_for_role(role: UserRole) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())
