"""Report cards MVP — marks + attendance from DB, teacher remarks, admin publish, PDF."""
import io
import uuid
from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from core import db, get_current_user, get_perm, is_super_admin, now_utc
from routers.academic import assert_teacher_section_access
from routers.marks import grade_for_score, default_grading_scale, percentage_for_score

router = APIRouter(prefix="/report-cards", tags=["report-cards"])

ENTITY_PWS = "pws"
SCHOOL_NAME = "Prarambhika World School"
SCHOOL_TAGLINE = "Excellence in Education & Character"


def _can_build(user: dict) -> bool:
    if user.get("role") == "coach" and not get_perm(user, "view_academic_marks"):
        return False
    return is_super_admin(user) or get_perm(user, "view_academic_marks") or get_perm(user, "enter_academic_marks")


def _can_publish(user: dict) -> bool:
    return is_super_admin(user) or get_perm(user, "manage_academic_structure")


def _assert_build(user: dict) -> None:
    if not _can_build(user):
        raise HTTPException(403, "Report card permission required")


def _assert_publish(user: dict) -> None:
    if not _can_publish(user):
        raise HTTPException(403, "Only school administrators can publish report cards")


async def _branding() -> dict:
    settings = await db.entity_settings.find_one({"entity_id": "pws"}, {"_id": 0})
    return {
        "school_name": (settings or {}).get("school_name") or SCHOOL_NAME,
        "tagline": (settings or {}).get("tagline") or SCHOOL_TAGLINE,
        "logo_url": (settings or {}).get("logo_url"),
    }


async def _attendance_pct(person_id: str, start_date: Optional[str], end_date: Optional[str]) -> Optional[float]:
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
    records = await db.attendance.find(q, {"_id": 0, "status": 1}).to_list(1000)
    if not records:
        return None
    attended = sum(1 for r in records if r.get("status") in ("present", "late"))
    return round((attended / len(records)) * 100, 1)


async def _coach_remark_for_student(person: dict, start_date: Optional[str], end_date: Optional[str]) -> Optional[str]:
    """Latest published ALPHA coach assessment remark when student participates in sports."""
    ents = person.get("entities") or []
    if "ALPHA" not in ents and person.get("organization") != "BOTH":
        return None
    player = await db.people.find_one(
        {"kind": "player", "name": person.get("name")},
        {"_id": 0, "id": 1},
    )
    if not player:
        return None
    q: dict = {"player_id": player["id"], "status": "published"}
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
    ).to_list(50)
    subject_ids = [m["subject_id"] for m in marks]
    subjects = {}
    if subject_ids:
        subs = await db.subjects.find({"id": {"$in": subject_ids}}, {"_id": 0}).to_list(50)
        subjects = {s["id"]: s for s in subs}

    scale = await default_grading_scale(year_id) if year_id else None
    bands = (scale or {}).get("bands") or []

    subject_rows = []
    total_obtained = 0.0
    total_max = 0.0
    for m in marks:
        sub = subjects.get(m["subject_id"], {})
        obtained = m.get("marks_obtained")
        if obtained is None:
            continue
        mx = m.get("max_marks") or 100
        pct = m.get("percentage") or percentage_for_score(obtained, mx)
        total_obtained += obtained
        total_max += mx
        subject_rows.append({
            "subject_id": m["subject_id"],
            "subject_name": sub.get("name") or m["subject_id"],
            "marks_obtained": obtained,
            "max_marks": mx,
            "percentage": pct,
            "grade": m.get("grade") or grade_for_score(obtained, bands, mx),
        })

    overall_pct = round((total_obtained / total_max) * 100, 1) if total_max else None
    overall_grade = grade_for_score(overall_pct, bands, 100) if overall_pct is not None else None
    att_pct = await _attendance_pct(person["id"], term.get("start_date"), term.get("end_date"))
    suggested_coach = await _coach_remark_for_student(person, term.get("start_date"), term.get("end_date"))
    branding = await _branding()

    return {
        "person_id": person_id,
        "person_name": person.get("name"),
        "admission_number": person.get("admission_number"),
        "section_id": person.get("section_id"),
        "grade_name": (section or {}).get("grade_name") or person.get("group", "").split("-")[0],
        "section_label": (section or {}).get("label") or person.get("group"),
        "exam_term_id": exam_term_id,
        "exam_term_name": term.get("name"),
        "academic_year_id": year_id,
        "academic_year_name": (year or {}).get("name"),
        "subjects": subject_rows,
        "total_obtained": total_obtained,
        "total_max": total_max,
        "percentage": overall_pct,
        "overall_grade": overall_grade,
        "attendance_pct": att_pct,
        "has_alpha_participation": "ALPHA" in (person.get("entities") or []) or person.get("organization") == "BOTH",
        "suggested_coach_remark": suggested_coach,
        "branding": branding,
        "entity_id": ENTITY_PWS,
    }


