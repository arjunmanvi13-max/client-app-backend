"""Role-based dashboard MVP aggregations (no advanced financial analytics)."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional, List, Dict, Any

from core import (
    db,
    now_utc,
    resolve_user_institution,
    person_entity_filter,
    fee_entity_filter,
    attendance_entity_filter,
    is_super_admin,
    is_admin,
)
from notifications_service import normalize_notification, notification_filter_for_user


def _entity_param(entity: Optional[str]) -> str:
    raw = (entity or "both").strip().lower()
    if raw in ("pws", "alpha"):
        return raw.upper()
    return "BOTH"


async def _attendance_totals_today(entity: str) -> dict:
    today = now_utc().strftime("%Y-%m-%d")
    match: dict = {"date": today}
    ent_f = attendance_entity_filter(entity)
    if ent_f:
        match = {"$and": [match, ent_f]}
    rows = await db.attendance.aggregate([
        {"$match": match},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]).to_list(20)
    totals = {"present": 0, "absent": 0, "late": 0, "leave": 0, "total": 0}
    for r in rows:
        st = r["_id"] or "present"
        totals[st] = r["count"]
        totals["total"] += r["count"]
    return totals


async def _fees_collected_today(entity: str) -> dict:
    today = now_utc().strftime("%Y-%m-%d")
    base = {"status": "paid", "paid_at": {"$regex": f"^{today}"}}
    base.update(fee_entity_filter(entity))
    rows = await db.fees.aggregate([
        {"$match": base},
        {"$group": {"_id": None, "total": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
    ]).to_list(1)
    fee_total = int(rows[0]["total"]) if rows else 0
    fee_count = rows[0]["count"] if rows else 0

    inv_q: dict = {"status": {"$ne": "refunded"}}
    if entity == "PWS":
        inv_q["entity_id"] = "pws"
    elif entity == "ALPHA":
        inv_q["entity_id"] = "alpha"
    inv_q["created_at"] = {"$regex": f"^{today}"}
    inv_rows = await db.payments.aggregate([
        {"$match": inv_q},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ]).to_list(1)
    inv_total = int(inv_rows[0]["total"]) if inv_rows else 0
    inv_count = inv_rows[0]["count"] if inv_rows else 0
    return {
        "total": fee_total + inv_total,
        "fee_total": fee_total,
        "invoice_payments_total": inv_total,
        "transaction_count": fee_count + inv_count,
    }


async def _outstanding_invoices(entity: str) -> dict:
    q: dict = {
        "balance_due": {"$gt": 0},
        "status": {"$nin": ["cancelled", "draft", "void"]},
    }
    if entity == "PWS":
        q["entity_id"] = "pws"
    elif entity == "ALPHA":
        q["entity_id"] = "alpha"
    rows = await db.invoices.aggregate([
        {"$match": q},
        {"$group": {"_id": None, "total": {"$sum": "$balance_due"}, "count": {"$sum": 1}}},
    ]).to_list(1)
    return {
        "total": int(rows[0]["total"]) if rows else 0,
        "count": rows[0]["count"] if rows else 0,
    }


async def super_admin_dashboard(user: dict, entity: Optional[str] = None) -> dict:
    inst = resolve_user_institution(user, _entity_param(entity))
    people_q: dict = {"status": {"$ne": "deactivated"}}
    ent_f = person_entity_filter(inst)
    if ent_f:
        people_q = {"$and": [people_q, ent_f]}

    open_statuses = ["open", "in_progress", "blocked", "assigned", "delayed"]
    return {
        "role": "super_admin",
        "entity": inst,
        "entity_label": "Combined" if inst == "BOTH" else inst,
        "today": now_utc().strftime("%Y-%m-%d"),
        "active_people": await db.people.count_documents(people_q),
        "attendance_today": await _attendance_totals_today(inst),
        "fees_collected_today": await _fees_collected_today(inst),
        "outstanding_invoices": await _outstanding_invoices(inst),
        "pending_approvals": await db.approval_requests.count_documents({"status": "pending"}),
        "open_tasks": await db.tasks.count_documents({"status": {"$in": open_statuses}}),
        "generated_at": now_utc().isoformat(),
    }


async def admin_dashboard(user: dict) -> dict:
    """Sports Admin — ALPHA-scoped MVP (no entity selector)."""
    data = await super_admin_dashboard(user, "alpha")
    data["role"] = "admin"
    return data


async def teacher_dashboard(user: dict) -> dict:
    from routers.academic import get_open_academic_year

    today = now_utc().strftime("%Y-%m-%d")
    open_year = await get_open_academic_year()
    year_id = (open_year or {}).get("id")
    assignments: List[dict] = []
    section_attendance: List[dict] = []
    pending_marks = 0

    if year_id:
        rows = await db.teacher_class_assignments.find(
            {"teacher_user_id": user["id"], "academic_year_id": year_id},
            {"_id": 0},
        ).to_list(100)
        seen = set()
        for r in rows:
            key = (r["section_id"], r["subject_id"])
            if key in seen:
                continue
            seen.add(key)
            section = await db.sections.find_one({"id": r["section_id"]}, {"_id": 0, "label": 1, "grade_name": 1})
            subject = await db.subjects.find_one({"id": r["subject_id"]}, {"_id": 0, "name": 1, "code": 1})
            assignments.append({
                "section_id": r["section_id"],
                "section_label": (section or {}).get("label"),
                "grade_name": (section or {}).get("grade_name"),
                "subject_id": r["subject_id"],
                "subject_name": (subject or {}).get("name"),
            })

            student_ids = await db.people.distinct(
                "id",
                {"kind": "student", "section_id": r["section_id"], "status": {"$ne": "deactivated"}},
            )
            marked = await db.attendance.count_documents({
                "person_id": {"$in": student_ids},
                "date": today,
                "kind": "student",
            }) if student_ids else 0
            present = await db.attendance.count_documents({
                "person_id": {"$in": student_ids},
                "date": today,
                "kind": "student",
                "status": {"$in": ["present", "late"]},
            }) if student_ids else 0
            section_attendance.append({
                "section_id": r["section_id"],
                "section_label": (section or {}).get("label"),
                "total_students": len(student_ids),
                "marked_today": marked,
                "present_today": present,
            })

            assessments = await db.assessments.find({
                "section_id": r["section_id"],
                "subject_id": r["subject_id"],
                "academic_year_id": year_id,
            }, {"_id": 0, "id": 1}).to_list(30)
            for asm in assessments:
                marks_n = await db.academic_marks.count_documents({"assessment_id": asm["id"]})
                if marks_n < len(student_ids):
                    pending_marks += 1

    notif_rows = await db.notifications.find(
        notification_filter_for_user(user),
        {"_id": 0},
    ).sort("created_at", -1).to_list(5)
    notifications = [normalize_notification(n) for n in notif_rows]

    return {
        "role": "teacher",
        "today": today,
        "assigned_classes": assignments,
        "attendance_today": section_attendance,
        "pending_marks_entry": pending_marks,
        "recent_notifications": notifications,
        "unread_notifications": sum(1 for n in notifications if not n.get("read")),
        "generated_at": now_utc().isoformat(),
    }


async def coach_dashboard_mvp(user: dict) -> dict:
    from routers.coach import _coach_visibility_filter

    today = now_utc().strftime("%Y-%m-%d")
    q = _coach_visibility_filter(user)
    players = await db.people.find(q, {"_id": 0, "id": 1, "centre": 1, "sport": 1, "slot": 1}).to_list(2000)
    player_ids = [p["id"] for p in players]

    today_records = await db.attendance.find(
        {"date": today, "kind": "player", "person_id": {"$in": player_ids}},
        {"_id": 0, "status": 1},
    ).to_list(3000) if player_ids else []

    centres = sorted({p.get("centre") for p in players if p.get("centre")})
    sports = sorted({p.get("sport") for p in players if p.get("sport")})

    pending_assessments = 0
    if player_ids:
        complete_today = await db.player_assessments.count_documents({
            "schema_version": {"$gte": 2},
            "date": today,
            "player_id": {"$in": player_ids},
            "status": {"$in": ["draft", "final", "published"]},
            "scores.overall_score": {"$ne": None},
        })
        if complete_today < len(player_ids):
            pending_assessments = 1

    return {
        "role": "coach",
        "today": today,
        "assigned_centres": user.get("assigned_centres") or centres,
        "assigned_sports": user.get("assigned_sports") or sports,
        "total_players": len(players),
        "attendance_today": {
            "marked": len(today_records),
            "present": sum(1 for r in today_records if r.get("status") in ("present", "late")),
            "absent": sum(1 for r in today_records if r.get("status") == "absent"),
            "unmarked": max(len(player_ids) - len(today_records), 0),
        },
        "pending_assessments": pending_assessments,
        "generated_at": now_utc().isoformat(),
    }


async def parent_dashboard(user: dict) -> dict:
    from routers.parents import _wards_for, _public_profile

    wards = await _wards_for(user)
    today = now_utc().strftime("%Y-%m-%d")
    week_ago = (now_utc() - timedelta(days=7)).strftime("%Y-%m-%d")
    children: List[dict] = []

    for w in wards:
        profile = _public_profile(w)
        recent_att = await db.attendance.find(
            {"person_id": w["id"], "date": {"$gte": week_ago}},
            {"_id": 0, "date": 1, "status": 1, "kind": 1},
        ).sort("date", -1).to_list(7)

        inv_rows = await db.invoices.find(
            {
                "person_id": w["id"],
                "balance_due": {"$gt": 0},
                "status": {"$nin": ["cancelled", "draft", "void"]},
            },
            {"_id": 0, "balance_due": 1, "outstanding_amount": 1},
        ).to_list(50)
        outstanding = sum(int(i.get("balance_due") or i.get("outstanding_amount") or 0) for i in inv_rows)

        report_cards = await db.report_cards.find(
            {"person_id": w["id"], "status": "published"},
            {"_id": 0, "id": 1, "exam_term_name": 1, "published_at": 1, "section_label": 1},
        ).sort("published_at", -1).to_list(3)

        today_rec = await db.attendance.find_one(
            {"person_id": w["id"], "date": today},
            {"_id": 0, "status": 1},
        )

        children.append({
            **profile,
            "today_status": (today_rec or {}).get("status"),
            "recent_attendance": recent_att,
            "outstanding_invoices_total": outstanding,
            "outstanding_invoices_count": len(inv_rows),
            "recent_report_cards": report_cards,
        })

    return {
        "role": "parent",
        "today": today,
        "children": children,
        "generated_at": now_utc().isoformat(),
    }


async def build_mvp_dashboard(user: dict, entity: Optional[str] = None) -> dict:
    role = user.get("role")
    if role == "super_admin":
        return await super_admin_dashboard(user, entity)
    if role == "admin":
        return await admin_dashboard(user)
    if role == "teacher":
        return await teacher_dashboard(user)
    if role == "coach":
        return await coach_dashboard_mvp(user)
    if role == "parent":
        return await parent_dashboard(user)
    return {
        "role": role,
        "today": now_utc().strftime("%Y-%m-%d"),
        "message": "Use GET /dashboard for generic stats",
    }
