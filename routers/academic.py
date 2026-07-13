"""Academic structure — years, grades, sections, subjects, teacher class assignments (PWS)."""
import uuid
from typing import Optional, Literal, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core import (
    db, get_current_user, is_admin, is_super_admin, get_perm, now_utc,
)

router = APIRouter(prefix="/academic", tags=["academic"])

ENTITY_PWS = "pws"
YEAR_STATUSES = ("open", "closed", "archived")


from rbac.guards import assert_manage_academic, can_manage_academic as _can_manage_academic


def _assert_manage_academic(user: dict) -> None:
    assert_manage_academic(user)


async def get_open_academic_year(entity_id: str = ENTITY_PWS) -> Optional[dict]:
    return await db.academic_years.find_one(
        {"entity_id": entity_id, "status": "open"},
        {"_id": 0},
    )


async def _year_by_id(year_id: str) -> dict:
    year = await db.academic_years.find_one({"id": year_id}, {"_id": 0})
    if not year:
        raise HTTPException(404, "Academic year not found")
    return year


def _assert_year_writable(year: dict) -> None:
    if year.get("status") == "archived":
        raise HTTPException(403, "Archived academic years are read-only")


async def _class_assignments_for_teacher(
    teacher_user_id: str,
    academic_year_id: Optional[str] = None,
) -> list[dict]:
    year = academic_year_id
    if not year:
        open_year = await get_open_academic_year()
        if not open_year:
            return []
        year = open_year["id"]
    rows = await db.teacher_class_assignments.find(
        {"teacher_user_id": teacher_user_id, "academic_year_id": year},
        {"_id": 0},
    ).to_list(300)
    return rows


async def assigned_section_ids_for_teacher(
    teacher_user_id: str,
    academic_year_id: Optional[str] = None,
) -> list[str]:
    year = academic_year_id
    if not year:
        open_year = await get_open_academic_year()
        if not open_year:
            return []
        year = open_year["id"]
    class_rows = await _class_assignments_for_teacher(teacher_user_id, year)
    section_ids = list({r["section_id"] for r in class_rows})
    if section_ids:
        return section_ids
    # Legacy section-only assignments
    rows = await db.teacher_section_assignments.find(
        {"teacher_user_id": teacher_user_id, "academic_year_id": year},
        {"section_id": 1, "_id": 0},
    ).to_list(200)
    return [r["section_id"] for r in rows]


async def assigned_subject_ids_for_teacher(
    teacher_user_id: str,
    section_id: str,
    academic_year_id: Optional[str] = None,
) -> list[str]:
    year = academic_year_id
    if not year:
        open_year = await get_open_academic_year()
        if not open_year:
            return []
        year = open_year["id"]
    rows = await db.teacher_class_assignments.find(
        {
            "teacher_user_id": teacher_user_id,
            "academic_year_id": year,
            "section_id": section_id,
        },
        {"subject_id": 1, "_id": 0},
    ).to_list(100)
    return [r["subject_id"] for r in rows]


async def resolve_section_group(section_id: str) -> tuple[str, str]:
    section = await db.sections.find_one({"id": section_id}, {"_id": 0})
    if not section:
        raise HTTPException(404, "Section not found")
    return section["id"], section["label"]


async def assert_teacher_section_access(user: dict, section_id: str) -> None:
    if user.get("role") != "teacher":
        return
    assigned = await assigned_section_ids_for_teacher(user["id"])
    if section_id not in assigned:
        raise HTTPException(403, "You are not assigned to this section")


async def assert_teacher_subject_access(
    user: dict,
    section_id: str,
    subject_id: str,
    academic_year_id: Optional[str] = None,
) -> None:
    """Teacher must have an explicit class assignment for section + subject."""
    if user.get("role") != "teacher":
        return
    await assert_teacher_section_access(user, section_id)
    subjects = await assigned_subject_ids_for_teacher(user["id"], section_id, academic_year_id)
    if subjects and subject_id not in subjects:
        raise HTTPException(403, "You are not assigned to teach this subject in this section")
    if not subjects:
        # Legacy: section-only assignment — allow any subject in section until class assignments exist
        return


# ------------------ Models ------------------
class YearIn(BaseModel):
    name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    entity_id: Literal["pws", "alpha", "both"] = ENTITY_PWS


