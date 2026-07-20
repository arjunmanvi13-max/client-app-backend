"""Teacher profile PDF (ReportLab) — PWS branding."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

from entity_receipt_branding import BRAND_ASSETS_DIR, ENTITY_RECEIPT_BRANDING

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm

SLATE_900 = colors.Color(0.06, 0.09, 0.16)
SLATE_600 = colors.Color(0.28, 0.33, 0.41)
SLATE_500 = colors.Color(0.39, 0.45, 0.55)
SLATE_100 = colors.Color(0.95, 0.96, 0.98)
BLUE_700 = colors.Color(0.12, 0.25, 0.69)


def _draw_logo(c: pdfcanvas.Canvas, logo_path: Optional[str], x: float, y: float, size: float) -> None:
    if not logo_path or not Path(logo_path).is_file():
        return
    try:
        c.drawImage(logo_path, x, y, width=size, height=size, preserveAspectRatio=True, mask="auto")
    except Exception:
        pass


def _yes_no(flag: bool) -> str:
    return "Yes" if flag else "No"


def render_teacher_profile_pdf(
    teacher: dict,
    *,
    class_rows: List[Dict[str, Any]],
    format_date: Callable[[Optional[str]], str],
) -> bytes:
    """Render a teacher profile sheet with profile, access, and class allocation."""
    pws = ENTITY_RECEIPT_BRANDING["pws"]
    alpha = ENTITY_RECEIPT_BRANDING["alpha"]
    pws_logo = str(BRAND_ASSETS_DIR / pws["logo_filename"])
    alpha_logo = str(BRAND_ASSETS_DIR / alpha["logo_filename"])

    perms = teacher.get("permissions") or {}
    designation = teacher.get("teacher_designation") or "TEACHER"
    designation_label = "Class Teacher" if designation == "CLASS_TEACHER" else "Teacher"

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    y = PAGE_H - MARGIN

    # Header band
    header_h = 28 * mm
    c.setFillColor(SLATE_100)
    c.roundRect(MARGIN, y - header_h, PAGE_W - 2 * MARGIN, header_h, 4 * mm, fill=1, stroke=0)

    logo_size = 16 * mm
    _draw_logo(c, pws_logo, MARGIN + 4 * mm, y - header_h + 6 * mm, logo_size)
    _draw_logo(c, alpha_logo, MARGIN + 4 * mm + logo_size + 3 * mm, y - header_h + 6 * mm, logo_size)

    text_x = MARGIN + 4 * mm + logo_size * 2 + 10 * mm
    c.setFillColor(SLATE_900)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(text_x, y - 11 * mm, "PWS & ALPHA Tracker")
    c.setFont("Helvetica", 9)
    c.setFillColor(SLATE_500)
    c.drawString(text_x, y - 16 * mm, pws["display_name"])
    c.drawString(text_x, y - 21 * mm, alpha["display_name"])

    c.setFillColor(BLUE_700)
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(PAGE_W - MARGIN - 4 * mm, y - 12 * mm, "Teacher Profile")
    c.setFont("Helvetica", 8)
    c.setFillColor(SLATE_500)
    c.drawRightString(PAGE_W - MARGIN - 4 * mm, y - 18 * mm, format_date(teacher.get("updated_at") or teacher.get("created_at")) or "—")

    y -= header_h + 8 * mm

    def section_title(title: str) -> None:
        nonlocal y
        c.setFillColor(SLATE_900)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(MARGIN, y, title)
        y -= 3 * mm
        c.setStrokeColor(SLATE_100)
        c.setLineWidth(1)
        c.line(MARGIN, y, PAGE_W - MARGIN, y)
        y -= 6 * mm

    def field_row(label: str, value: str, col: int = 0) -> None:
        nonlocal y
        col_w = (PAGE_W - 2 * MARGIN - 6 * mm) / 2
        x = MARGIN + col * (col_w + 6 * mm)
        c.setFillColor(SLATE_500)
        c.setFont("Helvetica", 8)
        c.drawString(x, y, label.upper())
        c.setFillColor(SLATE_900)
        c.setFont("Helvetica", 10)
        c.drawString(x, y - 5 * mm, (value or "—")[:48])
        if col == 1:
            y -= 14 * mm

    section_title("Profile Details")
    field_row("Name", teacher.get("name") or "", 0)
    field_row("Date of Joining", format_date(teacher.get("date_of_joining")), 1)
    mobile = teacher.get("mobile") or teacher.get("phone") or ""
    if mobile and len(mobile) == 10:
        mobile = f"+91 {mobile[:5]} {mobile[5:]}"
    field_row("Mobile Number", mobile, 0)
    field_row("Designation", designation_label, 1)
    c.setFillColor(SLATE_500)
    c.setFont("Helvetica", 8)
    c.drawString(MARGIN, y, "ADDRESS")
    c.setFillColor(SLATE_900)
    c.setFont("Helvetica", 10)
    addr = (teacher.get("address") or "—").strip() or "—"
    c.drawString(MARGIN, y - 5 * mm, addr[:90])
    y -= 16 * mm

    section_title("Account & Access")
    field_row("Email", teacher.get("email") or "", 0)
    status = teacher.get("status") or "active"
    field_row("Account Status", "Active" if status == "active" else "Deactivated", 1)

    c.setFillColor(SLATE_500)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGIN, y, "PERMISSION LEVEL")
    y -= 6 * mm
    for label, enabled in [
        ("Attendance Allowed", bool(perms.get("mark_student_attendance"))),
        ("Marks Entry", bool(perms.get("enter_academic_marks"))),
        ("Student Assessment", bool(perms.get("view_academic_marks"))),
    ]:
        c.setFillColor(SLATE_600)
        c.setFont("Helvetica", 9)
        c.drawString(MARGIN + 2 * mm, y, f"• {label}:")
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(SLATE_900)
        c.drawString(MARGIN + 52 * mm, y, _yes_no(enabled))
        y -= 5.5 * mm
    y -= 4 * mm

    section_title("Allocated Classes")
    if not class_rows:
        c.setFillColor(SLATE_500)
        c.setFont("Helvetica", 9)
        c.drawString(MARGIN, y, "No classes allocated.")
        y -= 8 * mm
    else:
        table_top = y
        col_x = [MARGIN, MARGIN + 52 * mm, MARGIN + 72 * mm]
        headers = ["Class", "Section", "Subjects"]
        c.setFillColor(SLATE_100)
        c.rect(MARGIN, table_top - 6 * mm, PAGE_W - 2 * MARGIN, 7 * mm, fill=1, stroke=0)
        c.setFillColor(SLATE_600)
        c.setFont("Helvetica-Bold", 8)
        for i, h in enumerate(headers):
            c.drawString(col_x[i] + 2 * mm, table_top - 4.5 * mm, h)
        y = table_top - 10 * mm
        c.setFont("Helvetica", 9)
        c.setFillColor(SLATE_900)
        for row in class_rows:
            if y < MARGIN + 20 * mm:
                c.showPage()
                y = PAGE_H - MARGIN
            c.drawString(col_x[0] + 2 * mm, y, str(row.get("class_name") or "—")[:24])
            c.drawString(col_x[1] + 2 * mm, y, str(row.get("section") or "—")[:8])
            subjects = row.get("subjects") or []
            subj_txt = ", ".join(subjects) if isinstance(subjects, list) else str(subjects)
            c.drawString(col_x[2] + 2 * mm, y, subj_txt[:60] or "—")
            y -= 6.5 * mm
            c.setStrokeColor(SLATE_100)
            c.line(MARGIN, y + 2 * mm, PAGE_W - MARGIN, y + 2 * mm)

    c.setFillColor(SLATE_500)
    c.setFont("Helvetica", 7)
    c.drawCentredString(PAGE_W / 2, MARGIN - 2 * mm, "Generated by PWS & ALPHA Tracker · Confidential")

    c.save()
    return buf.getvalue()
