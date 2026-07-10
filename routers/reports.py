"""Reports module — Phase 1 (Financial Reports + Global Filters + Excel export).

Endpoints (all mounted under /api/reports):
    GET  /financial/summary        Revenue & Fee Summary aggregations
    GET  /financial/defaulters     Aging report with buckets
    GET  /financial/payment-modes  Payment mode breakdown (Cash/Online/Bank)
    GET  /financial/export         Excel (.xlsx) export of a specific report

Role gating:
    super_admin  -> full access, all institutions
    admin        -> Sports Admin: force institution=ALPHA
    principal, vice_principal -> deny for financial (PWS only, no fees here)
    others -> 403
"""
from datetime import datetime, timedelta
from io import BytesIO
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from core import db, get_current_user, is_super_admin, is_sports_admin, now_utc

router = APIRouter(prefix="/reports", tags=["reports"])


# ---------------- helpers ----------------
FEE_HEADS = ["Registration", "Monthly", "Hostel", "Day Boarding", "Transport", "Uniform", "Kit", "Tournament", "Books", "Event", "Other"]

def _access_check(user: dict, area: str = "financial"):
    """Guard: only super_admin, admin (Sports Admin) can access financial reports."""
    role = user.get("role")
    if role == "super_admin":
        return
    if role == "admin":
        return
    raise HTTPException(403, "You do not have access to Reports.")


def _resolve_institution(user: dict, requested: Optional[str]) -> str:
    """Sports Admin is forced to ALPHA regardless of the query param."""
    if is_sports_admin(user):
        return "ALPHA"
    v = (requested or "BOTH").upper()
    if v not in ("ALPHA", "PWS", "BOTH"):
        return "BOTH"
    return v


def _parse_iso(d: Optional[str]) -> Optional[datetime]:
    if not d:
        return None
    try:
        return datetime.fromisoformat(d[:10])
    except Exception:
        return None


def _build_fee_query(
    user: dict,
    date_from: Optional[str],
    date_to: Optional[str],
    institution: Optional[str],
    centre: Optional[str],
    sport: Optional[str],
    status: Optional[str],
    payment_status: Optional[str],
) -> Dict[str, Any]:
    """Build MongoDB query for `fees` collection with the global filters applied."""
    q: Dict[str, Any] = {}
    inst = _resolve_institution(user, institution)
    # Financial reports are ALPHA-only currently (PWS has no fees module).
    # So even if BOTH is requested, we only match ALPHA player fees.
    if inst == "PWS":
        # PWS has no fees — return an impossible clause to short-circuit
        q["_no_pws_fees"] = True
        return q
    if centre and centre != "All":
        q["centre"] = centre
    if sport and sport != "All":
        q["sport"] = sport
    if payment_status == "paid":
        q["status"] = "paid"
    elif payment_status == "pending":
        q["status"] = "due"
    elif payment_status == "overdue":
        # Overdue = due AND due_date < today
        q["status"] = "due"
        q["due_date"] = {"$lt": now_utc().strftime("%Y-%m-%d")}
    # Date range on paid_at for "collected within window"; on due_date if payment_status pending/overdue
    df = _parse_iso(date_from)
    dt = _parse_iso(date_to)
    if df or dt:
        # If filtering paid, use paid_at range; otherwise use due_date range
        field = "paid_at" if q.get("status") == "paid" else "due_date"
        rng: Dict[str, Any] = q.get(field, {}) if isinstance(q.get(field), dict) else {}
        if df:
            rng["$gte"] = df.strftime("%Y-%m-%d")
        if dt:
            rng["$lte"] = (dt.replace(hour=23, minute=59)).strftime("%Y-%m-%d")
        q[field] = rng
    return q


# ---------------- 1. Revenue & Fee Summary ----------------
async def _ensure_recurring_fees():
    """Materialize all players' recurring monthly dues before financial aggregation."""
    from routers.fees import ensure_all_players_monthly_fees
    await ensure_all_players_monthly_fees()


