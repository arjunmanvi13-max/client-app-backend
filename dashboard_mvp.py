"""Role-based dashboard MVP aggregations (no advanced financial analytics)."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional, List, Dict, Any

import logging

from core import (
    db,
    now_utc,
    resolve_user_institution,
    person_entity_filter,
    fee_entity_filter,
    attendance_entity_filter,
    is_super_admin,
    is_admin,
    is_pws_admin_user,
    is_pws_accounts_user,
    is_alpha_admin_user,
    is_alpha_accounts_user,
    is_teacher_user,
    today_ist,
)
from academic_class_roster import class_roster_query_for_section_ids
from notifications_service import notification_filter_for_user, normalize_notification

logger = logging.getLogger(__name__)


def _entity_param(entity: Optional[str]) -> str:
    raw = (entity or "both").strip().lower()
    if raw in ("pws", "alpha"):
        return raw.upper()
    return "BOTH"


async def _attendance_totals_today(entity: str) -> dict:
    today = today_ist()
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
    today = today_ist()
    base = {"status": "paid", "paid_on": today}
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
        "today": today_ist(),
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


def _empty_teacher_dashboard(today: str) -> dict:
    return {
        "role": "teacher",
        "today": today,
        "assigned_classes": [],
        "attendance_today": [],
        "pending_marks_entry": 0,
        "recent_notifications": [],
        "unread_notifications": 0,
        "generated_at": now_utc().isoformat(),
    }


async def _teacher_notifications(user: dict) -> dict:
    notif_rows = await db.notifications.find(
        notification_filter_for_user(user),
        {"_id": 0},
    ).sort("created_at", -1).to_list(5)
    notifications = [normalize_notification(n) for n in notif_rows]
    return {
        "recent_notifications": notifications,
        "unread_notifications": sum(1 for n in notifications if not n.get("read")),
    }


async def teacher_dashboard(user: dict) -> dict:
    today = today_ist()
    out = _empty_teacher_dashboard(today)
    try:
        open_year = await db.academic_years.find_one(
            {"entity_id": "pws", "status": "open"},
            {"_id": 0, "id": 1},
        )
        year_id = (open_year or {}).get("id")
        if not year_id:
            out.update(await _teacher_notifications(user))
            return out

        rows = await db.teacher_class_assignments.find(
            {"teacher_user_id": user["id"], "academic_year_id": year_id},
            {"_id": 0, "section_id": 1, "subject_id": 1},
        ).to_list(200)
        pairs: List[tuple] = []
        seen = set()
        for r in rows:
            sid, sub = r.get("section_id"), r.get("subject_id")
            if not sid or not sub or (sid, sub) in seen:
                continue
            seen.add((sid, sub))
            pairs.append((sid, sub))
        section_ids = list(dict.fromkeys(sid for sid, _ in pairs))
        subject_ids = list(dict.fromkeys(sub for _, sub in pairs))

        sections = await db.sections.find(
            {"id": {"$in": section_ids}},
            {"_id": 0, "id": 1, "label": 1, "grade_name": 1},
        ).to_list(200) if section_ids else []
        subjects = await db.subjects.find(
            {"id": {"$in": subject_ids}},
            {"_id": 0, "id": 1, "name": 1},
        ).to_list(200) if subject_ids else []
        section_map = {s["id"]: s for s in sections}
        subject_map = {s["id"]: s for s in subjects}

        assignments = []
        for sid, sub in pairs:
            section = section_map.get(sid) or {}
            subject = subject_map.get(sub) or {}
            assignments.append({
                "section_id": sid,
                "section_label": section.get("label"),
                "grade_name": section.get("grade_name"),
                "subject_id": sub,
                "subject_name": subject.get("name"),
            })

        students_by_section: Dict[str, list] = {sid: [] for sid in section_ids}
        if section_ids:
            roster_q = await class_roster_query_for_section_ids(section_ids)
            people = await db.people.find(
                roster_q,
                {"_id": 0, "id": 1, "section_id": 1, "group": 1},
            ).to_list(8000)
            label_to_sid = {s["label"]: s["id"] for s in sections if s.get("label")}
            for person in people:
                sid = person.get("section_id")
                if sid not in students_by_section:
                    sid = label_to_sid.get(person.get("group") or "")
                if sid in students_by_section and person.get("id"):
                    students_by_section[sid].append(person["id"])

        att_counts = {sid: {"marked": 0, "present": 0} for sid in section_ids}
        pid_to_section = {
            pid: sid
            for sid, ids in students_by_section.items()
            for pid in ids
        }
        all_ids = list(pid_to_section)
        if all_ids:
            att_rows = await db.attendance.find(
                {"person_id": {"$in": all_ids}, "date": today, "kind": "student"},
                {"_id": 0, "person_id": 1, "status": 1},
            ).to_list(20000)
            for row in att_rows:
                sid = pid_to_section.get(row.get("person_id"))
                if not sid:
                    continue
                att_counts[sid]["marked"] += 1
                if row.get("status") in ("present", "late"):
                    att_counts[sid]["present"] += 1

        section_attendance = []
        for sid in section_ids:
            section = section_map.get(sid) or {}
            ids = students_by_section.get(sid) or []
            counts = att_counts.get(sid) or {"marked": 0, "present": 0}
            section_attendance.append({
                "section_id": sid,
                "section_label": section.get("label"),
                "total_students": len(ids),
                "marked_today": counts["marked"],
                "present_today": counts["present"],
            })

        pending_marks = 0
        if pairs:
            assessments = await db.assessments.find(
                {
                    "academic_year_id": year_id,
                    "$or": [{"section_id": sid, "subject_id": sub} for sid, sub in pairs],
                },
                {"_id": 0, "id": 1, "section_id": 1},
            ).to_list(200)
            marked_by_asm: Dict[str, int] = {}
            asm_ids = [a["id"] for a in assessments if a.get("id")]
            if asm_ids:
                grouped = await db.academic_marks.aggregate([
                    {"$match": {"assessment_id": {"$in": asm_ids}}},
                    {"$group": {"_id": "$assessment_id", "n": {"$sum": 1}}},
                ]).to_list(200)
                marked_by_asm = {row["_id"]: row["n"] for row in grouped}
            for asm in assessments:
                need = len(students_by_section.get(asm.get("section_id"), []))
                if marked_by_asm.get(asm.get("id"), 0) < need:
                    pending_marks += 1

        out["assigned_classes"] = assignments
        out["attendance_today"] = section_attendance
        out["pending_marks_entry"] = pending_marks
        out.update(await _teacher_notifications(user))
        return out
    except Exception:
        logger.exception("Teacher dashboard failed for user %s", user.get("id"))
        out["error"] = "Some of today's data could not be loaded."
        try:
            out.update(await _teacher_notifications(user))
        except Exception:
            logger.exception("Teacher notifications failed for user %s", user.get("id"))
        return out


async def coach_dashboard_mvp(user: dict) -> dict:
    from routers.coach import _coach_visibility_filter

    today = today_ist()
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
    today = today_ist()
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


async def pws_admin_dashboard(user: dict) -> dict:
    """PWS-scoped org dashboard — admin, accounts, principal roles."""
    data = await super_admin_dashboard(user, "pws")
    data["role"] = user.get("role") or "pws_admin"
    data["entity_label"] = "PWS"
    return data


async def alpha_org_dashboard(user: dict) -> dict:
    """ALPHA-scoped org dashboard — admin, accounts roles."""
    data = await super_admin_dashboard(user, "alpha")
    data["role"] = user.get("role") or "admin"
    data["entity_label"] = "ALPHA"
    return data


async def build_mvp_dashboard(user: dict, entity: Optional[str] = None) -> dict:
    role = user.get("role")
    if role == "super_admin":
        return await super_admin_dashboard(user, entity)
    if is_pws_admin_user(user) or is_pws_accounts_user(user):
        return await pws_admin_dashboard(user)
    if is_alpha_admin_user(user) or is_alpha_accounts_user(user):
        return await alpha_org_dashboard(user)
    if role == "admin":
        return await admin_dashboard(user)
    if is_teacher_user(user) or (user.get("user_type") or "").strip().lower() == "pws_teacher":
        return await teacher_dashboard(user)
    if role == "coach":
        return await coach_dashboard_mvp(user)
    if role == "parent":
        return await parent_dashboard(user)
    return {
        "role": role,
        "today": today_ist(),
        "message": "Use GET /dashboard for generic stats",
    }
