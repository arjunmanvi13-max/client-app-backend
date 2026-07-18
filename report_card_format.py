"""Report card layout, aggregation, validation, and PDF rendering (PWS Term format)."""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from routers.marks import grade_for_score, percentage_for_score

GRADING_SCALE_NOTE = (
    "8 Point Grading Scale: A1 (91 - 100), A2 (81 - 90), B1 (71 - 80), B2 (61-70), "
    "C1 (51 - 60), C2 (41 - 50), D (33 - 40), E (0 - 32)"
)

CO_SCHOLASTIC_AREAS = [
    ("music_dramatics", "Music / Dramatics"),
    ("art_education", "Art / Education"),
    ("physical_education_yoga", "Physical Education / Yoga"),
]

SCHOLASTIC_COLUMNS = [
    ("periodic_test", 20),
    ("independent_assessment", 10),
    ("written_assessment", 40),
    ("project", 20),
    ("group_discussion", 10),
    ("theory", 50),
    ("practical_viva", 40),
]

HYBRID_SUBJECT_PATTERN = re.compile(r"information|technology|\bit\b", re.I)

FINALIZED_STATUSES = frozenset({"finalized", "published"})


def empty_scholastic_row(subject_name: str) -> dict:
    row: dict = {"subject_name": subject_name.upper()}
    for key, _ in SCHOLASTIC_COLUMNS:
        row[key] = None
    row["marks_obtained"] = 0
    row["max_marks"] = 100
    row["grade"] = None
    row["is_hybrid"] = bool(HYBRID_SUBJECT_PATTERN.search(subject_name))
    return row


def component_key_from_assessment(name: str) -> Optional[str]:
    n = (name or "").lower()
    if "periodic" in n:
        return "periodic_test"
    if "independent" in n:
        return "independent_assessment"
    if "written" in n or "unit test" in n:
        return "written_assessment"
    if "project" in n:
        return "project"
    if "group" in n and "discuss" in n:
        return "group_discussion"
    if "theory" in n:
        return "theory"
    if "practical" in n or "viva" in n:
        return "practical_viva"
    return "written_assessment"


def _subject_max_marks(row: dict) -> int:
    if row.get("is_hybrid"):
        total = 0
        for key, mx in SCHOLASTIC_COLUMNS:
            if row.get(key) is not None:
                total += mx
        return total or 100
    return 100


def compute_row_totals(row: dict, bands: list) -> dict:
    parts = [row.get(k) for k, _ in SCHOLASTIC_COLUMNS if row.get(k) is not None]
    obtained = sum(parts) if parts else float(row.get("marks_obtained") or 0)
    max_marks = _subject_max_marks(row)
    pct = round((obtained / max_marks) * 100, 1) if max_marks else None
    row["marks_obtained"] = int(obtained) if obtained == int(obtained) else round(obtained, 1)
    row["max_marks"] = max_marks
    row["percentage"] = pct
    row["grade"] = grade_for_score(obtained, bands, max_marks) if obtained is not None else None
    return row


def aggregate_scholastic_rows(
    marks: List[dict],
    assessments: Dict[str, dict],
    subjects: Dict[str, dict],
    bands: list,
) -> List[dict]:
    by_subject: Dict[str, dict] = {}
    for m in marks:
        sid = m.get("subject_id")
        if not sid:
            continue
        sub = subjects.get(sid, {})
        name = sub.get("name") or sid
        if sid not in by_subject:
            by_subject[sid] = empty_scholastic_row(name)
            by_subject[sid]["subject_id"] = sid
        row = by_subject[sid]
        asm = assessments.get(m.get("assessment_id") or "", {})
        key = component_key_from_assessment(asm.get("name", ""))
        if key and m.get("marks_obtained") is not None:
            row[key] = m.get("marks_obtained")
        elif m.get("marks_obtained") is not None and not any(row.get(k) is not None for k, _ in SCHOLASTIC_COLUMNS):
            row["written_assessment"] = m.get("marks_obtained")

    rows = [compute_row_totals(r, bands) for r in by_subject.values()]
    rows.sort(key=lambda r: r.get("subject_name", ""))
    return rows


def compute_overall(scholastic_rows: List[dict]) -> Tuple[float, float, Optional[float], Optional[str], list]:
    total_obtained = sum(r.get("marks_obtained") or 0 for r in scholastic_rows)
    total_max = sum(r.get("max_marks") or 100 for r in scholastic_rows)
    pct = round((total_obtained / total_max) * 100, 1) if total_max else None
    from routers.marks import default_grading_scale  # noqa: circular guard — caller passes bands
    return total_obtained, total_max, pct, None, scholastic_rows


