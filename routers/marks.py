"""Academic marks & assessment MVP — terms, assessments, grading, teacher grid, publish."""
import uuid
from typing import Optional, Literal, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from core import db, get_current_user, get_perm, is_super_admin, now_utc
from routers.academic import (
    assert_teacher_section_access,
    assert_teacher_subject_access,
    assigned_section_ids_for_teacher,
    get_open_academic_year,
    _year_by_id,
    _assert_year_writable,
)

router = APIRouter(prefix="/marks", tags=["marks"])

ENTITY_PWS = "pws"
MARK_STATUSES = ("draft", "final", "published")


def _can_enter(user: dict) -> bool:
    if user.get("role") == "coach" and not get_perm(user, "enter_academic_marks"):
        return False
    return is_super_admin(user) or get_perm(user, "enter_academic_marks") or get_perm(user, "manage_academic_structure")


def _can_view(user: dict) -> bool:
    if user.get("role") == "coach" and not get_perm(user, "view_academic_marks"):
        return False
    return _can_enter(user) or get_perm(user, "view_academic_marks")


def _can_manage(user: dict) -> bool:
    return is_super_admin(user) or get_perm(user, "manage_academic_structure")


def _assert_enter(user: dict) -> None:
    if not _can_enter(user):
        raise HTTPException(403, "Academic marks entry permission required")


def _assert_view(user: dict) -> None:
    if not _can_view(user):
        raise HTTPException(403, "Academic marks view permission required")


def _assert_manage(user: dict) -> None:
    if not _can_manage(user):
        raise HTTPException(403, "Academic structure management required")


async def default_grading_scale(academic_year_id: str) -> Optional[dict]:
    return await db.grading_scales.find_one(
        {"academic_year_id": academic_year_id, "entity_id": ENTITY_PWS, "is_default": True},
        {"_id": 0},
    )


def percentage_for_score(score: Optional[float], max_marks: int) -> Optional[float]:
    if score is None or not max_marks:
        return None
    return round((score / max_marks) * 100, 1)


def grade_for_score(score: Optional[float], bands: list[dict], max_marks: int = 100) -> Optional[str]:
    pct = percentage_for_score(score, max_marks)
    if pct is None:
        return None
    for b in sorted(bands, key=lambda x: x.get("min", 0), reverse=True):
        if b.get("min", 0) <= pct <= b.get("max", 100):
            return b.get("grade")
    return None


async def _get_assessment(assessment_id: str) -> dict:
    a = await db.assessments.find_one({"id": assessment_id}, {"_id": 0})
    if not a:
        raise HTTPException(404, "Assessment not found")
    return a


async def _assert_teacher_assessment_access(user: dict, assessment: dict) -> None:
    await assert_teacher_section_access(user, assessment["section_id"])
    await assert_teacher_subject_access(
        user, assessment["section_id"], assessment["subject_id"], assessment.get("academic_year_id"),
    )


async def log_marks_audit(before: Optional[dict], after: dict, user: dict, reason: str, action: str = "correction") -> None:
    await db.marks_audit.insert_one({
        "id": str(uuid.uuid4()),
        "mark_id": after.get("id") or (before or {}).get("id"),
        "assessment_id": after.get("assessment_id"),
        "person_id": after.get("person_id"),
        "before_status": (before or {}).get("status"),
        "after_status": after.get("status"),
        "before_marks": (before or {}).get("marks_obtained"),
        "after_marks": after.get("marks_obtained"),
        "reason": reason,
        "action": action,
        "changed_by": user["id"],
        "changed_by_name": user.get("name"),
        "changed_at": now_utc().isoformat(),
    })


# ------------------ Exam terms (admin) ------------------
class ExamTermIn(BaseModel):
    academic_year_id: str
    name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_active: bool = True