class YearStatusIn(BaseModel):
    status: Literal["open", "closed", "archived"]


class GradeIn(BaseModel):
    academic_year_id: str
    name: str
    sort_order: int = 0
    entity_id: Literal["pws", "alpha", "both"] = ENTITY_PWS


class SectionIn(BaseModel):
    academic_year_id: str
    grade_id: str
    name: str
    entity_id: Literal["pws", "alpha", "both"] = ENTITY_PWS


class SubjectIn(BaseModel):
    academic_year_id: str
    name: str
    code: Optional[str] = None
    sort_order: int = 0
    grade_ids: List[str] = []
    section_ids: List[str] = []
    entity_id: Literal["pws", "alpha", "both"] = ENTITY_PWS


class SubjectScopeIn(BaseModel):
    grade_ids: List[str] = []
    section_ids: List[str] = []


class ClassAssignmentIn(BaseModel):
    teacher_user_id: str
    academic_year_id: str
    grade_id: str
    section_id: str
    subject_id: str


class TeacherAssignmentIn(BaseModel):
    """Legacy section-only assignment (backward compatible)."""
    teacher_user_id: str
    section_id: str
    academic_year_id: Optional[str] = None


# ------------------ Academic years ------------------
@router.get("/years")
async def list_years(user: dict = Depends(get_current_user)):
    if not (_can_manage_academic(user) or is_admin(user) or user.get("role") in ("principal", "vice_principal", "teacher")):
        raise HTTPException(403, "Not allowed")
    return await db.academic_years.find({"entity_id": ENTITY_PWS}, {"_id": 0}).sort("start_date", -1).to_list(50)


@router.get("/years/{year_id}")
async def get_year(year_id: str, user: dict = Depends(get_current_user)):
    if not (_can_manage_academic(user) or user.get("role") in ("principal", "vice_principal", "teacher")):
        raise HTTPException(403, "Not allowed")
    return await _year_by_id(year_id)


@router.post("/years")
async def create_year(payload: YearIn, user: dict = Depends(get_current_user)):
    _assert_manage_academic(user)
    existing_open = await db.academic_years.find_one({"entity_id": payload.entity_id, "status": "open"})
    doc = {
        "id": str(uuid.uuid4()),
        "name": payload.name.strip(),
        "entity_id": payload.entity_id,
        "status": "open" if not existing_open else "closed",
        "start_date": payload.start_date,
        "end_date": payload.end_date,
        "created_at": now_utc().isoformat(),
        "created_by": user["id"],
    }
    await db.academic_years.insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.patch("/years/{year_id}/status")
async def update_year_status(year_id: str, payload: YearStatusIn, user: dict = Depends(get_current_user)):
    _assert_manage_academic(user)
    year = await _year_by_id(year_id)
    if year.get("status") == "archived":
        raise HTTPException(403, "Archived years cannot be modified")
    if payload.status == "open":
        await db.academic_years.update_many(
            {"entity_id": year.get("entity_id", ENTITY_PWS), "status": "open", "id": {"$ne": year_id}},
            {"$set": {"status": "closed"}},
        )
    await db.academic_years.update_one(
        {"id": year_id},
        {"$set": {"status": payload.status, "updated_at": now_utc().isoformat()}},
    )
    return await _year_by_id(year_id)