def default_co_scholastic() -> dict:
    return {k: None for k, _ in CO_SCHOLASTIC_AREAS}


def format_dob(dob: Optional[str]) -> str:
    if not dob:
        return "—"
    s = str(dob)[:10]
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.strftime("%d-%m-%Y")
    except ValueError:
        return s


def format_display_class(grade_name: Optional[str], section_label: Optional[str]) -> str:
    g = (grade_name or "").strip()
    if section_label and "-" in section_label:
        prefix = section_label.split("-")[0]
        roman = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V", "6": "VI", "7": "VII", "8": "VIII", "9": "IX", "10": "X"}
        if prefix in roman:
            return roman[prefix]
    return g or (section_label or "—")


def format_attendance(present: Optional[int], total: Optional[int], pct: Optional[float] = None) -> str:
    if present is not None and total:
        return f"{present}/{total}"
    if pct is not None:
        return f"{pct}%"
    return "—"


def cell_display(value: Any) -> str:
    if value is None or value == "":
        return "-----"
    return str(value)


def validate_for_finalize(card: dict) -> List[str]:
    errors: List[str] = []
    if not card.get("person_id"):
        errors.append("Student is required")
    if not card.get("exam_term_id"):
        errors.append("Term is required")
    if not (card.get("scholastic_rows") or card.get("subjects")):
        errors.append("At least one scholastic subject row is required")
    if not (card.get("teacher_remark") or "").strip():
        errors.append("Remarks are required before finalization")
    att = card.get("attendance_display") or format_attendance(
        card.get("attendance_present"), card.get("attendance_total"), card.get("attendance_pct"),
    )
    if att in ("—", ""):
        errors.append("Attendance is required")
    co = card.get("co_scholastic") or {}
    for key, label in CO_SCHOLASTIC_AREAS:
        if not co.get(key):
            errors.append(f"Co-scholastic grade required: {label}")
    return errors


def enrich_card_computed(card: dict, bands: list) -> dict:
    rows = card.get("scholastic_rows") or []
    if not rows and card.get("subjects"):
        rows = []
        for s in card["subjects"]:
            row = empty_scholastic_row(s.get("subject_name", ""))
            row["subject_id"] = s.get("subject_id")
            row["written_assessment"] = s.get("marks_obtained")
            rows.append(compute_row_totals(row, bands))
        card["scholastic_rows"] = rows

    computed = [compute_row_totals(dict(r), bands) for r in rows]
    card["scholastic_rows"] = computed
    card["subjects"] = [
        {
            "subject_id": r.get("subject_id"),
            "subject_name": r.get("subject_name"),
            "marks_obtained": r.get("marks_obtained"),
            "max_marks": r.get("max_marks"),
            "percentage": r.get("percentage"),
            "grade": r.get("grade"),
        }
        for r in computed
    ]
    total_obtained = sum(r.get("marks_obtained") or 0 for r in computed)
    total_max = sum(r.get("max_marks") or 100 for r in computed)
    pct = round((total_obtained / total_max) * 100, 1) if total_max else None
    card["total_obtained"] = total_obtained
    card["total_max"] = total_max
    card["percentage"] = pct
    card["overall_grade"] = grade_for_score(total_obtained, bands, total_max) if total_max else None
    card["overall_marks_display"] = f"{int(total_obtained)}/{int(total_max)}"
    if not card.get("attendance_display"):
        card["attendance_display"] = format_attendance(
            card.get("attendance_present"), card.get("attendance_total"), card.get("attendance_pct"),
        )
    return card


def render_report_card_pdf(card: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    branding = card.get("branding") or {}
    school = branding.get("school_name") or "Prarambhika World School"
    session = card.get("academic_year_name") or "2025 - 26"
    term = card.get("exam_term_name") or "TERM I"
    title = f"Report Card for Academic Session: {session} ({term.upper()})"

    buf = io.BytesIO()
    page_size = landscape(A4)
    c = pdfcanvas.Canvas(buf, pagesize=page_size)
    w, h = page_size
    margin = 12 * mm
    y = h - margin

    c.setFillColor(colors.HexColor("#1E40AF"))
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, y, school)
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#64748B"))
    c.drawRightString(w - margin, y, branding.get("tagline") or "Excellence in Education & Character")
    y -= 8 * mm
    c.setStrokeColor(colors.HexColor("#1E40AF"))
    c.setLineWidth(0.8)
    c.line(margin, y, w - margin, y)
    y -= 7 * mm

    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(w / 2, y, title)
    y -= 8 * mm

    c.setFont("Helvetica", 9)
    left_x = margin
    right_x = w / 2 + 5 * mm
    details = [
        ("Student's Name", card.get("person_name") or "—"),
        ("Class", format_display_class(card.get("grade_name"), card.get("section_label"))),
        ("Father's Name", card.get("father_name") or "—"),
        ("Date of Birth", format_dob(card.get("dob"))),
        ("Mother's Name", card.get("mother_name") or "—"),
    ]
    for i, (label, val) in enumerate(details):
        x = left_x if i % 2 == 0 else right_x
        if i % 2 == 0 and i > 0:
            y -= 5 * mm
        c.setFont("Helvetica-Bold", 8)
        c.drawString(x, y, f"{label} - ")
        c.setFont("Helvetica", 9)
        c.drawString(x + 28 * mm, y, str(val)[:40])
        if i % 2 == 1:
            y -= 5 * mm
    y -= 4 * mm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin, y, "SCHOLASTIC AREA")
    y -= 5 * mm

    col_headers = [
        "SUBJECT", "PT\n(20)", "IA\n(10)", "WA\n(40)", "PROJ\n(20)", "GD\n(10)",
        "THEORY\n(50)", "PRAC\n(40)", "MARKS", "GRADE",
    ]
    col_keys = [None] + [k for k, _ in SCHOLASTIC_COLUMNS] + ["marks_obtained", "grade"]
    col_widths = [32, 14, 14, 14, 14, 14, 16, 16, 16, 14]
    x = margin
    c.setFont("Helvetica-Bold", 6)
    for hdr, cw in zip(col_headers, col_widths):
        c.drawString(x + 1, y, hdr.split("\n")[0])
        if "\n" in hdr:
            c.drawString(x + 1, y - 3 * mm, hdr.split("\n")[1])
        x += cw * mm
    y -= 8 * mm
    c.setLineWidth(0.5)
    c.line(margin, y + 2 * mm, w - margin, y + 2 * mm)

    c.setFont("Helvetica", 7)
    rows = card.get("scholastic_rows") or []
    for row in rows:
        x = margin
        vals = [row.get("subject_name", "")]
        for key, _ in SCHOLASTIC_COLUMNS:
            vals.append(cell_display(row.get(key)))
        vals.append(str(row.get("marks_obtained", "—")))
        vals.append(str(row.get("grade") or "—"))
        for val, cw in zip(vals, col_widths):
            text = str(val)[:18]
            c.drawString(x + 1, y, text)
            x += cw * mm
        y -= 4.5 * mm
        if y < 35 * mm:
            c.showPage()
            y = h - margin

    y -= 3 * mm
    c.setFont("Helvetica", 6.5)
    for chunk in _wrap(GRADING_SCALE_NOTE, 140):
        c.drawString(margin, y, chunk)
        y -= 3.5 * mm

    y -= 2 * mm
    c.setFont("Helvetica-Bold", 9)
    overall = card.get("overall_marks_display") or f"{card.get('total_obtained', '—')}/{card.get('total_max', '—')}"
    pct = card.get("percentage")
    c.drawString(margin, y, f"OVERALL MARKS {overall}")
    c.drawString(margin + 55 * mm, y, f"PERCENTAGE {pct if pct is not None else '—'}%")
    y -= 8 * mm

    c.drawString(margin, y, "Co-Scholastic Area")
    c.drawString(margin + 45 * mm, y, "Grade")
    y -= 4 * mm
    co = card.get("co_scholastic") or {}
    c.setFont("Helvetica", 8)
    for key, label in CO_SCHOLASTIC_AREAS:
        c.drawString(margin, y, label)
        c.drawString(margin + 45 * mm, y, str(co.get(key) or "—"))
        y -= 4 * mm

    y -= 3 * mm
    c.setFont("Helvetica", 8)
    remark = (card.get("teacher_remark") or "").strip()
    c.drawString(margin, y, f"Remarks - {remark[:120]}")
    y -= 5 * mm
    att = card.get("attendance_display") or "—"
    c.drawString(margin, y, f"Attendance - {att}")
    y -= 5 * mm
    issue = (card.get("issue_date") or card.get("finalized_at") or card.get("published_at") or "")[:10]
    if issue:
        c.drawString(margin, y, f"Date of issue - {format_dob(issue) if len(issue) == 10 else issue}")

    y = 18 * mm
    c.setFont("Helvetica", 8)
    c.drawString(margin, y, "Class Teacher")
    c.drawCentredString(w / 2, y, "Principal")
    c.drawRightString(w - margin, y, school)

    c.setFont("Helvetica-Oblique", 6)
    c.setFillColor(colors.HexColor("#94A3B8"))
    c.drawCentredString(w / 2, 10 * mm, f"Generated {datetime.utcnow().strftime('%Y-%m-%d')} · {school}")

    c.save()
    return buf.getvalue()


def _wrap(text: str, max_chars: int) -> List[str]:
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
