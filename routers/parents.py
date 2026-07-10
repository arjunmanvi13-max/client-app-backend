"""Parent App router — read-only attendance + alerts for a parent user.

A parent user has `linked_person_ids: [<student/player ids>]` on their User doc.
All endpoints scope strictly to the wards (children) linked to the calling parent.
"""
import uuid
from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from core import db, get_current_user, now_utc

router = APIRouter(prefix="/parent", tags=["parent"])


def _ensure_parent(user: dict) -> None:
    if user.get("role") != "parent":
        raise HTTPException(403, "Parent role required")


async def _wards_for(user: dict) -> list[dict]:
    ids = user.get("linked_person_ids") or []
    if not ids:
        return []
    return await db.people.find({"id": {"$in": ids}}, {"_id": 0}).to_list(50)


@router.get("/wards")
async def list_wards(user: dict = Depends(get_current_user)):
    _ensure_parent(user)
    wards = await _wards_for(user)
    today = now_utc().strftime("%Y-%m-%d")
    # Annotate each ward with today's attendance status (P/A/L/-) and 30-day pct
    thirty_ago = (now_utc() - timedelta(days=30)).strftime("%Y-%m-%d")
    out = []
    for w in wards:
        today_rec = await db.attendance.find_one(
            {"person_id": w["id"], "date": today}, {"_id": 0, "status": 1}
        )
        records = await db.attendance.find(
            {"person_id": w["id"], "date": {"$gte": thirty_ago}}, {"_id": 0, "status": 1}
        ).to_list(200)
        total = len(records)
        present = sum(1 for r in records if r.get("status") == "present")
        out.append({
            **w,
            "today_status": (today_rec or {}).get("status"),
            "attendance_30d": {
                "total": total,
                "present": present,
                "absent": total - present,
                "pct": round((present / total) * 100, 1) if total else None,
            },
        })
    return out


@router.get("/attendance/{person_id}")
async def ward_attendance(person_id: str, days: int = 30, user: dict = Depends(get_current_user)):
    _ensure_parent(user)
    if person_id not in (user.get("linked_person_ids") or []):
        raise HTTPException(404, "Ward not found")
    since = (now_utc() - timedelta(days=max(1, min(days, 90)))).strftime("%Y-%m-%d")
    records = await db.attendance.find(
        {"person_id": person_id, "date": {"$gte": since}}, {"_id": 0}
    ).sort("date", -1).to_list(500)
    return {"person_id": person_id, "days": days, "records": records}


@router.get("/fees/{person_id}")
async def ward_fees(person_id: str, user: dict = Depends(get_current_user)):
    _ensure_parent(user)
    if person_id not in (user.get("linked_person_ids") or []):
        raise HTTPException(404, "Ward not found")
    person = await db.people.find_one({"id": person_id}, {"_id": 0})
    if not person:
        raise HTTPException(404, "Ward not found")
    if person.get("organization") != "ALPHA":
        return {"person_id": person_id, "fees": [], "summary": {"total_due": 0, "total_paid": 0, "overdue_count": 0}}
    fees = await db.fees.find({"player_id": person_id}, {"_id": 0}).sort("due_date", -1).to_list(200)
    total_due = sum(f.get("amount_due", 0) for f in fees if f.get("status") != "paid")
    total_paid = sum(f.get("amount_paid", 0) for f in fees if f.get("status") == "paid")
    today = now_utc().strftime("%Y-%m-%d")
    overdue_count = sum(1 for f in fees if f.get("status") != "paid" and (f.get("due_date") or "9999") < today)
    return {
        "person_id": person_id,
        "fees": fees,
        "summary": {"total_due": total_due, "total_paid": total_paid, "overdue_count": overdue_count},
    }


