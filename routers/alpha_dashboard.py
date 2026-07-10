"""ALPHA Sports ERP Dashboard — consolidated metrics endpoint.

Returns three analytics bands (Financial / Attendance / Tasks) plus filters.
Scoped to ALPHA org. Drill-down breakdowns by centre + sport are pre-aggregated
so the UI can render quickly without further calls.
"""
from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from core import db, get_current_user, is_admin, now_utc

router = APIRouter(prefix="/alpha-dashboard", tags=["alpha-dashboard"])

ALPHA_CENTRES = ["Balua", "Harding Park"]
ALPHA_SPORTS = ["Cricket", "Football"]


def _bucket_init():
    return {"total_amount": 0, "total_count": 0, "by_centre": {}, "by_centre_sport": {}}


async def _financial_band(centre: Optional[str], sport: Optional[str], date_from: Optional[str], date_to: Optional[str]) -> dict:
    from routers.fees import ensure_all_players_monthly_fees
    await ensure_all_players_monthly_fees()
    today = now_utc().strftime("%Y-%m-%d")
    this_month = today[:7]
    thirty_days_ago = (now_utc() - timedelta(days=30)).strftime("%Y-%m-%d")

    base: dict = {}
    if centre: base["centre"] = centre
    if sport: base["sport"] = sport

    # ----- A. Fees Collected Today -----
    collected_filter = {**base, "status": "paid", "paid_at": {"$regex": f"^{today}"}}
    collected_today = await db.fees.aggregate([
        {"$match": collected_filter},
        {"$group": {"_id": None, "total": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
    ]).to_list(1)
    # Breakdown by centre+sport
    collected_breakdown = await db.fees.aggregate([
        {"$match": collected_filter},
        {"$group": {"_id": {"centre": "$centre", "sport": "$sport"}, "total": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
    ]).to_list(50)

    # ----- B. Current Month Fees Due -----
    due_curr_filter = {**base, "status": "due", "period_month": this_month}
    due_curr = await db.fees.aggregate([
        {"$match": due_curr_filter},
        {"$group": {"_id": None, "total": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
    ]).to_list(1)
    due_curr_breakdown = await db.fees.aggregate([
        {"$match": due_curr_filter},
        {"$group": {"_id": {"centre": "$centre", "sport": "$sport"}, "total": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
    ]).to_list(50)

    # ----- C. >30-day Overdue -----
    overdue_filter = {**base, "status": "due", "due_date": {"$lt": thirty_days_ago}}
    overdue = await db.fees.aggregate([
        {"$match": overdue_filter},
        {"$group": {"_id": None, "total": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
    ]).to_list(1)
    overdue_breakdown = await db.fees.aggregate([
        {"$match": overdue_filter},
        {"$group": {"_id": {"centre": "$centre", "sport": "$sport"}, "total": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
    ]).to_list(50)

    def _shape(total_row, breakdown):
        t = total_row[0] if total_row else {"total": 0, "count": 0}
        by_centre: dict = {c: {"total": 0, "count": 0} for c in ALPHA_CENTRES}
        by_centre_sport: dict = {c: {sp: {"total": 0, "count": 0} for sp in ALPHA_SPORTS} for c in ALPHA_CENTRES}
        for r in breakdown:
            c = r["_id"].get("centre") or "Unknown"
            sp = r["_id"].get("sport") or "Unknown"
            if c in by_centre:
                by_centre[c]["total"] += r["total"]
                by_centre[c]["count"] += r["count"]
            if c in by_centre_sport and sp in by_centre_sport[c]:
                by_centre_sport[c][sp]["total"] += r["total"]
                by_centre_sport[c][sp]["count"] += r["count"]
        return {"total": t["total"], "count": t["count"], "by_centre": by_centre, "by_centre_sport": by_centre_sport}

    return {
        "collected_today": _shape(collected_today, collected_breakdown),
        "due_current_month": _shape(due_curr, due_curr_breakdown),
        "overdue_30plus": _shape(overdue, overdue_breakdown),
    }


async def _attendance_band(centre: Optional[str], sport: Optional[str]) -> dict:
    today = now_utc().strftime("%Y-%m-%d")

    async def _group(kind: str, extra_match: dict | None = None):
        match: dict = {"date": today, "kind": kind}
        if extra_match: match.update(extra_match)
        pipeline = [
            {"$match": match},
            {"$group": {"_id": {"centre": "$centre", "sport": "$sport", "status": "$status"}, "count": {"$sum": 1}}},
        ]
        rows = await db.attendance.aggregate(pipeline).to_list(200)
        by_centre = {c: {"present": 0, "absent": 0, "late": 0, "leave": 0} for c in ALPHA_CENTRES}
        by_centre_sport = {c: {sp: {"present": 0, "absent": 0, "late": 0, "leave": 0} for sp in ALPHA_SPORTS} for c in ALPHA_CENTRES}
        total = {"present": 0, "absent": 0, "late": 0, "leave": 0}
        for r in rows:
            c = r["_id"].get("centre") or "Unknown"
            sp = r["_id"].get("sport") or "Unknown"
            st = r["_id"].get("status") or "absent"
            n = r["count"]
            if centre and c != centre: continue
            if sport and sp != sport: continue
            total[st] = total.get(st, 0) + n
            if c in by_centre: by_centre[c][st] = by_centre[c].get(st, 0) + n
            if c in by_centre_sport and sp in by_centre_sport[c]:
                by_centre_sport[c][sp][st] = by_centre_sport[c][sp].get(st, 0) + n
        return {"total": total, "by_centre": by_centre, "by_centre_sport": by_centre_sport}

    players = await _group("player")
    coaches_today = await db.attendance.aggregate([
        {"$match": {"date": today, "kind": "coach"}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]).to_list(20)
    coaches_total = {"present": 0, "absent": 0, "late": 0, "leave": 0}
    for r in coaches_today:
        st = r["_id"] or "absent"
        coaches_total[st] = coaches_total.get(st, 0) + r["count"]

    # Hostel — ALPHA hostel residents
    hostel_residents = await db.people.count_documents({"kind": "player", "player_type": "Hostel", "organization": "ALPHA"})
    morning_present = await db.roll_calls.count_documents({"date": today, "session": "morning", "present": True})
    morning_absent = await db.roll_calls.count_documents({"date": today, "session": "morning", "present": False})

    # Staff — ALPHA Operations only (Person collection where kind=staff and centre is an ALPHA centre)
    staff = await db.attendance.aggregate([
        {"$match": {"date": today, "kind": "staff"}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]).to_list(20)
    staff_total = {"present": 0, "absent": 0, "late": 0, "leave": 0}
    for r in staff:
        st = r["_id"] or "absent"
        staff_total[st] = staff_total.get(st, 0) + r["count"]

    return {
        "players": players,
        "coaches": {"total": coaches_total},
        "hostel": {"total": {"present": morning_present, "absent": morning_absent, "residents": hostel_residents}},
        "staff": {"total": staff_total},
    }


async def _tasks_band(centre: Optional[str], sport: Optional[str]) -> dict:
    """Task aggregation. For now we don't filter by centre/sport since current
    Task schema doesn't carry those fields — filters are applied client-side
    when extended in a future iteration.
    """
    today_iso = now_utc().isoformat()
    statuses = ["assigned", "in_progress", "completed", "delayed", "reviewed"]
    counts = {}
    for s in statuses:
        counts[s] = await db.tasks.count_documents({"status": s})
    pending = counts.get("assigned", 0)
    in_progress = counts.get("in_progress", 0)
    completed = counts.get("completed", 0) + counts.get("reviewed", 0)
    delayed = counts.get("delayed", 0) + await db.tasks.count_documents({
        "status": {"$nin": ["completed", "reviewed"]},
        "deadline": {"$lt": today_iso},
    })
    followup = await db.tasks.count_documents({
        "$or": [{"status": "delayed"}, {"comments": {"$exists": True, "$ne": []}}],
    })
    return {
        "pending": pending,
        "in_progress": in_progress,
        "completed": completed,
        "delayed": delayed,
        "followup": followup,
        "total": sum(counts.values()),
    }


@router.get("")
async def alpha_dashboard(
    centre: Optional[str] = None,
    sport: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    if not is_admin(user):
        raise HTTPException(403, "Admin / Super Admin only")
    financial = await _financial_band(centre, sport, date_from, date_to)
    attendance = await _attendance_band(centre, sport)
    tasks = await _tasks_band(centre, sport)
    return {
        "filters": {"centre": centre, "sport": sport, "date_from": date_from, "date_to": date_to},
        "financial": financial,
        "attendance": attendance,
        "tasks": tasks,
        "generated_at": now_utc().isoformat(),
    }