class ReportCardBuildIn(BaseModel):
    person_id: str
    exam_term_id: str


class TeacherRemarkIn(BaseModel):
    teacher_remark: str


class PublishIn(BaseModel):
    coach_remark: Optional[str] = None


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

    if existing and existing.get("status") == "published":
        raise HTTPException(403, "Published report cards cannot be rebuilt. Unpublish via admin first.")

    doc = {
        **data,
        "status": existing.get("status") if existing and existing.get("status") != "published" else "draft",
        "teacher_remark": (existing or {}).get("teacher_remark"),
        "coach_remark": (existing or {}).get("coach_remark"),
        "approved_coach_remark": (existing or {}).get("approved_coach_remark"),
        "updated_at": ts,
        "built_at": ts,
        "built_by": user["id"],
    }
    if not existing:
        doc["id"] = str(uuid.uuid4())
        doc["created_at"] = ts
        await db.report_cards.insert_one(doc)
    else:
        doc["id"] = existing["id"]
        await db.report_cards.update_one({"id": existing["id"]}, {"$set": doc})
    doc.pop("_id", None)
    return doc


@router.patch("/{card_id}/teacher-remark")
async def set_teacher_remark(card_id: str, payload: TeacherRemarkIn, user: dict = Depends(get_current_user)):
    _assert_build(user)
    card = await db.report_cards.find_one({"id": card_id}, {"_id": 0})
    if not card:
        raise HTTPException(404, "Report card not found")
    if card.get("status") == "published":
        raise HTTPException(403, "Published report cards are read-only")
    if user.get("role") == "teacher" and card.get("section_id"):
        await assert_teacher_section_access(user, card["section_id"])
    await db.report_cards.update_one(
        {"id": card_id},
        {"$set": {
            "teacher_remark": payload.teacher_remark.strip(),
            "status": "draft",
            "remark_by": user["id"],
            "remark_at": now_utc().isoformat(),
        }},
    )
    return await db.report_cards.find_one({"id": card_id}, {"_id": 0})


@router.post("/{card_id}/submit")
async def submit_for_review(card_id: str, user: dict = Depends(get_current_user)):
    _assert_build(user)
    card = await db.report_cards.find_one({"id": card_id}, {"_id": 0})
    if not card:
        raise HTTPException(404, "Report card not found")
    if user.get("role") == "teacher" and card.get("section_id"):
        await assert_teacher_section_access(user, card["section_id"])
    if not (card.get("teacher_remark") or "").strip():
        raise HTTPException(400, "Teacher remark is required before submission")
    await db.report_cards.update_one(
        {"id": card_id},
        {"$set": {"status": "review", "submitted_at": now_utc().isoformat(), "submitted_by": user["id"]}},
    )
    return await db.report_cards.find_one({"id": card_id}, {"_id": 0})


@router.post("/{card_id}/publish")
async def publish_report_card(card_id: str, payload: PublishIn, user: dict = Depends(get_current_user)):
    _assert_publish(user)
    card = await db.report_cards.find_one({"id": card_id}, {"_id": 0})
    if not card:
        raise HTTPException(404, "Report card not found")
    if card.get("status") not in ("review", "draft"):
        if card.get("status") == "published":
            raise HTTPException(400, "Already published")
    ts = now_utc().isoformat()
    patch = {
        "status": "published",
        "published_at": ts,
        "published_by": user["id"],
        "reviewed_by": user["id"],
        "reviewed_at": ts,
    }
    if card.get("has_alpha_participation"):
        approved = (payload.coach_remark or "").strip() or card.get("approved_coach_remark") or card.get("suggested_coach_remark")
        if approved:
            patch["approved_coach_remark"] = approved
    await db.report_cards.update_one({"id": card_id}, {"$set": patch})
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


def _can_view_card(user: dict, card: dict) -> bool:
    if card.get("status") == "published":
        if user.get("role") == "parent":
            return card["person_id"] in (user.get("linked_person_ids") or [])
        if user.get("role") == "student":
            return True
    if _can_build(user):
        if user.get("role") == "teacher" and card.get("section_id"):
            return True  # caller must still check section access
        return True
    if _can_publish(user):
        return True
    return False


@router.get("")
async def list_report_cards(
    person_id: Optional[str] = None,
    exam_term_id: Optional[str] = None,
    status: Optional[str] = None,
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
    return await db.report_cards.find(q, {"_id": 0}).sort("built_at", -1).to_list(100)


@router.get("/{card_id}")
async def get_report_card(card_id: str, user: dict = Depends(get_current_user)):
    card = await db.report_cards.find_one({"id": card_id}, {"_id": 0})
    if not card:
        raise HTTPException(404, "Report card not found")
    if user.get("role") == "parent":
        if card.get("status") != "published":
            raise HTTPException(404, "Report card not found")
        if card["person_id"] not in (user.get("linked_person_ids") or []):
            raise HTTPException(404, "Report card not found")
        return card
    if not _can_view_card(user, card):
        raise HTTPException(403, "Not allowed")
    if user.get("role") == "teacher" and card.get("section_id"):
        await assert_teacher_section_access(user, card["section_id"])
    return card


@router.post("/generate")
async def generate_report_card_legacy(payload: ReportCardBuildIn, user: dict = Depends(get_current_user)):
    """Backward-compatible alias for build."""
    return await build_report_card(payload, user)


def _render_pdf(card: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas as pdfcanvas

    branding = card.get("branding") or {}
    school = branding.get("school_name") or SCHOOL_NAME
    tagline = branding.get("tagline") or SCHOOL_TAGLINE

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 18 * mm

    c.setFillColor(colors.HexColor("#1E40AF"))
    c.setFont("Helvetica-Bold", 18)
    c.drawString(20 * mm, y, school)
    y -= 7 * mm
    c.setFillColor(colors.HexColor("#64748B"))
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, y, tagline)
    y -= 5 * mm
    c.setStrokeColor(colors.HexColor("#1E40AF"))
    c.setLineWidth(1)
    c.line(20 * mm, y, w - 20 * mm, y)
    y -= 10 * mm

    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(20 * mm, y, "Report Card")
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    lines = [
        f"Student: {card.get('person_name', '')}",
        f"Admission No: {card.get('admission_number') or '—'}",
        f"Academic Year: {card.get('academic_year_name') or '—'}",
        f"Grade / Section: {card.get('grade_name') or '—'} / {card.get('section_label') or '—'}",
        f"Term: {card.get('exam_term_name', '')}",
    ]
    for line in lines:
        c.drawString(20 * mm, y, line)
        y -= 5 * mm
    if card.get("percentage") is not None:
        c.drawString(20 * mm, y, f"Overall: {card['percentage']}% ({card.get('overall_grade') or '—'})")
        y -= 5 * mm
    if card.get("attendance_pct") is not None:
        c.drawString(20 * mm, y, f"Attendance: {card['attendance_pct']}%")
        y -= 8 * mm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(20 * mm, y, "Subject")
    c.drawString(85 * mm, y, "Marks")
    c.drawString(115 * mm, y, "%")
    c.drawString(135 * mm, y, "Grade")
    y -= 6 * mm
    c.setFont("Helvetica", 10)
    for row in card.get("subjects") or []:
        if y < 45 * mm:
            c.showPage()
            y = h - 20 * mm
        c.drawString(20 * mm, y, row.get("subject_name", ""))
        c.drawString(85 * mm, y, f"{row.get('marks_obtained', '—')}/{row.get('max_marks', 100)}")
        c.drawString(115 * mm, y, str(row.get("percentage") or "—"))
        c.drawString(135 * mm, y, row.get("grade") or "—")
        y -= 5 * mm

    y -= 6 * mm
    if card.get("teacher_remark"):
        c.setFont("Helvetica-Bold", 10)
        c.drawString(20 * mm, y, "Class Teacher's Remark")
        y -= 5 * mm
        c.setFont("Helvetica", 9)
        for chunk in _wrap_text(card["teacher_remark"], 85):
            c.drawString(20 * mm, y, chunk)
            y -= 4 * mm
    if card.get("approved_coach_remark"):
        y -= 4 * mm
        c.setFont("Helvetica-Bold", 10)
        c.drawString(20 * mm, y, "Sports Coach Remark (Approved)")
        y -= 5 * mm
        c.setFont("Helvetica", 9)
        for chunk in _wrap_text(card["approved_coach_remark"], 85):
            c.drawString(20 * mm, y, chunk)
            y -= 4 * mm

    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(colors.HexColor("#94A3B8"))
    c.drawString(20 * mm, 12 * mm, f"Published {card.get('published_at', card.get('built_at', ''))[:10]} · {school}")
    c.save()
    return buf.getvalue()


def _wrap_text(text: str, max_chars: int) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [text[:max_chars]]


@router.get("/{card_id}/pdf")
async def report_card_pdf(card_id: str, user: dict = Depends(get_current_user)):
    card = await db.report_cards.find_one({"id": card_id}, {"_id": 0})
    if not card:
        raise HTTPException(404, "Report card not found")
    if user.get("role") == "parent":
        if card.get("status") != "published" or card["person_id"] not in (user.get("linked_person_ids") or []):
            raise HTTPException(404, "Report card not found")
    elif not (_can_build(user) or _can_publish(user)):
        if card.get("status") != "published":
            raise HTTPException(403, "Not allowed")
    pdf = _render_pdf(card)
    name = (card.get("person_name") or "report").replace(" ", "_")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{name}_report_card.pdf"'},
    )
