"""Report cards — scholastic/co-scholastic format, finalize/lock, PDF export."""
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from core import db, get_current_user, get_perm, is_super_admin, now_utc
from report_card_format import (
    CO_SCHOLASTIC_AREAS,
    FINALIZED_STATUSES,
    GRADING_SCALE_NOTE,
    aggregate_scholastic_rows,
    default_co_scholastic,
    enrich_card_computed,
    format_attendance,
    format_dob,
    render_report_card_pdf,
    validate_for_finalize,
)
from routers.academic import assert_teacher_section_access
from routers.marks import default_grading_scale, grade_for_score, percentage_for_score

router = APIRouter(prefix="/report-cards", tags=["report-cards"])

ENTITY_PWS = "pws"
SCHOOL_NAME = "Prarambhika World School"
SCHOOL_TAGLINE = "Excellence in Education & Character"


def _can_build(user: dict) -> bool:
    from rbac.guards import can_enter_marks
    from rbac.authorization import normalize_role
    from rbac.enums import UserRole
    if normalize_role(user.get("role", "")) == UserRole.ALPHA_COACH and not get_perm(user, "view_academic_marks"):
        return False
    return is_super_admin(user) or can_enter_marks(user) or get_perm(user, "view_academic_marks")


def _can_publish(user: dict) -> bool:
    from rbac.guards import can_manage_academic
    return is_super_admin(user) or can_manage_academic(user)


def _assert_build(user: dict) -> None:
    if not _can_build(user):
        raise HTTPException(403, "Report card permission required")


def _assert_publish(user: dict) -> None:
    if not _can_publish(user):
        raise HTTPException(403, "Only school administrators can finalize or publish report cards")


def _is_locked(card: dict) -> bool:
    return card.get("status") in FINALIZED_STATUSES


async def _audit(card_id: str, action: str, user: dict, detail: Optional[dict] = None) -> None:
    await db.report_card_audit.insert_one({
        "id": str(uuid.uuid4()),
        "card_id": card_id,
        "action": action,
        "actor_id": user.get("id"),
        "actor_name": user.get("name"),
        "detail": detail or {},
        "at": now_utc().isoformat(),
    })


async def _branding() -> dict:
    settings = await db.entity_settings.find_one({"entity_id": "pws"}, {"_id": 0})
    return {
        "school_name": (settings or {}).get("school_name") or SCHOOL_NAME,
        "tagline": (settings or {}).get("tagline") or SCHOOL_TAGLINE,
        "logo_url": (settings or {}).get("logo_url"),
    }


async def _attendance_counts(
    person_id: str,
    start_date: Optional[str],
    end_date: Optional[str],
) -> tuple[Optional[int], Optional[int], Optional[float]]:
    q: dict = {"person_id": person_id, "kind": "student"}
    if start_date or end_date:
        date_q: dict = {}
        if start_date:
            date_q["$gte"] = start_date
        if end_date:
            date_q["$lte"] = end_date
        q["date"] = date_q
    else:
        since = (now_utc() - timedelta(days=90)).strftime("%Y-%m-%d")
        q["date"] = {"$gte": since}
    records = await db.attendance.find(q, {"_id": 0, "status": 1}).to_list(2000)
    if not records:
        return None, None, None
    attended = sum(1 for r in records if r.get("status") in ("present", "late"))
    total = len(records)
    pct = round((attended / total) * 100, 1)
    return attended, total, pct


async def _coach_remark_for_student(person: dict, start_date: Optional[str], end_date: Optional[str]) -> Optional[str]:
    ents = person.get("entities") or []
    if "ALPHA" not in ents and person.get("organization") != "BOTH":
        return None
    player = await db.people.find_one({"kind": "player", "name": person.get("name")}, {"_id": 0, "id": 1})
    if not player:
        return None
    q: dict = {
        "player_id": player["id"],
        "status": "published",
        "$or": [{"schema_version": 2}, {"schema_version": {"$exists": False}}],
    }
    if start_date or end_date:
        dr: dict = {}
        if start_date:
            dr["$gte"] = start_date
        if end_date:
            dr["$lte"] = end_date
        q["date"] = dr
    row = await db.player_assessments.find_one(q, {"_id": 0}, sort=[("date", -1)])
    return (row or {}).get("coach_remark")


