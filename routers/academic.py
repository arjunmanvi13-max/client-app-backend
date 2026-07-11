"""Academic structure — years, grades, sections, teacher assignments (PWS)."""
import uuid
from typing import Optional, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core import (
    db, get_current_user, is_admin, is_super_admin, get_perm, assert_perm, now_utc,
)

router = APIRouter(prefix="/academic", tags=["academic"])

ENTITY_PWS = "pws"


def _can_manage_academic(user: dict) -> bool:
    return is_super_admin(user) or get_perm(user, "manage_academic_structure")


def _assert_manage_academic(user: dict) -> None:
    if not _can_manage_academic(user):
        raise HTTPException(403, "Academic structure management permission required")


async def get_open_academic_year(entity_id: str = ENTITY_PWS) -> Optional[dict]:
    return await db.academic_years.find_one(
        {"entity_id": entity_id, "status": "open"},
        {"_id": 0},
    )


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
    rows = await db.teacher_section_assignments.find(
        {"teacher_user_id": teacher_user_id, "academic_year_id": year},
        {"section_id": 1, "_id": 0},
    ).to_list(200)
    return [r["section_id"] for r in rows]


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


class YearIn(BaseModel):
    name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    entity_id: Literal["pws", "alpha", "both"] = ENTITY_PWS


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


class TeacherAssignmentIn(BaseModel):
    teacher_user_id: str
    section_id: str
    academic_year_id: Optional[str] = None


@router.get("/years")
async def list_years(user: dict = Depends(get_current_user)):
    if not (_can_manage_academic(user) or is_admin(user) or user.get("role") in ("principal", "vice_principal", "teacher")):
        raise HTTPException(403, "Not allowed")
    return await db.academic_years.find({}, {"_id": 0}).sort("start_date", -1).to_list(50)


@router.post("/years")
async def create_year(payload: YearIn, user: dict = Depends(get_current_user)):
    _assert_manage_academic(user)
    existing_open = await db.academic_years.find_one({"entity_id": payload.entity_id, "status": "open"})
    doc = {
        "id": str(uuid.uuid4()),
        "name": payload.name.strip(),
        "entity_id": payload.entity_id,
        "status": "open" if not existing_open else "planned",
        "start_date": payload.start_date,
        "end_date": payload.end_date,
        "created_at": now_utc().isoformat(),
        "created_by": user["id"],
    }
    await db.academic_years.insert_one(doc)
    doc.pop("_id", None)
    return doc


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
    year = await db.academic_years.find_one({"id": payload.academic_year_id})
    if not year:
        raise HTTPException(404, "Academic year not found")
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
    rows = await db.sections.find(q, {"_id": 0}).sort("label", 1).to_list(200)
    return rows


@router.post("/sections")
async def create_section(payload: SectionIn, user: dict = Depends(get_current_user)):
    _assert_manage_academic(user)
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
    """Sections the caller may use when marking student attendance."""
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
    result = await db.teacher_section_assignments.delete_one({"id": assignment_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Assignment not found")
    return {"ok": True}