async def _compute_alerts_for_parent(user: dict) -> list[dict]:
    """Compute (do not persist) read-time alerts based on current state.

    Categories:
    - absent_today: ward marked absent on today's date
    - low_attendance_7d: ward had >=3 absences in trailing 7 days
    - fees_overdue: ward has any fee whose due_date is older than 7 days and still unpaid
    """
    alerts: list[dict] = []
    wards = await _wards_for(user)
    today_str = now_utc().strftime("%Y-%m-%d")
    week_ago = (now_utc() - timedelta(days=7)).strftime("%Y-%m-%d")
    cutoff_overdue = (now_utc() - timedelta(days=7)).strftime("%Y-%m-%d")

    for w in wards:
        # absent_today
        rec_today = await db.attendance.find_one({"person_id": w["id"], "date": today_str}, {"_id": 0, "status": 1})
        if rec_today and rec_today.get("status") == "absent":
            alerts.append({
                "id": f"absent-{w['id']}-{today_str}",
                "type": "absent_today",
                "severity": "high",
                "title": f"{w['name']} marked absent today",
                "message": f"{w['name']} was marked absent on {today_str}.",
                "ward_id": w["id"],
                "ward_name": w["name"],
                "created_at": now_utc().isoformat(),
            })

        # low_attendance_7d
        week_recs = await db.attendance.find(
            {"person_id": w["id"], "date": {"$gte": week_ago}}, {"_id": 0, "status": 1}
        ).to_list(50)
        absences = sum(1 for r in week_recs if r.get("status") == "absent")
        if absences >= 3:
            alerts.append({
                "id": f"low7d-{w['id']}-{today_str}",
                "type": "low_attendance_7d",
                "severity": "medium",
                "title": f"{w['name']} — {absences} absences this week",
                "message": f"{w['name']} has been absent {absences} time(s) in the last 7 days.",
                "ward_id": w["id"],
                "ward_name": w["name"],
                "created_at": now_utc().isoformat(),
            })

        # fees_overdue (ALPHA only)
        if w.get("organization") == "ALPHA":
            overdue = await db.fees.find({
                "player_id": w["id"],
                "status": {"$ne": "paid"},
                "due_date": {"$lt": cutoff_overdue},
            }, {"_id": 0}).to_list(20)
            if overdue:
                total = sum(f.get("amount_due", 0) for f in overdue)
                alerts.append({
                    "id": f"fees-{w['id']}-{today_str}",
                    "type": "fees_overdue",
                    "severity": "medium",
                    "title": f"Fees overdue — {w['name']}",
                    "message": f"₹{total:,.0f} unpaid across {len(overdue)} fee(s) (>7 days overdue).",
                    "ward_id": w["id"],
                    "ward_name": w["name"],
                    "amount_due": total,
                    "created_at": now_utc().isoformat(),
                })

    # severity weight: high first
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: sev_rank.get(a.get("severity", "low"), 9))
    return alerts


@router.get("/alerts")
async def parent_alerts(user: dict = Depends(get_current_user)):
    _ensure_parent(user)
    # Merge stored notifications (e.g., pushed when absent) + computed alerts.
    stored = await db.notifications.find({"user_id": user["id"]}, {"_id": 0}).sort("created_at", -1).to_list(100)
    computed = await _compute_alerts_for_parent(user)
    return {"stored": stored, "computed": computed, "count": len(stored) + len(computed)}


async def push_parent_notification(person_id: str, title: str, body: str, ntype: str = "absent") -> int:
    """Internal helper — called from attendance endpoints when a child is marked absent.

    Returns the number of parent users notified.
    """
    person = await db.people.find_one({"id": person_id}, {"_id": 0, "parent_user_ids": 1, "name": 1})
    if not person:
        return 0
    parent_ids = person.get("parent_user_ids") or []
    if not parent_ids:
        return 0
    docs = []
    for pid in parent_ids:
        docs.append({
            "id": str(uuid.uuid4()),
            "user_id": pid,
            "type": ntype,
            "title": title,
            "body": body,
            "person_id": person_id,
            "read": False,
            "created_at": now_utc().isoformat(),
        })
    if docs:
        await db.notifications.insert_many(docs)
    return len(docs)