async def build_report_card_data(person_id: str, exam_term_id: str) -> dict:
    person = await db.people.find_one({"id": person_id, "kind": "student"}, {"_id": 0})
    if not person:
        raise HTTPException(404, "Student not found")
    term = await db.exam_terms.find_one({"id": exam_term_id}, {"_id": 0})
    if not term:
        raise HTTPException(404, "Exam term not found")

    section = None
    if person.get("section_id"):
        section = await db.sections.find_one({"id": person["section_id"]}, {"_id": 0})
    year_id = term.get("academic_year_id")
    year = await db.academic_years.find_one({"id": year_id}, {"_id": 0, "name": 1}) if year_id else None

    marks = await db.academic_marks.find(
        {
            "person_id": person_id,
            "exam_term_id": exam_term_id,
            "status": {"$in": ["final", "published"]},
        },
        {"_id": 0},
    ).to_list(200)

    assessment_ids = list({m["assessment_id"] for m in marks if m.get("assessment_id")})
    assessments: Dict[str, dict] = {}
    if assessment_ids:
        rows = await db.assessments.find({"id": {"$in": assessment_ids}}, {"_id": 0}).to_list(200)
        assessments = {a["id"]: a for a in rows}

    subject_ids = list({m["subject_id"] for m in marks if m.get("subject_id")})
    subjects: Dict[str, dict] = {}
    if subject_ids:
        subs = await db.subjects.find({"id": {"$in": subject_ids}}, {"_id": 0}).to_list(100)
        subjects = {s["id"]: s for s in subs}

    scale = await default_grading_scale(year_id) if year_id else None
    bands = (scale or {}).get("bands") or []

    scholastic_rows = aggregate_scholastic_rows(marks, assessments, subjects, bands)

    subject_rows = []
    for row in scholastic_rows:
        subject_rows.append({
            "subject_id": row.get("subject_id"),
            "subject_name": row.get("subject_name"),
            "marks_obtained": row.get("marks_obtained"),
            "max_marks": row.get("max_marks"),
            "percentage": row.get("percentage"),
            "grade": row.get("grade"),
        })

    att_present, att_total, att_pct = await _attendance_counts(
        person["id"], term.get("start_date"), term.get("end_date"),
    )
    suggested_coach = await _coach_remark_for_student(person, term.get("start_date"), term.get("end_date"))
    branding = await _branding()

    card: dict = {
        "person_id": person_id,
        "person_name": person.get("name"),
        "admission_number": person.get("admission_number"),
        "father_name": person.get("father_name") or person.get("guardian_name"),
        "mother_name": person.get("mother_name"),
        "dob": person.get("dob"),
        "section_id": person.get("section_id"),
        "grade_name": (section or {}).get("grade_name") or person.get("group", "").split("-")[0],
        "section_label": (section or {}).get("label") or person.get("group"),
        "exam_term_id": exam_term_id,
        "exam_term_name": term.get("name"),
        "academic_year_id": year_id,
        "academic_year_name": (year or {}).get("name"),
        "scholastic_rows": scholastic_rows,
        "subjects": subject_rows,
        "co_scholastic": default_co_scholastic(),
        "grading_scale_note": GRADING_SCALE_NOTE,
        "attendance_present": att_present,
        "attendance_total": att_total,
        "attendance_pct": att_pct,
        "attendance_display": format_attendance(att_present, att_total, att_pct),
        "has_alpha_participation": "ALPHA" in (person.get("entities") or []) or person.get("organization") == "BOTH",
        "suggested_coach_remark": suggested_coach,
        "branding": branding,
        "entity_id": ENTITY_PWS,
    }
    return enrich_card_computed(card, bands)


class ReportCardBuildIn(BaseModel):
    person_id: str
    exam_term_id: str


class TeacherRemarkIn(BaseModel):
    teacher_remark: str


class CoScholasticIn(BaseModel):
    music_dramatics: Optional[str] = None
    art_education: Optional[str] = None
    physical_education_yoga: Optional[str] = None


class ScholasticRowIn(BaseModel):
    subject_name: str
    subject_id: Optional[str] = None
    periodic_test: Optional[float] = None
    independent_assessment: Optional[float] = None
    written_assessment: Optional[float] = None
    project: Optional[float] = None
    group_discussion: Optional[float] = None
    theory: Optional[float] = None
    practical_viva: Optional[float] = None


class ReportCardUpdateIn(BaseModel):
    teacher_remark: Optional[str] = None
    scholastic_rows: Optional[List[ScholasticRowIn]] = None
    co_scholastic: Optional[CoScholasticIn] = None
    attendance_present: Optional[int] = None
    attendance_total: Optional[int] = None
    attendance_display: Optional[str] = None
    issue_date: Optional[str] = None


class PublishIn(BaseModel):
    coach_remark: Optional[str] = None


class ReopenIn(BaseModel):
    reason: str = Field(min_length=3)


async def _get_card(card_id: str) -> dict:
    card = await db.report_cards.find_one({"id": card_id}, {"_id": 0})
    if not card:
        raise HTTPException(404, "Report card not found")
    return card


async def _bands_for_card(card: dict) -> list:
    scale = await default_grading_scale(card.get("academic_year_id"))
    return (scale or {}).get("bands") or []


@router.post("/build")
async def build_report_card(payload: ReportCardBuildIn, user: dict = Depends(get_current_user)):
    """Build or refresh draft from saved marks and attendance."""
    _assert_build(user)
    person = await db.people.find_one({"id": payload.person_id, "kind": "student"}, {"_id": 0})
    if not person:
        raise HTTPException(404, "Student not found")
    if user.get("role") == "teacher" and person.get("section_id"):
        await assert_teacher_section_access(user, person["section_id"])

    data = await build_report_card_data(payload.person_id, payload.exam_term_id)
    ts = now_utc().isoformat()
    existing = await db.report_cards.find_one({
        "person_id": payload.person_id,
        "exam_term_id": payload.exam_term_id,
    })

    if existing and _is_locked(existing):
        raise HTTPException(403, "Finalized report cards cannot be rebuilt. Reopen via admin first.")

    preserved_co = (existing or {}).get("co_scholastic") or data.get("co_scholastic")
    doc = {
        **data,
        "status": existing.get("status") if existing and existing.get("status") not in FINALIZED_STATUSES else "draft",
        "teacher_remark": (existing or {}).get("teacher_remark"),
        "coach_remark": (existing or {}).get("coach_remark"),
        "approved_coach_remark": (existing or {}).get("approved_coach_remark"),
        "co_scholastic": preserved_co,
        "issue_date": (existing or {}).get("issue_date"),
        "updated_at": ts,
        "updated_by": user["id"],
        "built_at": ts,
        "built_by": user["id"],
    }
    if not existing:
        doc["id"] = str(uuid.uuid4())
        doc["created_at"] = ts
        doc["created_by"] = user["id"]
        await db.report_cards.insert_one(doc)
        await _audit(doc["id"], "created", user)
    else:
        doc["id"] = existing["id"]
        doc["created_at"] = existing.get("created_at")
        doc["created_by"] = existing.get("created_by")
        await db.report_cards.update_one({"id": existing["id"]}, {"$set": doc})
        await _audit(doc["id"], "rebuilt", user)
    doc.pop("_id", None)
    return doc


@router.patch("/{card_id}")
async def update_report_card_draft(card_id: str, payload: ReportCardUpdateIn, user: dict = Depends(get_current_user)):
    """Update draft report card fields (remarks, scholastic rows, co-scholastic, attendance)."""
    _assert_build(user)
    card = await _get_card(card_id)
    if _is_locked(card):
        raise HTTPException(403, "Reopen the report card before editing")
    if user.get("role") == "teacher" and card.get("section_id"):
        await assert_teacher_section_access(user, card["section_id"])

    bands = await _bands_for_card(card)
    patch: dict = {"updated_at": now_utc().isoformat(), "updated_by": user["id"]}

    if payload.teacher_remark is not None:
        patch["teacher_remark"] = payload.teacher_remark.strip()
    if payload.co_scholastic is not None:
        patch["co_scholastic"] = {**default_co_scholastic(), **payload.co_scholastic.dict(exclude_none=True)}
    if payload.attendance_present is not None:
        patch["attendance_present"] = payload.attendance_present
    if payload.attendance_total is not None:
        patch["attendance_total"] = payload.attendance_total
    if payload.attendance_display is not None:
        patch["attendance_display"] = payload.attendance_display.strip()
    if payload.issue_date is not None:
        patch["issue_date"] = payload.issue_date

    if payload.scholastic_rows is not None:
        rows = []
        for r in payload.scholastic_rows:
            row = r.dict()
            row["subject_name"] = (row.get("subject_name") or "").upper()
            rows.append(row)
        merged = {**card, "scholastic_rows": rows}
        enrich_card_computed(merged, bands)
        patch["scholastic_rows"] = merged["scholastic_rows"]
        patch["subjects"] = merged.get("subjects") or merged["scholastic_rows"]
        patch["total_obtained"] = merged["total_obtained"]
        patch["total_max"] = merged["total_max"]
        patch["percentage"] = merged["percentage"]
        patch["overall_grade"] = merged["overall_grade"]
        patch["overall_marks_display"] = merged["overall_marks_display"]

    if len(patch) <= 2:
        raise HTTPException(400, "No fields to update")

    await db.report_cards.update_one({"id": card_id}, {"$set": patch})
    await _audit(card_id, "updated", user, {"fields": list(patch.keys())})
    fresh = await db.report_cards.find_one({"id": card_id}, {"_id": 0})
    return enrich_card_computed(fresh, bands)


@router.patch("/{card_id}/teacher-remark")
async def set_teacher_remark(card_id: str, payload: TeacherRemarkIn, user: dict = Depends(get_current_user)):
    return await update_report_card_draft(
        card_id,
        ReportCardUpdateIn(teacher_remark=payload.teacher_remark),
        user,
    )


@router.post("/{card_id}/submit")
async def submit_for_review(card_id: str, user: dict = Depends(get_current_user)):
    _assert_build(user)
    card = await _get_card(card_id)
    if _is_locked(card):
        raise HTTPException(403, "Finalized report cards cannot be submitted")
    if user.get("role") == "teacher" and card.get("section_id"):
        await assert_teacher_section_access(user, card["section_id"])
    if not (card.get("teacher_remark") or "").strip():
        raise HTTPException(400, "Teacher remark is required before submission")
    await db.report_cards.update_one(
        {"id": card_id},
        {"$set": {"status": "review", "submitted_at": now_utc().isoformat(), "submitted_by": user["id"]}},
    )
    await _audit(card_id, "submitted", user)
    return await db.report_cards.find_one({"id": card_id}, {"_id": 0})


@router.post("/{card_id}/finalize")
async def finalize_report_card(card_id: str, user: dict = Depends(get_current_user)):
    _assert_publish(user)
    card = await _get_card(card_id)
    bands = await _bands_for_card(card)
    card = enrich_card_computed(card, bands)
    errors = validate_for_finalize(card)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    ts = now_utc().isoformat()
    patch = {
        "status": "finalized",
        "finalized_at": ts,
        "finalized_by": user["id"],
        "issue_date": card.get("issue_date") or ts[:10],
        "updated_at": ts,
        "updated_by": user["id"],
        **{k: card[k] for k in ("total_obtained", "total_max", "percentage", "overall_grade", "overall_marks_display") if k in card},
    }
    await db.report_cards.update_one({"id": card_id}, {"$set": patch})
    await _audit(card_id, "finalized", user)
    return await db.report_cards.find_one({"id": card_id}, {"_id": 0})


@router.post("/{card_id}/reopen")
async def reopen_report_card(card_id: str, payload: ReopenIn, user: dict = Depends(get_current_user)):
    _assert_publish(user)
    card = await _get_card(card_id)
    if card.get("status") not in FINALIZED_STATUSES:
        raise HTTPException(400, "Only finalized report cards can be reopened")
    ts = now_utc().isoformat()
    await db.report_cards.update_one(
        {"id": card_id},
        {"$set": {"status": "draft", "updated_at": ts, "updated_by": user["id"]}},
    )
    await _audit(card_id, "reopened", user, {"reason": payload.reason.strip()})
    return await db.report_cards.find_one({"id": card_id}, {"_id": 0})


@router.post("/{card_id}/publish")
async def publish_report_card(card_id: str, payload: PublishIn, user: dict = Depends(get_current_user)):
    _assert_publish(user)
    card = await _get_card(card_id)
    if card.get("status") == "published":
        raise HTTPException(400, "Already published")
    if card.get("status") not in ("finalized", "review", "draft"):
        raise HTTPException(400, "Invalid status for publish")
    bands = await _bands_for_card(card)
    if card.get("status") != "finalized":
        card = enrich_card_computed(card, bands)
        errors = validate_for_finalize(card)
        if errors:
            raise HTTPException(400, "; ".join(errors))
    ts = now_utc().isoformat()
    patch: dict = {
        "status": "published",
        "published_at": ts,
        "published_by": user["id"],
        "reviewed_by": user["id"],
        "reviewed_at": ts,
        "finalized_at": card.get("finalized_at") or ts,
        "finalized_by": card.get("finalized_by") or user["id"],
        "issue_date": card.get("issue_date") or ts[:10],
    }
    if card.get("has_alpha_participation"):
        approved = (payload.coach_remark or "").strip() or card.get("approved_coach_remark") or card.get("suggested_coach_remark")
        if approved:
            patch["approved_coach_remark"] = approved
    await db.report_cards.update_one({"id": card_id}, {"$set": patch})
    await _audit(card_id, "published", user)
    fresh = await db.report_cards.find_one({"id": card_id}, {"_id": 0})
    try:
        from notifications_service import send_notification
        person = await db.people.find_one({"id": fresh["person_id"]}, {"_id": 0, "name": 1, "parent_user_ids": 1})
        pname = (person or {}).get("name", "Student")
        term_name = fresh.get("exam_term_name") or "Term"
        for pid in (person or {}).get("parent_user_ids") or []:
            await send_notification(
                pid,
                ntype="report_card_published",
                title="Report card published",
                message=f"Report card for {pname} ({term_name}) is now available.",
                ref_id=card_id,
                ref_type="report_card",
                entity_id="pws",
            )
    except Exception:
        pass
    return fresh


@router.get("/{card_id}/audit")
async def report_card_audit(card_id: str, user: dict = Depends(get_current_user)):
    card = await _get_card(card_id)
    if not (_can_build(user) or _can_publish(user)):
        raise HTTPException(403, "Not allowed")
    rows = await db.report_card_audit.find({"card_id": card_id}, {"_id": 0}).sort("at", -1).to_list(100)
    return rows


def _can_view_card(user: dict, card: dict) -> bool:
    if card.get("status") == "published":
        if user.get("role") == "parent":
            return card["person_id"] in (user.get("linked_person_ids") or [])
        if user.get("role") == "student":
            return True
    if _can_build(user):
        return True
    if _can_publish(user):
        return True
    return False


@router.get("")
async def list_report_cards(
    person_id: Optional[str] = None,
    exam_term_id: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    q: dict = {"entity_id": ENTITY_PWS}
    if user.get("role") == "parent":
        wards = user.get("linked_person_ids") or []
        q["person_id"] = {"$in": wards}
        q["status"] = "published"
    elif user.get("role") == "coach" and not get_perm(user, "view_academic_marks"):
        raise HTTPException(403, "Not allowed")
    else:
        _assert_build(user)
        if user.get("role") == "teacher":
            from routers.academic import assigned_section_ids_for_teacher
            sids = await assigned_section_ids_for_teacher(user["id"])
            if not sids:
                return []
            q["section_id"] = {"$in": sids}
    if person_id:
        q["person_id"] = person_id
    if exam_term_id:
        q["exam_term_id"] = exam_term_id
    if status and user.get("role") != "parent":
        q["status"] = status
    rows = await db.report_cards.find(q, {"_id": 0}).sort("built_at", -1).to_list(200)
    if search:
        needle = search.strip().lower()
        rows = [
            r for r in rows
            if needle in (r.get("person_name") or "").lower()
            or needle in (r.get("admission_number") or "").lower()
            or needle in (r.get("exam_term_name") or "").lower()
        ]
    return rows


@router.get("/{card_id}")
async def get_report_card(card_id: str, user: dict = Depends(get_current_user)):
    card = await _get_card(card_id)
    if user.get("role") == "parent":
        if card.get("status") != "published":
            raise HTTPException(404, "Report card not found")
        if card["person_id"] not in (user.get("linked_person_ids") or []):
            raise HTTPException(404, "Report card not found")
        return enrich_card_computed(card, await _bands_for_card(card))
    if not _can_view_card(user, card):
        raise HTTPException(403, "Not allowed")
    if user.get("role") == "teacher" and card.get("section_id"):
        await assert_teacher_section_access(user, card["section_id"])
    return enrich_card_computed(card, await _bands_for_card(card))


@router.post("/generate")
async def generate_report_card_legacy(payload: ReportCardBuildIn, user: dict = Depends(get_current_user)):
    return await build_report_card(payload, user)


@router.get("/{card_id}/pdf")
async def report_card_pdf(card_id: str, user: dict = Depends(get_current_user)):
    card = await _get_card(card_id)
    if user.get("role") == "parent":
        if card.get("status") != "published" or card["person_id"] not in (user.get("linked_person_ids") or []):
            raise HTTPException(404, "Report card not found")
    elif not (_can_build(user) or _can_publish(user)):
        if card.get("status") != "published":
            raise HTTPException(403, "Not allowed")
    if card.get("status") not in FINALIZED_STATUSES:
        raise HTTPException(403, "PDF export is available only for finalized report cards")
    card = enrich_card_computed(card, await _bands_for_card(card))
    await _audit(card_id, "exported_pdf", user)
    pdf = render_report_card_pdf(card)
    name = (card.get("person_name") or "report").replace(" ", "_")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{name}_report_card.pdf"'},
    )