@router.get("/exam-terms")
async def list_exam_terms(academic_year_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    _assert_view(user)
    q: dict = {"entity_id": ENTITY_PWS}
    if academic_year_id:
        q["academic_year_id"] = academic_year_id
    return await db.exam_terms.find(q, {"_id": 0}).sort("start_date", 1).to_list(50)


@router.post("/exam-terms")
async def create_exam_term(payload: ExamTermIn, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    year = await _year_by_id(payload.academic_year_id)
    _assert_year_writable(year)
    doc = {
        "id": str(uuid.uuid4()),
        "academic_year_id": payload.academic_year_id,
        "name": payload.name.strip(),
        "start_date": payload.start_date,
        "end_date": payload.end_date,
        "is_active": payload.is_active,
        "entity_id": ENTITY_PWS,
        "created_at": now_utc().isoformat(),
        "created_by": user["id"],
    }
    await db.exam_terms.insert_one(doc)
    doc.pop("_id", None)
    return doc


# ------------------ Assessments (admin) ------------------
class AssessmentIn(BaseModel):
    academic_year_id: str
    exam_term_id: str
    section_id: str
    subject_id: str
    name: str
    max_marks: int = Field(100, ge=1, le=500)


@router.get("/assessments")
async def list_assessments(
    academic_year_id: Optional[str] = None,
    exam_term_id: Optional[str] = None,
    section_id: Optional[str] = None,
    subject_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    _assert_view(user)
    q: dict = {"entity_id": ENTITY_PWS}
    if academic_year_id:
        q["academic_year_id"] = academic_year_id
    if exam_term_id:
        q["exam_term_id"] = exam_term_id
    if section_id:
        q["section_id"] = section_id
    if subject_id:
        q["subject_id"] = subject_id
    if user.get("role") == "teacher":
        year = academic_year_id or (await get_open_academic_year() or {}).get("id")
        if not year:
            return []
        assignments = await db.teacher_class_assignments.find(
            {"teacher_user_id": user["id"], "academic_year_id": year},
            {"_id": 0},
        ).to_list(200)
        if not assignments:
            return []
        allowed = [
            {"$and": [{"section_id": a["section_id"]}, {"subject_id": a["subject_id"]}]}
            for a in assignments
        ]
        q["$or"] = allowed
    rows = await db.assessments.find(q, {"_id": 0}).sort("name", 1).to_list(200)
    out = []
    for r in rows:
        section = await db.sections.find_one({"id": r["section_id"]}, {"_id": 0, "label": 1, "grade_name": 1})
        subject = await db.subjects.find_one({"id": r["subject_id"]}, {"_id": 0, "name": 1})
        term = await db.exam_terms.find_one({"id": r["exam_term_id"]}, {"_id": 0, "name": 1})
        out.append({**r, "section": section, "subject": subject, "exam_term": term})
    return out


@router.post("/assessments")
async def create_assessment(payload: AssessmentIn, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    year = await _year_by_id(payload.academic_year_id)
    _assert_year_writable(year)
    section = await db.sections.find_one({"id": payload.section_id, "academic_year_id": payload.academic_year_id})
    if not section:
        raise HTTPException(404, "Section not found")
    subject = await db.subjects.find_one({"id": payload.subject_id, "academic_year_id": payload.academic_year_id})
    if not subject:
        raise HTTPException(404, "Subject not found")
    term = await db.exam_terms.find_one({"id": payload.exam_term_id})
    if not term:
        raise HTTPException(404, "Exam term not found")
    existing = await db.assessments.find_one({
        "exam_term_id": payload.exam_term_id,
        "section_id": payload.section_id,
        "subject_id": payload.subject_id,
        "name": payload.name.strip(),
    })
    if existing:
        raise HTTPException(400, "Assessment already exists for this combination")
    doc = {
        "id": str(uuid.uuid4()),
        "academic_year_id": payload.academic_year_id,
        "exam_term_id": payload.exam_term_id,
        "section_id": payload.section_id,
        "subject_id": payload.subject_id,
        "grade_id": section.get("grade_id"),
        "name": payload.name.strip(),
        "max_marks": payload.max_marks,
        "entity_id": ENTITY_PWS,
        "created_at": now_utc().isoformat(),
        "created_by": user["id"],
    }
    await db.assessments.insert_one(doc)
    doc.pop("_id", None)
    return doc


# ------------------ Grading scales (admin) ------------------
class GradingBand(BaseModel):
    min: float
    max: float
    grade: str
    description: Optional[str] = None


class GradingScaleIn(BaseModel):
    academic_year_id: str
    name: str
    bands: List[GradingBand]
    is_default: bool = False


@router.get("/grading-scales")
async def list_grading_scales(academic_year_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    _assert_view(user)
    q: dict = {"entity_id": ENTITY_PWS}
    if academic_year_id:
        q["academic_year_id"] = academic_year_id
    return await db.grading_scales.find(q, {"_id": 0}).sort("name", 1).to_list(20)


@router.post("/grading-scales")
async def create_grading_scale(payload: GradingScaleIn, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    year = await _year_by_id(payload.academic_year_id)
    _assert_year_writable(year)
    if payload.is_default:
        await db.grading_scales.update_many(
            {"academic_year_id": payload.academic_year_id, "is_default": True},
            {"$set": {"is_default": False}},
        )
    doc = {
        "id": str(uuid.uuid4()),
        "academic_year_id": payload.academic_year_id,
        "name": payload.name.strip(),
        "bands": [b.model_dump() for b in payload.bands],
        "is_default": payload.is_default,
        "entity_id": ENTITY_PWS,
        "created_at": now_utc().isoformat(),
        "created_by": user["id"],
    }
    await db.grading_scales.insert_one(doc)
    doc.pop("_id", None)
    return doc


# ------------------ Teacher combinations ------------------
@router.get("/my-combinations")
async def my_mark_combinations(
    academic_year_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    _assert_enter(user)
    year_id = academic_year_id
    if not year_id:
        open_year = await get_open_academic_year()
        year_id = open_year["id"] if open_year else None
    if not year_id:
        return {"academic_year_id": None, "combinations": []}

    if user.get("role") == "teacher":
        rows = await db.teacher_class_assignments.find(
            {"teacher_user_id": user["id"], "academic_year_id": year_id},
            {"_id": 0},
        ).to_list(100)
    else:
        sections = await db.sections.find({"academic_year_id": year_id, "entity_id": ENTITY_PWS}, {"_id": 0}).to_list(100)
        subjects = await db.subjects.find({"academic_year_id": year_id, "entity_id": ENTITY_PWS}, {"_id": 0}).to_list(100)
        rows = []
        for sec in sections:
            for sub in subjects:
                grade_ids = sub.get("grade_ids") or []
                section_ids = sub.get("section_ids") or []
                if grade_ids and sec.get("grade_id") not in grade_ids:
                    continue
                if section_ids and sec["id"] not in section_ids:
                    continue
                rows.append({
                    "academic_year_id": year_id,
                    "grade_id": sec.get("grade_id"),
                    "section_id": sec["id"],
                    "subject_id": sub["id"],
                })

    combos = []
    seen = set()
    for r in rows:
        key = (r["section_id"], r["subject_id"])
        if key in seen:
            continue
        seen.add(key)
        section = await db.sections.find_one({"id": r["section_id"]}, {"_id": 0})
        subject = await db.subjects.find_one({"id": r["subject_id"]}, {"_id": 0})
        grade = await db.grades.find_one({"id": r.get("grade_id")}, {"_id": 0, "name": 1})
        assessments = await db.assessments.find(
            {"section_id": r["section_id"], "subject_id": r["subject_id"], "academic_year_id": year_id},
            {"_id": 0},
        ).to_list(50)
        combos.append({
            "academic_year_id": year_id,
            "grade_id": r.get("grade_id"),
            "grade_name": (grade or {}).get("name") or (section or {}).get("grade_name"),
            "section_id": r["section_id"],
            "section_label": (section or {}).get("label"),
            "subject_id": r["subject_id"],
            "subject_name": (subject or {}).get("name"),
            "assessments": assessments,
        })
    return {"academic_year_id": year_id, "combinations": combos}


# ------------------ Marks grid & batch save ------------------
class MarkEntry(BaseModel):
    person_id: str
    marks_obtained: Optional[float] = Field(None, ge=0)


class MarksBatchIn(BaseModel):
    assessment_id: str
    entries: list[MarkEntry]
    status: Literal["draft", "final"] = "draft"


@router.get("/grid")
async def marks_grid(assessment_id: str, user: dict = Depends(get_current_user)):
    _assert_view(user)
    assessment = await _get_assessment(assessment_id)
    if user.get("role") == "teacher":
        await _assert_teacher_assessment_access(user, assessment)

    section = await db.sections.find_one({"id": assessment["section_id"]}, {"_id": 0})
    subject = await db.subjects.find_one({"id": assessment["subject_id"]}, {"_id": 0})
    term = await db.exam_terms.find_one({"id": assessment["exam_term_id"]}, {"_id": 0})
    year = await db.academic_years.find_one({"id": assessment["academic_year_id"]}, {"_id": 0, "name": 1, "status": 1})

    students = await db.people.find(
        {"kind": "student", "section_id": assessment["section_id"]},
        {"_id": 0, "id": 1, "name": 1},
    ).sort("name", 1).to_list(200)

    existing = await db.academic_marks.find({"assessment_id": assessment_id}, {"_id": 0}).to_list(500)
    by_person = {m["person_id"]: m for m in existing}

    scale = await default_grading_scale(assessment["academic_year_id"])
    bands = (scale or {}).get("bands") or []
    max_marks = assessment.get("max_marks", 100)

    rows = []
    for s in students:
        m = by_person.get(s["id"])
        score = m.get("marks_obtained") if m else None
        rows.append({
            "person_id": s["id"],
            "name": s["name"],
            "marks_obtained": score,
            "max_marks": max_marks,
            "percentage": m.get("percentage") if m else percentage_for_score(score, max_marks),
            "grade": m.get("grade") if m else grade_for_score(score, bands, max_marks),
            "status": m.get("status") if m else None,
            "mark_id": m.get("id") if m else None,
            "entered_by_name": m.get("entered_by_name") if m else None,
            "entered_at": m.get("entered_at") if m else None,
        })

    return {
        "academic_year": year,
        "assessment": assessment,
        "section": section,
        "subject": subject,
        "exam_term": term,
        "max_marks": max_marks,
        "grading_scale": scale,
        "students": rows,
    }


@router.post("/batch")
async def save_marks_batch(payload: MarksBatchIn, user: dict = Depends(get_current_user)):
    _assert_enter(user)
    assessment = await _get_assessment(payload.assessment_id)
    if user.get("role") == "teacher":
        await _assert_teacher_assessment_access(user, assessment)

    max_marks = assessment.get("max_marks", 100)
    person_ids = [e.person_id for e in payload.entries]
    valid_students = await db.people.find(
        {"kind": "student", "section_id": assessment["section_id"], "id": {"$in": person_ids}},
        {"id": 1, "_id": 0},
    ).to_list(500)
    valid_ids = {s["id"] for s in valid_students}

    scale = await default_grading_scale(assessment["academic_year_id"])
    bands = (scale or {}).get("bands") or []
    ts = now_utc().isoformat()
    saved = 0

    for entry in payload.entries:
        if entry.person_id not in valid_ids:
            raise HTTPException(400, f"Student {entry.person_id} is not in this section")
        if entry.marks_obtained is not None and entry.marks_obtained > max_marks:
            raise HTTPException(400, f"Marks {entry.marks_obtained} exceed maximum {max_marks}")

        existing = await db.academic_marks.find_one({
            "person_id": entry.person_id,
            "assessment_id": payload.assessment_id,
        })

        if existing and existing.get("status") == "final" and user.get("role") == "teacher":
            raise HTTPException(403, "Finalized marks cannot be edited by teachers. Contact an administrator.")
        if existing and existing.get("status") == "published":
            raise HTTPException(403, "Published marks are locked. Use admin correction.")

        if entry.marks_obtained is None:
            if existing and existing.get("status") in ("final", "published"):
                raise HTTPException(403, "Cannot clear finalized or published marks")
            await db.academic_marks.delete_many({
                "person_id": entry.person_id,
                "assessment_id": payload.assessment_id,
            })
            continue

        pct = percentage_for_score(entry.marks_obtained, max_marks)
        grade = grade_for_score(entry.marks_obtained, bands, max_marks)
        doc = {
            "person_id": entry.person_id,
            "assessment_id": payload.assessment_id,
            "section_id": assessment["section_id"],
            "subject_id": assessment["subject_id"],
            "exam_term_id": assessment["exam_term_id"],
            "academic_year_id": assessment["academic_year_id"],
            "marks_obtained": entry.marks_obtained,
            "max_marks": max_marks,
            "percentage": pct,
            "grade": grade,
            "status": payload.status,
            "entity_id": ENTITY_PWS,
            "entered_by": user["id"],
            "entered_by_name": user.get("name"),
            "entered_at": ts,
            "updated_at": ts,
        }
        if payload.status == "final":
            doc["finalized_by"] = user["id"]
            doc["finalized_at"] = ts

        if existing:
            if existing.get("status") == "final" and payload.status == "draft" and user.get("role") == "teacher":
                raise HTTPException(403, "Cannot revert finalized marks to draft")
            await db.academic_marks.update_one({"id": existing["id"]}, {"$set": doc})
        else:
            doc["id"] = str(uuid.uuid4())
            doc["created_at"] = ts
            await db.academic_marks.insert_one(doc)
        saved += 1

    return {"ok": True, "saved": saved, "status": payload.status}


# ------------------ Publish & admin correction ------------------
class PublishIn(BaseModel):
    assessment_id: str


@router.post("/publish")
async def publish_assessment_marks(payload: PublishIn, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    assessment = await _get_assessment(payload.assessment_id)
    ts = now_utc().isoformat()
    result = await db.academic_marks.update_many(
        {"assessment_id": payload.assessment_id, "status": "final"},
        {"$set": {"status": "published", "published_at": ts, "published_by": user["id"]}},
    )
    return {"published": result.modified_count, "assessment_id": payload.assessment_id}


class MarksCorrectionIn(BaseModel):
    mark_id: str
    marks_obtained: Optional[float] = Field(None, ge=0)
    status: Optional[Literal["draft", "final", "published"]] = None
    reason: str


@router.post("/correct")
async def correct_marks(payload: MarksCorrectionIn, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    if not payload.reason.strip():
        raise HTTPException(400, "Audit reason is required")
    row = await db.academic_marks.find_one({"id": payload.mark_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Mark not found")
    if row.get("status") not in ("final", "published"):
        raise HTTPException(400, "Only finalized or published marks can be corrected via audit")

    assessment = await _get_assessment(row["assessment_id"])
    max_marks = row.get("max_marks") or assessment.get("max_marks", 100)
    if payload.marks_obtained is not None and payload.marks_obtained > max_marks:
        raise HTTPException(400, f"Marks exceed maximum {max_marks}")

    scale = await default_grading_scale(row["academic_year_id"])
    bands = (scale or {}).get("bands") or []
    ts = now_utc().isoformat()
    updated = {**row}
    if payload.marks_obtained is not None:
        updated["marks_obtained"] = payload.marks_obtained
        updated["percentage"] = percentage_for_score(payload.marks_obtained, max_marks)
        updated["grade"] = grade_for_score(payload.marks_obtained, bands, max_marks)
    if payload.status:
        updated["status"] = payload.status
        if payload.status == "draft":
            updated["reopened_at"] = ts
            updated["reopened_by"] = user["id"]
    updated["entered_by"] = user["id"]
    updated["entered_by_name"] = user.get("name")
    updated["entered_at"] = ts
    updated["updated_at"] = ts
    updated["corrected"] = True

    await db.academic_marks.update_one({"id": payload.mark_id}, {"$set": updated})
    await log_marks_audit(row, updated, user, payload.reason.strip(), action="admin_correction")
    return updated


@router.get("/audit")
async def marks_audit_history(
    mark_id: Optional[str] = None,
    assessment_id: Optional[str] = None,
    limit: int = 100,
    user: dict = Depends(get_current_user),
):
    _assert_manage(user)
    q: dict = {}
    if mark_id:
        q["mark_id"] = mark_id
    if assessment_id:
        q["assessment_id"] = assessment_id
    return await db.marks_audit.find(q, {"_id": 0}).sort("changed_at", -1).to_list(min(limit, 200))


# ------------------ Published marks (student / parent) ------------------
async def _published_marks_for_person(person_id: str) -> list[dict]:
    marks = await db.academic_marks.find(
        {"person_id": person_id, "status": "published"},
        {"_id": 0},
    ).to_list(200)
    subject_ids = list({m["subject_id"] for m in marks})
    assessment_ids = list({m["assessment_id"] for m in marks if m.get("assessment_id")})
    subjects = {}
    if subject_ids:
        subs = await db.subjects.find({"id": {"$in": subject_ids}}, {"_id": 0}).to_list(50)
        subjects = {s["id"]: s for s in subs}
    assessments = {}
    if assessment_ids:
        rows = await db.assessments.find({"id": {"$in": assessment_ids}}, {"_id": 0}).to_list(50)
        assessments = {a["id"]: a for a in rows}
    term_ids = list({m["exam_term_id"] for m in marks})
    terms = {}
    if term_ids:
        trows = await db.exam_terms.find({"id": {"$in": term_ids}}, {"_id": 0}).to_list(20)
        terms = {t["id"]: t for t in trows}
    enriched = []
    for m in marks:
        a = assessments.get(m.get("assessment_id") or "")
        enriched.append({
            **m,
            "subject_name": (subjects.get(m["subject_id"]) or {}).get("name"),
            "exam_term_name": (terms.get(m["exam_term_id"]) or {}).get("name"),
            "assessment_name": (a or {}).get("name"),
        })
    return enriched


@router.get("/published/{person_id}")
async def published_marks(person_id: str, user: dict = Depends(get_current_user)):
    person = await db.people.find_one({"id": person_id, "kind": "student"}, {"_id": 0})
    if not person:
        raise HTTPException(404, "Student not found")

    if user.get("role") == "parent":
        if person_id not in (user.get("linked_person_ids") or []):
            raise HTTPException(404, "Ward not found")
    elif user.get("role") == "student":
        if user.get("name") != person.get("name"):
            linked = user.get("linked_person_ids") or []
            if person_id not in linked:
                raise HTTPException(403, "You can only view your own marks")
    elif not _can_view(user):
        raise HTTPException(403, "Not allowed")

    marks = await _published_marks_for_person(person_id)
    return {"person_id": person_id, "marks": marks}


@router.get("/student/{person_id}")
async def student_marks(person_id: str, exam_term_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    """Staff/teacher view — includes draft/final. Parents should use /published."""
    _assert_view(user)
    person = await db.people.find_one({"id": person_id, "kind": "student"}, {"_id": 0})
    if not person:
        raise HTTPException(404, "Student not found")
    if user.get("role") == "teacher":
        await assert_teacher_section_access(user, person.get("section_id") or "")

    q: dict = {"person_id": person_id}
    if exam_term_id:
        q["exam_term_id"] = exam_term_id
    marks = await db.academic_marks.find(q, {"_id": 0}).to_list(200)
    subject_ids = list({m["subject_id"] for m in marks})
    subjects = {}
    if subject_ids:
        subs = await db.subjects.find({"id": {"$in": subject_ids}}, {"_id": 0}).to_list(50)
        subjects = {s["id"]: s for s in subs}
    term_ids = list({m["exam_term_id"] for m in marks})
    terms = {}
    if term_ids:
        trows = await db.exam_terms.find({"id": {"$in": term_ids}}, {"_id": 0}).to_list(20)
        terms = {t["id"]: t for t in trows}

    enriched = []
    for m in marks:
        enriched.append({
            **m,
            "subject": subjects.get(m["subject_id"]),
            "exam_term": terms.get(m["exam_term_id"]),
        })
    return {"person_id": person_id, "marks": enriched}


@router.get("/sections")
async def marks_sections(user: dict = Depends(get_current_user)):
    _assert_enter(user)
    if user.get("role") == "teacher":
        year = await get_open_academic_year()
        if not year:
            return {"sections": []}
        sids = await assigned_section_ids_for_teacher(user["id"], year["id"])
        if not sids:
            return {"sections": []}
        sections = await db.sections.find({"id": {"$in": sids}}, {"_id": 0}).sort("label", 1).to_list(50)
        return {"sections": sections}
    sections = await db.sections.find({"entity_id": ENTITY_PWS}, {"_id": 0}).sort("label", 1).to_list(100)
    return {"sections": sections}


# Backward-compat alias used by tests / legacy clients
_default_grading_scale = default_grading_scale
