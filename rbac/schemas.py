"""
MongoDB document schemas (Pydantic) for RBAC and entity mappings.

Stack: MongoDB + Motor — these models document collection shapes and validate
API payloads. They complement (not replace) existing models in core.py.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field

from rbac.enums import BusinessEntity, Permission, UserRole

# ---------------------------------------------------------------------------
# users — login accounts
# ---------------------------------------------------------------------------

class UserPermissions(BaseModel):
    """Granular overrides; keys are Permission enum values (SCREAMING_SNAKE_CASE)."""

    model_config = {"extra": "allow"}

    def is_granted(self, permission: Permission) -> Optional[bool]:
        val = getattr(self, permission.value, None)
        if val is None and permission.value in self.model_extra or {}:
            val = self.model_extra.get(permission.value)
        return val if isinstance(val, bool) else None


class UserDocument(BaseModel):
    """
    MongoDB `users` collection.

    Relationships:
    - Coach scope: assigned_centres[], assigned_sports[] (+ coach_sport_assignments)
    - Parent scope: linked_person_ids[] ↔ people.parent_user_ids[]
    - Staff link: person_id → people (kind=staff)
    """

    id: str
    email: Optional[EmailStr] = None
    mobile: Optional[str] = None
    password_hash: str
    name: str
    role: str  # legacy or canonical; normalize with normalize_role()
    organization: BusinessEntity = BusinessEntity.PWS
    department: Optional[str] = None

    is_active: bool = True  # canonical; mirrors status != "deactivated"
    status: Literal["active", "deactivated"] = "active"

    # Legacy permission systems (prefer permissions_rbac + Permission enum)
    permissions: dict[str, bool] = Field(default_factory=dict)
    can_manage: list[Literal["student", "player", "teacher", "coach", "staff"]] = Field(default_factory=list)
    coach_permissions: list[Literal["view_players", "add_players", "edit_players"]] = Field(default_factory=list)

    # RBAC overrides — Permission enum string → bool (MANAGE_ACCESS grants)
    permissions_rbac: dict[str, bool] = Field(default_factory=dict)

    coach_type: Optional[Literal["head", "assistant"]] = None
    assigned_sport: Optional[str] = None
    assigned_centres: list[Literal["Balua", "Harding Park"]] = Field(default_factory=list)
    assigned_sports: list[Literal["Cricket", "Football"]] = Field(default_factory=list)

    linked_person_ids: list[str] = Field(default_factory=list)
    person_id: Optional[str] = None

    must_change_password: bool = False
    is_password_set: bool = True
    created_at: str
    updated_at: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.is_active and self.status == "active"


# ---------------------------------------------------------------------------
# people — roster (students, players, staff records)
# ---------------------------------------------------------------------------

class PersonDocument(BaseModel):
    """MongoDB `people` collection."""

    id: str
    kind: Literal["student", "player", "teacher", "coach", "staff"]
    name: str
    organization: BusinessEntity = BusinessEntity.PWS
    entities: list[Literal["PWS", "ALPHA"]] = Field(default_factory=list)

    is_active: bool = True
    status: Literal["active", "deactivated"] = "active"

    # Student mapping (grade / section)
    section_id: Optional[str] = None
    group: Optional[str] = None  # e.g. "9-A"
    admission_number: Optional[str] = None
    roll_number: Optional[str] = None
    date_of_admission: Optional[str] = None

    # Player mapping (venue / sport / category)
    centre: Optional[Literal["Balua", "Harding Park"]] = None
    sport: Optional[Literal["Cricket", "Football"]] = None
    slot: Optional[Literal["Morning", "Evening", "Both"]] = None
    player_type: Optional[
        Literal["Daily", "Day Boarding", "Hostel", "Hostel Only", "Boarding"]
    ] = None
    player_id: Optional[str] = None
    skill_level: Optional[Literal["Beginner", "Intermediate", "Advanced"]] = None

    parent_user_ids: list[str] = Field(default_factory=list)
    employee_id: Optional[str] = None
    department: Optional[str] = None
    created_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Assignment / mapping collections
# ---------------------------------------------------------------------------

class TeacherSubjectAssignment(BaseModel):
    """
    MongoDB `teacher_class_assignments` — teacher → grade + section + subject.

    Used by MANAGE_TEACHERS_MAP_SUBJECTS.
    """

    id: str
    teacher_user_id: str
    academic_year_id: str
    grade_id: str
    section_id: str
    subject_id: str
    created_at: str
    created_by: str


class TeacherSectionAssignment(BaseModel):
    """
    MongoDB `teacher_section_assignments` — teacher → grade + section (homeroom).

    Used by MANAGE_TEACHERS_MAP_SECTIONS.
    """

    id: str
    teacher_user_id: str
    academic_year_id: str
    section_id: str
    created_at: str
    created_by: str


class CoachSportAssignment(BaseModel):
    """
    MongoDB `coach_sport_assignments` (recommended) — coach → sport + venue.

    Mirrors user.assigned_centres / assigned_sports for queryable RBAC scope.
    Existing coaches store scope on the user doc; this collection formalizes it.
    """

    id: str
    coach_user_id: str
    sport: Literal["Cricket", "Football"]
    centre: Literal["Balua", "Harding Park"]
    coach_type: Literal["head", "assistant"] = "assistant"
    is_active: bool = True
    created_at: str
    created_by: str


class StudentEnrollment(BaseModel):
    """
    MongoDB `student_enrollments` (recommended) — student → grade + section per year.

    Today section_id lives on people; this supports historical enrolments.
    """

    id: str
    student_person_id: str
    academic_year_id: str
    grade_id: str
    section_id: str
    is_active: bool = True
    enrolled_at: str


class PlayerEnrollment(BaseModel):
    """
    MongoDB `player_enrollments` (recommended) — player → venue + sport + category.

    Used by MANAGE_PLAYERS / ADD_ALPHA_PLAYERS.
    """

    id: str
    player_person_id: str
    centre: Literal["Balua", "Harding Park"]
    sport: Literal["Cricket", "Football"]
    player_type: Literal["Daily", "Day Boarding", "Hostel", "Hostel Only", "Boarding"]
    slot: Optional[Literal["Morning", "Evening", "Both"]] = None
    is_active: bool = True
    enrolled_at: str


# ---------------------------------------------------------------------------
# Fee heads (Super Admin)
# ---------------------------------------------------------------------------

class FeeHeadDocument(BaseModel):
    """MongoDB `fee_catalog` / rate-card entries — MANAGE_FEES_HEADS."""

    id: str
    entity: Literal["PWS", "ALPHA"]
    code: str
    name: str
    default_amount: float
    is_active: bool = True
    created_at: str
    updated_at: Optional[str] = None


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

class UserCreateRBAC(BaseModel):
    """Payload for CREATE_USERS."""

    email: EmailStr
    name: str
    role: UserRole
    organization: BusinessEntity = BusinessEntity.PWS
    department: Optional[str] = None
    is_active: bool = True
    permissions_rbac: dict[str, bool] = Field(default_factory=dict)


class PermissionGrant(BaseModel):
    """Payload for MANAGE_ACCESS — grant/revoke a single permission."""

    permission: Permission
    granted: bool


# MongoDB index recommendations (run once in migration/seed):
MONGODB_INDEXES: dict[str, list[tuple]] = {
    "users": [
        ([("email", 1)], {"unique": True, "sparse": True}),
        ([("role", 1), ("organization", 1)], {}),
        ([("status", 1)], {}),
    ],
    "teacher_class_assignments": [
        ([("teacher_user_id", 1), ("academic_year_id", 1)], {}),
        ([("section_id", 1), ("subject_id", 1)], {}),
    ],
    "teacher_section_assignments": [
        ([("teacher_user_id", 1), ("academic_year_id", 1)], {}),
        ([("section_id", 1)], {}),
    ],
    "coach_sport_assignments": [
        ([("coach_user_id", 1)], {}),
        ([("centre", 1), ("sport", 1)], {}),
    ],
    "student_enrollments": [
        ([("student_person_id", 1), ("academic_year_id", 1)], {}),
    ],
    "player_enrollments": [
        ([("player_person_id", 1)], {}),
        ([("centre", 1), ("sport", 1), ("player_type", 1)], {}),
    ],
}