# ------------------ Grades ------------------
@router.get("/grades")
async def list_grades(academic_year_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    if not (_can_manage_academic(user) or is_admin(user) or user.get("role") in ("principal", "vice_principal", "teacher")):
        raise HTTPException(403, "Not allowed")
    q: dict = {"entity_id": ENTITY_PWS}
    if academic_year_id:
        q["academic_year_id"] = academic_year_id
    return await db.grades.find(q, {"_id": 0}).sort("sort_order", 1).to_list(100)


@router.post("/grades")
async def create_grade(payload: GradeIn, user: dict = Depends(get_current_user)):
    _assert_manage_academic(user)
    year = await _year_by_id(payload.academic_year_id)
    _assert_year_writable(year)
    doc = {
        "id": str(uuid.uuid4()),
        "academic_year_id": payload.academic_year_id,
        "name": payload.name.strip(),
        "entity_id": payload.entity_id,
        "sort_order": payload.sort_order,
        "created_at": now_utc().isoformat(),
    }
    await db.grades.insert_one(doc)
    doc.pop("_id", None)
    return doc


# ------------------ Sections ------------------
@router.get("/sections")
async def list_sections(
    academic_year_id: Optional[str] = None,
    grade_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    if not (_can_manage_academic(user) or is_admin(user) or user.get("role") in ("principal", "vice_principal", "teacher")
            or get_perm(user, "add_students") or get_perm(user, "mark_student_attendance")):
        raise HTTPException(403, "Not allowed")
    q: dict = {"entity_id": ENTITY_PWS}
    if academic_year_id:
        q["academic_year_id"] = academic_year_id
    if grade_id:
        q["grade_id"] = grade_id
    if user.get("role") == "teacher":
        assigned = await assigned_section_ids_for_teacher(user["id"], academic_year_id)
        q["id"] = {"$in": assigned} if assigned else {"$in": []}
    return await db.sections.find(q, {"_id": 0}).sort("label", 1).to_list(200)


@router.post("/sections")
async def create_section(payload: SectionIn, user: dict = Depends(get_current_user)):
    _assert_manage_academic(user)
    year = await _year_by_id(payload.academic_year_id)
    _assert_year_writable(year)
    grade = await db.grades.find_one({"id": payload.grade_id, "academic_year_id": payload.academic_year_id})
    if not grade:
        raise HTTPException(404, "Grade not found for this academic year")
    label = f"{grade['name']}-{payload.name.strip()}"
    if await db.sections.find_one({"academic_year_id": payload.academic_year_id, "label": label}):
        raise HTTPException(400, f"Section {label} already exists")
    doc = {
        "id": str(uuid.uuid4()),
        "academic_year_id": payload.academic_year_id,
        "grade_id": payload.grade_id,
        "grade_name": grade["name"],
        "name": payload.name.strip(),
        "label": label,
        "entity_id": payload.entity_id,
        "created_at": now_utc().isoformat(),
    }
    await db.sections.insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.get("/sections/for-attendance")
async def sections_for_attendance(user: dict = Depends(get_current_user)):
    if not (is_admin(user) or get_perm(user, "mark_student_attendance")):
        raise HTTPException(403, "Student attendance permission required")
    open_year = await get_open_academic_year()
    if not open_year:
        return {"academic_year": None, "sections": []}
    q: dict = {"academic_year_id": open_year["id"], "entity_id": ENTITY_PWS}
    if user.get("role") == "teacher":
        assigned = await assigned_section_ids_for_teacher(user["id"], open_year["id"])
        if not assigned:
            return {"academic_year": open_year, "sections": []}
        q["id"] = {"$in": assigned}
    sections = await db.sections.find(q, {"_id": 0}).sort("label", 1).to_list(200)
    return {"academic_year": open_year, "sections": sections}


# ------------------ Subjects ------------------
@router.get("/subjects")
async def list_subjects(
    academic_year_id: Optional[str] = None,
    grade_id: Optional[str] = None,
    section_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    if not (_can_manage_academic(user) or user.get("role") in ("principal", "vice_principal", "teacher")
            or get_perm(user, "mark_student_attendance")):
        raise HTTPException(403, "Not allowed")
    q: dict = {"entity_id": ENTITY_PWS}
    if academic_year_id:
        q["academic_year_id"] = academic_year_id
    rows = await db.subjects.find(q, {"_id": 0}).sort("sort_order", 1).to_list(100)
    if grade_id:
        rows = [s for s in rows if not s.get("grade_ids") or grade_id in s.get("grade_ids", [])]
    if section_id:
        rows = [s for s in rows if not s.get("section_ids") or section_id in s.get("section_ids", [])]
    if user.get("role") == "teacher" and section_id:
        allowed = await assigned_subject_ids_for_teacher(user["id"], section_id, academic_year_id)
        if allowed:
            rows = [s for s in rows if s["id"] in allowed]
    return rows


@router.post("/subjects")
async def create_subject(payload: SubjectIn, user: dict = Depends(get_current_user)):
    _assert_manage_academic(user)
    year = await _year_by_id(payload.academic_year_id)
    _assert_year_writable(year)
    doc = {
        "id": str(uuid.uuid4()),
        "academic_year_id": payload.academic_year_id,
        "name": payload.name.strip(),
        "code": (payload.code or payload.name[:3]).upper(),
        "sort_order": payload.sort_order,
        "grade_ids": payload.grade_ids,
        "section_ids": payload.section_ids,
        "entity_id": payload.entity_id,
        "created_at": now_utc().isoformat(),
    }
    await db.subjects.insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.patch("/subjects/{subject_id}/scope")
async def update_subject_scope(subject_id: str, payload: SubjectScopeIn, user: dict = Depends(get_current_user)):
    _assert_manage_academic(user)
    subject = await db.subjects.find_one({"id": subject_id}, {"_id": 0})
    if not subject:
        raise HTTPException(404, "Subject not found")
    year = await _year_by_id(subject["academic_year_id"])
    _assert_year_writable(year)
    await db.subjects.update_one(
        {"id": subject_id},
        {"$set": {"grade_ids": payload.grade_ids, "section_ids": payload.section_ids}},
    )
    return await db.subjects.find_one({"id": subject_id}, {"_id": 0})


# ------------------ Teacher class assignments (year + grade + section + subject) ------------------
@router.get("/class-assignments")
async def list_class_assignments(
    teacher_user_id: Optional[str] = None,
    academic_year_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    if user.get("role") == "teacher":
        teacher_user_id = user["id"]
    elif not _can_manage_academic(user):
        raise HTTPException(403, "Not allowed")
    q: dict = {}
    if teacher_user_id:
        q["teacher_user_id"] = teacher_user_id
    if academic_year_id:
        q["academic_year_id"] = academic_year_id
    else:
        open_year = await get_open_academic_year()
        if open_year:
            q["academic_year_id"] = open_year["id"]
    rows = await db.teacher_class_assignments.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    out = []
    for r in rows:
        section = await db.sections.find_one({"id": r["section_id"]}, {"_id": 0, "label": 1, "grade_name": 1})
        grade = await db.grades.find_one({"id": r["grade_id"]}, {"_id": 0, "name": 1})
        subject = await db.subjects.find_one({"id": r["subject_id"]}, {"_id": 0, "name": 1, "code": 1})
        teacher = await db.users.find_one({"id": r["teacher_user_id"]}, {"_id": 0, "name": 1, "email": 1})
        out.append({**r, "section": section, "grade": grade, "subject": subject, "teacher": teacher})
    return out


@router.get("/my-assignments")
async def my_class_assignments(user: dict = Depends(get_current_user)):
    if user.get("role") != "teacher":
        raise HTTPException(403, "Teacher role required")
    open_year = await get_open_academic_year()
    q: dict = {"teacher_user_id": user["id"]}
    if open_year:
        q["academic_year_id"] = open_year["id"]
    rows = await db.teacher_class_assignments.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    out = []
    for r in rows:
        section = await db.sections.find_one({"id": r["section_id"]}, {"_id": 0, "label": 1, "grade_name": 1})
        grade = await db.grades.find_one({"id": r["grade_id"]}, {"_id": 0, "name": 1})
        subject = await db.subjects.find_one({"id": r["subject_id"]}, {"_id": 0, "name": 1, "code": 1})
        out.append({**r, "section": section, "grade": grade, "subject": subject})
    return out


@router.post("/class-assignments")
async def create_class_assignment(payload: ClassAssignmentIn, user: dict = Depends(get_current_user)):
    _assert_manage_academic(user)
    year = await _year_by_id(payload.academic_year_id)
    _assert_year_writable(year)
    teacher = await db.users.find_one({"id": payload.teacher_user_id, "role": "teacher"})
    if not teacher:
        raise HTTPException(404, "Teacher user not found")
    section = await db.sections.find_one({"id": payload.section_id, "academic_year_id": payload.academic_year_id})
    if not section:
        raise HTTPException(404, "Section not found")
    if section.get("grade_id") != payload.grade_id:
        raise HTTPException(400, "Section does not belong to the selected grade")
    subject = await db.subjects.find_one({"id": payload.subject_id, "academic_year_id": payload.academic_year_id})
    if not subject:
        raise HTTPException(404, "Subject not found")
    existing = await db.teacher_class_assignments.find_one({
        "teacher_user_id": payload.teacher_user_id,
        "academic_year_id": payload.academic_year_id,
        "section_id": payload.section_id,
        "subject_id": payload.subject_id,
    })
    if existing:
        raise HTTPException(400, "This class assignment already exists")
    doc = {
        "id": str(uuid.uuid4()),
        "teacher_user_id": payload.teacher_user_id,
        "academic_year_id": payload.academic_year_id,
        "grade_id": payload.grade_id,
        "section_id": payload.section_id,
        "subject_id": payload.subject_id,
        "created_at": now_utc().isoformat(),
        "created_by": user["id"],
    }
    await db.teacher_class_assignments.insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.delete("/class-assignments/{assignment_id}")
async def delete_class_assignment(assignment_id: str, user: dict = Depends(get_current_user)):
    _assert_manage_academic(user)
    row = await db.teacher_class_assignments.find_one({"id": assignment_id})
    if not row:
        raise HTTPException(404, "Assignment not found")
    year = await _year_by_id(row["academic_year_id"])
    _assert_year_writable(year)
    await db.teacher_class_assignments.delete_one({"id": assignment_id})
    return {"ok": True}


# ------------------ Legacy section-only teacher assignments (backward compatible) ------------------
@router.get("/teacher-assignments")
async def list_teacher_assignments(
    teacher_user_id: Optional[str] = None,
    academic_year_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    _assert_manage_academic(user)
    q: dict = {}
    if teacher_user_id:
        q["teacher_user_id"] = teacher_user_id
    if academic_year_id:
        q["academic_year_id"] = academic_year_id
    else:
        open_year = await get_open_academic_year()
        if open_year:
            q["academic_year_id"] = open_year["id"]
    rows = await db.teacher_section_assignments.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    out = []
    for r in rows:
        section = await db.sections.find_one({"id": r["section_id"]}, {"_id": 0, "label": 1, "grade_name": 1})
        teacher = await db.users.find_one({"id": r["teacher_user_id"]}, {"_id": 0, "name": 1, "email": 1})
        out.append({**r, "section": section, "teacher": teacher})
    return out


@router.post("/teacher-assignments")
async def create_teacher_assignment(payload: TeacherAssignmentIn, user: dict = Depends(get_current_user)):
    _assert_manage_academic(user)
    teacher = await db.users.find_one({"id": payload.teacher_user_id, "role": "teacher"})
    if not teacher:
        raise HTTPException(404, "Teacher user not found")
    section = await db.sections.find_one({"id": payload.section_id})
    if not section:
        raise HTTPException(404, "Section not found")
    year_id = payload.academic_year_id or section["academic_year_id"]
    year = await _year_by_id(year_id)
    _assert_year_writable(year)
    existing = await db.teacher_section_assignments.find_one({
        "teacher_user_id": payload.teacher_user_id,
        "section_id": payload.section_id,
        "academic_year_id": year_id,
    })
    if existing:
        raise HTTPException(400, "Teacher already assigned to this section")
    doc = {
        "id": str(uuid.uuid4()),
        "teacher_user_id": payload.teacher_user_id,
        "section_id": payload.section_id,
        "academic_year_id": year_id,
        "created_at": now_utc().isoformat(),
        "created_by": user["id"],
    }
    await db.teacher_section_assignments.insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.delete("/teacher-assignments/{assignment_id}")
async def delete_teacher_assignment(assignment_id: str, user: dict = Depends(get_current_user)):
    _assert_manage_academic(user)
    row = await db.teacher_section_assignments.find_one({"id": assignment_id})
    if row:
        year = await _year_by_id(row["academic_year_id"])
        _assert_year_writable(year)
    result = await db.teacher_section_assignments.delete_one({"id": assignment_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Assignment not found")
    return {"ok": True}


@router.get("/exam-terms")
async def list_exam_terms_legacy(academic_year_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    """Backward-compatible alias — see /marks/exam-terms."""
    from routers.marks import list_exam_terms
    return await list_exam_terms(academic_year_id=academic_year_id, user=user)


@router.get("/grading-scales")
async def list_grading_scales_legacy(academic_year_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    from routers.marks import list_grading_scales
    return await list_grading_scales(academic_year_id=academic_year_id, user=user)
