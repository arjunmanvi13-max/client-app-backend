"""Compact square-format fee receipt PDFs (ReportLab)."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

from entity_receipt_branding import fee_head_label, format_receipt_number_display

PAGE_W = 170 * mm
BASE_H = 165 * mm
ROW_H = 6.5 * mm

SLATE_900 = (0.06, 0.09, 0.16)
SLATE_600 = (0.28, 0.33, 0.41)
SLATE_500 = (0.39, 0.45, 0.55)
SLATE_100 = (0.95, 0.96, 0.98)
GREEN_600 = (0.02, 0.53, 0.32)


def _rs(n: int) -> str:
    return f"Rs. {int(n or 0):,}"


def _page_height(fee_rows: int, detail_rows: int = 4, has_address: bool = False, has_affiliation: bool = False) -> float:
    extra = max(0, fee_rows - 3) * ROW_H
    addr = 4 * mm if has_address else 0
    affil = 4 * mm if has_affiliation else 0
    return BASE_H + extra + max(0, detail_rows - 4) * 5 * mm + addr + affil


def _draw_logo(c: pdfcanvas.Canvas, logo_path: Optional[str], x: float, y: float, size: float) -> None:
    if not logo_path or not Path(logo_path).is_file():
        return
    try:
        c.drawImage(
            logo_path,
            x,
            y,
            width=size,
            height=size,
            preserveAspectRatio=True,
            mask="auto",
        )
    except Exception:
        pass


def render_batch_receipt_pdf(
    batch_id: str,
    fees: List[dict],
    player: dict,
    *,
    branding: Dict[str, Any],
    receipt_number: Optional[str],
    person_label: str,
    person_lines: List[str],
    format_month: Callable[[Optional[str]], str],
    format_date: Callable[[Optional[str]], str],
    format_datetime: Callable[[Optional[str]], str],
) -> bytes:
    """Payment receipt after fee collection (batch_id)."""
    f0 = fees[0]
    entity_id = branding.get("entity_id") or "alpha"
    total = sum(int(f.get("amount_due") or 0) for f in fees)
    address_lines = branding.get("address_lines") or []
    affiliation_line = (branding.get("affiliation_line") or "").strip()
    has_address = bool(address_lines)
    has_affiliation = bool(affiliation_line)
    H = _page_height(len(fees), has_address=has_address, has_affiliation=has_affiliation)
    W = PAGE_W
    margin = 10 * mm
    inner_w = W - 2 * margin
    logo_size = 14 * mm

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=(W, H))
    y = H - margin

    c.setStrokeColorRGB(0.85, 0.88, 0.92)
    c.setLineWidth(0.8)
    c.roundRect(margin - 2 * mm, margin - 2 * mm, inner_w + 4 * mm, H - 2 * margin + 4 * mm, 4 * mm, stroke=1, fill=0)

    # Header — white card style matching modal
    header_h = 24 * mm + (4 * mm if has_address else 0) + (4 * mm if has_affiliation else 0)
    header_top = y - header_h
    text_x = margin + logo_size + 4 * mm
    _draw_logo(c, branding.get("logo_path"), margin + 1 * mm, header_top + header_h - logo_size - 2 * mm, logo_size)

    c.setFillColorRGB(*SLATE_900)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(text_x, header_top + header_h - 8 * mm, str(branding.get("display_name", ""))[:42])
    line_y = header_top + header_h - 13 * mm
    if has_affiliation:
        c.setFont("Helvetica", 7.5)
        c.setFillColorRGB(*SLATE_500)
        c.drawString(text_x, line_y, affiliation_line[:72])
        line_y -= 4.5 * mm
    if has_address:
        c.setFont("Helvetica", 7.5)
        c.setFillColorRGB(*SLATE_500)
        c.drawString(text_x, line_y, ", ".join(address_lines)[:60])
        line_y -= 4.5 * mm
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(*SLATE_500)
    c.drawString(text_x, line_y, str(branding.get("receipt_title", "Fee Payment Receipt")))

    # Paid pill (top-right)
    c.setFillColorRGB(0.86, 0.99, 0.91)
    c.roundRect(margin + inner_w - 22 * mm, header_top + header_h - 9 * mm, 20 * mm, 6 * mm, 3 * mm, fill=1, stroke=0)
    c.setFillColorRGB(*GREEN_600)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawCentredString(margin + inner_w - 12 * mm, header_top + header_h - 6.5 * mm, "Paid")

    y = header_top - 4 * mm
    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(*SLATE_500)
    receipt_no = format_receipt_number_display(receipt_number or f0.get("receipt_number"), entity_id)
    c.drawString(margin + 2 * mm, y, f"Receipt No. {receipt_no}")
    y -= 8 * mm

    # Person block
    c.setFillColorRGB(*SLATE_900)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(margin + 2 * mm, y, person_label.upper())
    y -= 5 * mm
    c.setFont("Helvetica-Bold", 11)
    name = player.get("name") or f0.get("player_name") or "-"
    c.drawString(margin + 2 * mm, y, str(name)[:40])
    y -= 5 * mm
    c.setFont("Helvetica", 8.5)
    c.setFillColorRGB(*SLATE_500)
    for val in person_lines:
        c.drawString(margin + 2 * mm, y, str(val)[:55])
        y -= 4.5 * mm
    y -= 3 * mm

    # Fee table
    c.setFillColorRGB(*SLATE_100)
    c.rect(margin, y - 2 * mm, inner_w, 7 * mm, fill=1, stroke=0)
    c.setFillColorRGB(*SLATE_600)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(margin + 3 * mm, y, "FEE HEAD")
    c.drawString(margin + 68 * mm, y, "PERIOD")
    c.drawRightString(margin + inner_w - 3 * mm, y, "AMOUNT")
    y -= 7 * mm

    c.setFont("Helvetica", 8.5)
    for i, f in enumerate(fees):
        if i % 2 == 1:
            c.setFillColorRGB(0.98, 0.99, 1.0)
            c.rect(margin, y - 1.5 * mm, inner_w, ROW_H, fill=1, stroke=0)
        c.setFillColorRGB(*SLATE_900)
        head = fee_head_label(str(f.get("fee_type") or "-"), entity_id)[:22]
        period = format_month(f.get("period_month"))
        c.drawString(margin + 3 * mm, y, head)
        c.drawString(margin + 68 * mm, y, period)
        c.drawRightString(margin + inner_w - 3 * mm, y, _rs(f.get("amount_due", 0)))
        y -= ROW_H
        discount = int(f.get("discount_applied") or 0)
        if discount > 0:
            reason = (f.get("discount_reason") or "Concession").strip()[:16]
            c.setFillColorRGB(0.05, 0.46, 0.42)
            c.drawString(margin + 3 * mm, y, f"Discount — {reason}")
            c.drawString(margin + 68 * mm, y, "—")
            c.drawRightString(margin + inner_w - 3 * mm, y, f"- Rs. {discount:,}")
            y -= ROW_H

    c.setStrokeColorRGB(0.89, 0.91, 0.94)
    c.line(margin, y + 2 * mm, margin + inner_w, y + 2 * mm)
    y -= 4 * mm

    c.setFillColorRGB(0.93, 0.97, 0.99)
    c.roundRect(margin, y - 6 * mm, inner_w, 11 * mm, 2 * mm, fill=1, stroke=0)
    c.setFillColorRGB(*SLATE_900)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin + 4 * mm, y - 1 * mm, "Total collected")
    c.setFillColorRGB(0.12, 0.23, 0.54)
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(margin + inner_w - 4 * mm, y - 1.5 * mm, _rs(total))
    y -= 16 * mm

    c.setFillColorRGB(*SLATE_900)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(margin + 2 * mm, y, "PAYMENT DETAILS")
    y -= 5 * mm
    paid_at = f0.get("paid_at") or ""
    details = [
        ("Date", format_date(f0.get("transaction_date"))),
        ("Time", format_datetime(paid_at) if paid_at else "—"),
        ("Mode", f0.get("payment_mode") or "-"),
        ("Reference", f0.get("reference_id") or "—"),
        ("Collected by", f0.get("collected_by_name") or "-"),
    ]
    c.setFont("Helvetica", 7.5)
    for label, val in details:
        c.setFillColorRGB(*SLATE_500)
        c.drawString(margin + 2 * mm, y, f"{label}:")
        c.setFillColorRGB(*SLATE_900)
        c.drawString(margin + 28 * mm, y, str(val)[:42])
        y -= 4.5 * mm

    c.setFont("Helvetica-Oblique", 6.5)
    c.setFillColorRGB(0.58, 0.64, 0.72)
    c.drawCentredString(W / 2, margin + 3 * mm, "Computer-generated receipt · no signature required")

    c.save()
    return buf.getvalue()


def render_pws_fee_statement_pdf(
    student: dict,
    profile: dict,
    fees: List[dict],
    academic_year: str,
    *,
    format_month: Callable[[Optional[str]], str],
    format_date: Callable[[Optional[str]], str],
) -> bytes:
    """PWS fee statement / invoice export (paid vs pending)."""
    from entity_receipt_branding import resolve_entity_branding

    branding = resolve_entity_branding("pws")
    rows = fees[:40]
    H = _page_height(len(rows), detail_rows=6, has_address=True)
    W = PAGE_W
    margin = 10 * mm
    inner_w = W - 2 * margin

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=(W, H))
    y = H - margin

    header_h = 24 * mm
    y -= header_h
    logo_size = 14 * mm
    _draw_logo(c, branding.get("logo_path"), margin + 1 * mm, y + header_h - logo_size - 2 * mm, logo_size)
    c.setFillColorRGB(*SLATE_900)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin + logo_size + 4 * mm, y + header_h - 8 * mm, branding["display_name"])
    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(*SLATE_500)
    c.drawString(margin + logo_size + 4 * mm, y + header_h - 13 * mm, ", ".join(branding.get("address_lines") or []))
    c.drawString(margin + logo_size + 4 * mm, y + header_h - 17 * mm, f"FEE STATEMENT · AY {academic_year}")

    y -= 8 * mm
    c.setFont("Helvetica-Bold", 10)
    c.setFillColorRGB(*SLATE_900)
    c.drawString(margin + 2 * mm, y, student.get("name", ""))
    y -= 5 * mm
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(*SLATE_500)
    transport = "Yes" if profile.get("transport_enabled") else "No"
    if profile.get("transport_enabled"):
        transport += f" ({profile.get('transport_distance')})"
    c.drawString(margin + 2 * mm, y, f"{profile.get('pws_class')} · {profile.get('pws_student_type')} · Transport: {transport}")
    y -= 8 * mm

    c.setFillColorRGB(*SLATE_100)
    c.rect(margin, y - 2 * mm, inner_w, 7 * mm, fill=1, stroke=0)
    c.setFillColorRGB(*SLATE_600)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(margin + 3 * mm, y, "PERIOD")
    c.drawString(margin + 40 * mm, y, "FEE")
    c.drawRightString(margin + inner_w - 28 * mm, y, "AMOUNT")
    c.drawRightString(margin + inner_w - 3 * mm, y, "STATUS")
    y -= 7 * mm

    c.setFont("Helvetica", 8)
    paid_total = 0
    unpaid_total = 0
    for i, f in enumerate(rows):
        if i % 2 == 1:
            c.setFillColorRGB(0.98, 0.99, 1.0)
            c.rect(margin, y - 1.5 * mm, inner_w, ROW_H, fill=1, stroke=0)
        status = f.get("status", "due")
        amt = int(f.get("amount_due") or 0)
        if status == "paid":
            paid_total += amt
        else:
            unpaid_total += amt
        c.setFillColorRGB(*SLATE_900)
        c.drawString(margin + 3 * mm, y, format_month(f.get("period_month")))
        c.drawString(margin + 40 * mm, y, str(f.get("fee_type", ""))[:14])
        c.drawRightString(margin + inner_w - 28 * mm, y, _rs(amt))
        c.setFillColorRGB(*GREEN_600 if status == "paid" else (0.85, 0.33, 0.10))
        c.drawRightString(margin + inner_w - 3 * mm, y, status.upper()[:4])
        y -= ROW_H

    y -= 3 * mm
    c.setFillColorRGB(0.93, 0.98, 0.96)
    c.roundRect(margin, y - 6 * mm, inner_w, 11 * mm, 2 * mm, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 9)
    c.setFillColorRGB(*GREEN_600)
    c.drawString(margin + 4 * mm, y - 1 * mm, f"Paid {_rs(paid_total)}")
    c.setFillColorRGB(0.85, 0.33, 0.10)
    c.drawRightString(margin + inner_w - 4 * mm, y - 1 * mm, f"Pending {_rs(unpaid_total)}")

    c.setFont("Helvetica-Oblique", 6.5)
    c.setFillColorRGB(0.58, 0.64, 0.72)
    c.drawCentredString(W / 2, margin + 3 * mm, "PWS Fee Statement · for parent/guardian reference")

    c.save()
    return buf.getvalue()