@router.get("/financial/summary")
async def revenue_summary(
    user: dict = Depends(get_current_user),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    institution: Optional[str] = None,
    centre: Optional[str] = None,
    sport: Optional[str] = None,
    status: Optional[str] = None,
):
    """Revenue & Fee Summary.

    Returns totals, current-month vs prev-month collections, outstanding dues,
    and breakdown by fee head. Drill-down data: Institution -> Branch -> Sport.
    """
    _access_check(user)
    await _ensure_recurring_fees()
    inst = _resolve_institution(user, institution)
    now = now_utc()
    cur_month = now.strftime("%Y-%m")
    prev_month_dt = (now.replace(day=1) - timedelta(days=1))
    prev_month = prev_month_dt.strftime("%Y-%m")

    # PWS has no fees currently; return zeros if strictly PWS requested.
    if inst == "PWS":
        return {
            "totals": {"collected_all_time": 0, "current_month": 0, "previous_month": 0, "outstanding": 0},
            "by_fee_head": [],
            "by_centre": [],
            "by_sport": [],
            "by_institution": [{"institution": "PWS", "collected": 0, "outstanding": 0}],
        }

    base_filter = {}
    if centre and centre != "All":
        base_filter["centre"] = centre
    if sport and sport != "All":
        base_filter["sport"] = sport

    df = _parse_iso(date_from)
    dt = _parse_iso(date_to)

    paid_filter = {**base_filter, "status": "paid"}
    if df or dt:
        rng: Dict[str, Any] = {}
        if df: rng["$gte"] = df.strftime("%Y-%m-%d")
        if dt: rng["$lte"] = dt.strftime("%Y-%m-%d")
        paid_filter["paid_at"] = rng

    due_filter = {**base_filter, "status": "due"}

    # Totals
    async def _sum(q, field="amount_due"):
        pipeline = [{"$match": q}, {"$group": {"_id": None, "sum": {"$sum": f"${field}"}}}]
        cur = db.fees.aggregate(pipeline)
        docs = await cur.to_list(1)
        return int(docs[0]["sum"]) if docs else 0

    collected_all = await _sum(paid_filter)
    outstanding = await _sum(due_filter)

    current_month_col = await _sum({**base_filter, "status": "paid", "paid_at": {"$gte": f"{cur_month}-01"}})
    previous_month_col = await _sum({
        **base_filter, "status": "paid",
        "paid_at": {"$gte": f"{prev_month}-01", "$lt": f"{cur_month}-01"}
    })

    # By fee head (respects filters)
    q_head = {**base_filter, "status": "paid"}
    if df or dt:
        q_head["paid_at"] = paid_filter["paid_at"]
    pipeline_head = [
        {"$match": q_head},
        {"$group": {"_id": "$fee_type", "sum": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
        {"$sort": {"sum": -1}},
    ]
    by_head_docs = await db.fees.aggregate(pipeline_head).to_list(50)
    by_head = [{"fee_head": d["_id"] or "Other", "amount": int(d["sum"]), "count": d["count"]} for d in by_head_docs]

    # Drill-down: by centre
    pipeline_c = [
        {"$match": q_head},
        {"$group": {"_id": "$centre", "collected": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
    ]
    by_centre_docs = await db.fees.aggregate(pipeline_c).to_list(20)
    by_centre = [{"centre": d["_id"] or "Unknown", "collected": int(d["collected"]), "count": d["count"]} for d in by_centre_docs]

    # Drill-down: by sport
    pipeline_s = [
        {"$match": q_head},
        {"$group": {"_id": "$sport", "collected": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
    ]
    by_sport_docs = await db.fees.aggregate(pipeline_s).to_list(20)
    by_sport = [{"sport": d["_id"] or "Unknown", "collected": int(d["collected"]), "count": d["count"]} for d in by_sport_docs]

    by_institution = [{"institution": "ALPHA", "collected": collected_all, "outstanding": outstanding}]
    return {
        "totals": {
            "collected_all_time": collected_all,
            "current_month": current_month_col,
            "previous_month": previous_month_col,
            "outstanding": outstanding,
        },
        "by_fee_head": by_head,
        "by_centre": by_centre,
        "by_sport": by_sport,
        "by_institution": by_institution,
    }


# ---------------- 2. Defaulter & Aging Report ----------------
@router.get("/financial/defaulters")
async def defaulters_aging(
    user: dict = Depends(get_current_user),
    institution: Optional[str] = None,
    centre: Optional[str] = None,
    sport: Optional[str] = None,
    limit: int = Query(500, le=2000),
):
    _access_check(user)
    await _ensure_recurring_fees()
    inst = _resolve_institution(user, institution)
    if inst == "PWS":
        return {"buckets": {"0_7": 0, "8_15": 0, "16_30": 0, "gt_30": 0}, "rows": []}
    today_str = now_utc().strftime("%Y-%m-%d")
    q: Dict[str, Any] = {"status": "due", "due_date": {"$lt": today_str}}
    if centre and centre != "All":
        q["centre"] = centre
    if sport and sport != "All":
        q["sport"] = sport
    cur = db.fees.find(q, {"_id": 0}).limit(limit)
    fees_docs = await cur.to_list(limit)
    rows = []
    today_dt = now_utc()
    buckets = {"0_7": 0, "8_15": 0, "16_30": 0, "gt_30": 0}
    for f in fees_docs:
        try:
            due_dt = datetime.fromisoformat(f.get("due_date", "")[:10])
            days = max((today_dt - due_dt).days, 0)
        except Exception:
            days = 0
        if days <= 7: bucket = "0_7"
        elif days <= 15: bucket = "8_15"
        elif days <= 30: bucket = "16_30"
        else: bucket = "gt_30"
        buckets[bucket] += 1
        rows.append({
            "player_id": f.get("player_id"),
            "player_name": f.get("player_name"),
            "centre": f.get("centre"),
            "sport": f.get("sport"),
            "category": f.get("category") or f.get("player_type"),
            "fee_type": f.get("fee_type"),
            "amount_due": int(f.get("amount_due", 0)),
            "due_date": f.get("due_date"),
            "days_overdue": days,
            "bucket": bucket,
        })
    rows.sort(key=lambda r: r["days_overdue"], reverse=True)
    return {"buckets": buckets, "rows": rows}


# ---------------- 3. Payment Mode Report ----------------
@router.get("/financial/payment-modes")
async def payment_modes(
    user: dict = Depends(get_current_user),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    institution: Optional[str] = None,
    centre: Optional[str] = None,
    sport: Optional[str] = None,
    limit: int = Query(1000, le=5000),
):
    _access_check(user)
    inst = _resolve_institution(user, institution)
    if inst == "PWS":
        return {"summary": {}, "transactions": []}
    q: Dict[str, Any] = {"status": "paid"}
    if centre and centre != "All":
        q["centre"] = centre
    if sport and sport != "All":
        q["sport"] = sport
    df = _parse_iso(date_from)
    dt = _parse_iso(date_to)
    if df or dt:
        rng: Dict[str, Any] = {}
        if df: rng["$gte"] = df.strftime("%Y-%m-%d")
        if dt: rng["$lte"] = dt.strftime("%Y-%m-%d")
        q["paid_at"] = rng
    # Summary aggregation
    pipeline = [
        {"$match": q},
        {"$group": {"_id": {"$ifNull": ["$payment_mode", "Unknown"]}, "count": {"$sum": 1}, "sum": {"$sum": "$amount_due"}}},
        {"$sort": {"sum": -1}},
    ]
    grp_docs = await db.fees.aggregate(pipeline).to_list(20)
    summary = {(d["_id"] or "Unknown"): {"count": d["count"], "sum": int(d["sum"])} for d in grp_docs}
    # Transactions (for online payments show ref/date/by)
    txn_cur = db.fees.find(q, {"_id": 0}).sort("paid_at", -1).limit(limit)
    txns = []
    async for f in txn_cur:
        txns.append({
            "player_id": f.get("player_id"),
            "player_name": f.get("player_name"),
            "centre": f.get("centre"),
            "sport": f.get("sport"),
            "fee_type": f.get("fee_type"),
            "amount": int(f.get("amount_due", 0)),
            "payment_mode": f.get("payment_mode") or "Unknown",
            "reference_id": f.get("reference_id"),
            "paid_at": f.get("paid_at"),
            "collected_by_name": f.get("collected_by_name") or f.get("paid_by_name") or None,
        })
    return {"summary": summary, "transactions": txns}


# ---------------- 4. Excel Export ----------------
@router.get("/financial/export")
async def export_financial(
    kind: str = Query(..., description="One of: summary, defaulters, payment-modes"),
    user: dict = Depends(get_current_user),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    institution: Optional[str] = None,
    centre: Optional[str] = None,
    sport: Optional[str] = None,
    status: Optional[str] = None,
):
    _access_check(user)
    await _ensure_recurring_fees()
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1E40AF")
    title_font = Font(bold=True, size=14, color="1E40AF")

    def write_title(sheet, title: str, subtitle: str):
        sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=6)
        c = sheet.cell(row=1, column=1, value=title)
        c.font = title_font
        sheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=6)
        s = sheet.cell(row=2, column=1, value=subtitle)
        s.font = Font(italic=True, color="475569", size=10)

    def write_header(sheet, row: int, cols: List[str]):
        for idx, col in enumerate(cols, start=1):
            cell = sheet.cell(row=row, column=idx, value=col)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="left")

    subtitle = f"Filters — Institution: {_resolve_institution(user, institution)} · Centre: {centre or 'All'} · Sport: {sport or 'All'} · Range: {date_from or '—'} → {date_to or '—'} · Generated: {now_utc().strftime('%Y-%m-%d %H:%M')} by {user.get('name')}"

    if kind == "summary":
        ws.title = "Revenue Summary"
        data = await revenue_summary(user, date_from, date_to, institution, centre, sport, status)
        write_title(ws, "PWS & ALPHA — Revenue & Fee Summary", subtitle)
        # Totals block
        ws.cell(row=4, column=1, value="Total Collected (₹)").font = Font(bold=True)
        ws.cell(row=4, column=2, value=data["totals"]["collected_all_time"])
        ws.cell(row=5, column=1, value="Current Month (₹)").font = Font(bold=True)
        ws.cell(row=5, column=2, value=data["totals"]["current_month"])
        ws.cell(row=6, column=1, value="Previous Month (₹)").font = Font(bold=True)
        ws.cell(row=6, column=2, value=data["totals"]["previous_month"])
        ws.cell(row=7, column=1, value="Outstanding (₹)").font = Font(bold=True)
        ws.cell(row=7, column=2, value=data["totals"]["outstanding"])
        # By fee head
        write_header(ws, 9, ["Fee Head", "Amount (₹)", "Count"])
        for i, r in enumerate(data["by_fee_head"], start=10):
            ws.cell(row=i, column=1, value=r["fee_head"])
            ws.cell(row=i, column=2, value=r["amount"])
            ws.cell(row=i, column=3, value=r["count"])
        # By centre
        r_off = 10 + len(data["by_fee_head"]) + 2
        write_header(ws, r_off, ["Centre", "Collected (₹)", "Count"])
        for i, r in enumerate(data["by_centre"], start=r_off + 1):
            ws.cell(row=i, column=1, value=r["centre"])
            ws.cell(row=i, column=2, value=r["collected"])
            ws.cell(row=i, column=3, value=r["count"])
    elif kind == "defaulters":
        ws.title = "Defaulters & Aging"
        data = await defaulters_aging(user, institution, centre, sport, 2000)
        write_title(ws, "PWS & ALPHA — Defaulters & Aging", subtitle)
        b = data["buckets"]
        ws.cell(row=4, column=1, value="0–7 days").font = Font(bold=True); ws.cell(row=4, column=2, value=b["0_7"])
        ws.cell(row=5, column=1, value="8–15 days").font = Font(bold=True); ws.cell(row=5, column=2, value=b["8_15"])
        ws.cell(row=6, column=1, value="16–30 days").font = Font(bold=True); ws.cell(row=6, column=2, value=b["16_30"])
        ws.cell(row=7, column=1, value="More than 30 days").font = Font(bold=True); ws.cell(row=7, column=2, value=b["gt_30"])
        write_header(ws, 9, ["Player", "Centre", "Sport", "Category", "Fee Head", "Amount Due (₹)", "Due Date", "Days Overdue", "Bucket"])
        for i, r in enumerate(data["rows"], start=10):
            ws.cell(row=i, column=1, value=r.get("player_name"))
            ws.cell(row=i, column=2, value=r.get("centre"))
            ws.cell(row=i, column=3, value=r.get("sport"))
            ws.cell(row=i, column=4, value=r.get("category"))
            ws.cell(row=i, column=5, value=r.get("fee_type"))
            ws.cell(row=i, column=6, value=r.get("amount_due"))
            ws.cell(row=i, column=7, value=r.get("due_date"))
            ws.cell(row=i, column=8, value=r.get("days_overdue"))
            ws.cell(row=i, column=9, value=r.get("bucket"))
    elif kind == "payment-modes":
        ws.title = "Payment Modes"
        data = await payment_modes(user, date_from, date_to, institution, centre, sport, 5000)
        write_title(ws, "PWS & ALPHA — Payment Mode Report", subtitle)
        write_header(ws, 4, ["Mode", "Transactions", "Amount (₹)"])
        row = 5
        for mode, agg in data["summary"].items():
            ws.cell(row=row, column=1, value=mode)
            ws.cell(row=row, column=2, value=agg["count"])
            ws.cell(row=row, column=3, value=agg["sum"])
            row += 1
        row += 2
        write_header(ws, row, ["Player", "Centre", "Sport", "Fee Head", "Amount (₹)", "Mode", "Reference", "Paid At", "Collected By"])
        for i, t in enumerate(data["transactions"], start=row + 1):
            ws.cell(row=i, column=1, value=t.get("player_name"))
            ws.cell(row=i, column=2, value=t.get("centre"))
            ws.cell(row=i, column=3, value=t.get("sport"))
            ws.cell(row=i, column=4, value=t.get("fee_type"))
            ws.cell(row=i, column=5, value=t.get("amount"))
            ws.cell(row=i, column=6, value=t.get("payment_mode"))
            ws.cell(row=i, column=7, value=t.get("reference_id"))
            ws.cell(row=i, column=8, value=t.get("paid_at"))
            ws.cell(row=i, column=9, value=t.get("collected_by_name"))
    else:
        raise HTTPException(400, f"Unknown export kind: {kind}. Use one of: summary, defaulters, payment-modes")

    # Auto-widen columns
    for col_cells in ws.columns:
        try:
            max_len = max((len(str(c.value)) for c in col_cells if c.value is not None), default=10)
            ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 2, 40)
        except Exception:
            pass

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"pws-alpha-{kind}-{now_utc().strftime('%Y%m%d-%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )
