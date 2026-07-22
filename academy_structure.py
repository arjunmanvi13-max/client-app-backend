"""Academy Structure baselines and Super Admin dashboard metrics."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core import (
    db,
    now_utc,
    person_entity_filter,
    fee_entity_filter,
    is_super_admin,
)

ACADEMY_CATEGORIES = ["Day Boarding", "Boarding", "Hostel", "Daily Players"]

DEFAULT_CATEGORY_BASELINES = {cat: 0 for cat in ACADEMY_CATEGORIES}


def _entity_scope(entity: Optional[str]) -> str:
    raw = (entity or "both").strip().upper()
    if raw in ("PWS", "ALPHA"):
        return raw
    return "BOTH"


def map_enrollment_category(kind: str, raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    if raw in ("Day School", "Daily"):
        return "Daily Players"
    if raw in ("Hostel", "Hostel Only"):
        return "Hostel"
    if raw == "Boarding":
        return "Boarding"
    if raw == "Day Boarding":
        return "Day Boarding"
    return None


def map_fee_category(raw: Optional[str]) -> Optional[str]:
    return map_enrollment_category("", raw)


async def get_entity_baselines(entity: str) -> Dict[str, int]:
    """Return merged category baselines for PWS, ALPHA, or BOTH."""
    out = dict(DEFAULT_CATEGORY_BASELINES)
    targets = ["PWS", "ALPHA"] if entity == "BOTH" else [entity]
    for ent in targets:
        doc = await db.academy_structure.find_one({"entity": ent}, {"_id": 0})
        cats = (doc or {}).get("categories") or {}
        for cat in ACADEMY_CATEGORIES:
            out[cat] += int(cats.get(cat) or 0)
    return out


async def save_entity_baselines(entity: str, categories: Dict[str, int], user_id: str) -> dict:
    ent = entity.upper()
    if ent not in ("PWS", "ALPHA"):
        raise ValueError("entity must be PWS or ALPHA")
    cleaned = {cat: max(0, int(categories.get(cat) or 0)) for cat in ACADEMY_CATEGORIES}
    doc = {
        "entity": ent,
        "categories": cleaned,
        "updated_at": now_utc().isoformat(),
        "updated_by": user_id,
    }
    await db.academy_structure.update_one({"entity": ent}, {"$set": doc}, upsert=True)
    return doc


async def _count_enrollment(inst: str) -> Dict[str, int]:
    counts = dict(DEFAULT_CATEGORY_BASELINES)
    base_q: dict = {"status": {"$ne": "deactivated"}}
    ent_f = person_entity_filter(inst)
    if ent_f:
        base_q = {"$and": [base_q, ent_f]}

    if inst in ("PWS", "BOTH"):
        students = await db.people.find(
            {**base_q, "kind": "student"},
            {"_id": 0, "pws_student_type": 1},
        ).to_list(5000)
        for row in students:
            cat = map_enrollment_category("student", row.get("pws_student_type"))
            if cat:
                counts[cat] += 1

    if inst in ("ALPHA", "BOTH"):
        players = await db.people.find(
            {**base_q, "kind": "player"},
            {"_id": 0, "player_type": 1},
        ).to_list(5000)
        for row in players:
            cat = map_enrollment_category("player", row.get("player_type"))
            if cat:
                counts[cat] += 1

    return counts


async def _fee_base_match(inst: str) -> dict:
    base: dict = {}
    ent_f = fee_entity_filter(inst)
    if ent_f:
        base.update(ent_f)
    return base


async def _revenue_metrics(inst: str, this_month: str) -> dict:
    base = await _fee_base_match(inst)
    by_category: Dict[str, dict] = {
        cat: {"expected": 0, "collected": 0, "gap": 0} for cat in ACADEMY_CATEGORIES
    }

    rows = await db.fees.find(
        {**base, "period_month": this_month},
        {"_id": 0, "category": 1, "amount_due": 1, "status": 1},
    ).to_list(10000)

    expected_total = 0
    collected_total = 0
    for row in rows:
        amount = int(row.get("amount_due") or 0)
        cat = map_fee_category(row.get("category")) or "Daily Players"
        if cat not in by_category:
            by_category[cat] = {"expected": 0, "collected": 0, "gap": 0}
        by_category[cat]["expected"] += amount
        expected_total += amount
        if row.get("status") == "paid":
            by_category[cat]["collected"] += amount
            collected_total += amount

    for cat, vals in by_category.items():
        vals["gap"] = vals["expected"] - vals["collected"]

    return {
        "expected_monthly": expected_total,
        "collected_monthly": collected_total,
        "collection_gap": expected_total - collected_total,
        "by_category": [
            {"category": cat, **by_category[cat]} for cat in ACADEMY_CATEGORIES
        ],
    }


async def _aging_dues(inst: str, this_month: str) -> dict:
    base = {**await _fee_base_match(inst), "status": "due"}
    current_rows = await db.fees.aggregate([
        {"$match": {**base, "period_month": this_month}},
        {"$group": {"_id": None, "total": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
    ]).to_list(1)
    overdue_rows = await db.fees.aggregate([
        {"$match": {**base, "period_month": {"$lt": this_month}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
    ]).to_list(1)
    return {
        "current_month_dues": int(current_rows[0]["total"]) if current_rows else 0,
        "current_month_count": current_rows[0]["count"] if current_rows else 0,
        "overdue_past_month": int(overdue_rows[0]["total"]) if overdue_rows else 0,
        "overdue_count": overdue_rows[0]["count"] if overdue_rows else 0,
    }


async def _attendance_for_roles(inst: str) -> dict:
    today = now_utc().strftime("%Y-%m-%d")

    async def role_stats(kind: str, org_filter: Optional[dict] = None) -> dict:
        roster_q: dict = {"kind": kind, "status": {"$ne": "deactivated"}}
        if org_filter:
            roster_q = {"$and": [roster_q, org_filter]}
        roster = await db.people.count_documents(roster_q)
        if kind == "coach":
            user_q = {"role": "coach", "status": {"$ne": "deactivated"}}
            if inst == "PWS":
                roster = 0
            elif inst == "ALPHA":
                roster = await db.users.count_documents(user_q)
            else:
                roster = await db.users.count_documents(user_q)
        elif kind == "staff":
            if inst == "PWS":
                roster_q = {
                    "kind": "staff",
                    "status": {"$ne": "deactivated"},
                    "$or": [{"organization": "PWS"}, {"organization": "BOTH"}, {"organization": {"$exists": False}}],
                }
            elif inst == "ALPHA":
                roster_q = {"kind": "staff", "status": {"$ne": "deactivated"}, "organization": "ALPHA"}
            roster = await db.people.count_documents(roster_q)

        att_q: dict = {"date": today, "kind": kind}
        if kind == "coach":
            att_q["kind"] = "coach"
        pipeline = [
            {"$match": att_q},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]
        rows = await db.attendance.aggregate(pipeline).to_list(20)
        stats = {"present": 0, "absent": 0, "late": 0, "leave": 0}
        for r in rows:
            st = r["_id"] or "present"
            if st in stats:
                stats[st] = r["count"]
        return {"roster": roster, **stats}

    return {
        "coaches": await role_stats("coach"),
        "staff": await role_stats("staff"),
    }


def _task_entity_filter(inst: str) -> dict:
    if inst == "PWS":
        return {"$or": [{"entity_id": "pws"}, {"entity_id": "both"}, {"entity_id": {"$exists": False}}]}
    if inst == "ALPHA":
        return {"$or": [{"entity_id": "alpha"}, {"entity_id": "both"}, {"entity_id": {"$exists": False}}]}
    return {}


def _approval_entity_filter(inst: str) -> dict:
    if inst == "PWS":
        return {"entity_id": "pws"}
    if inst == "ALPHA":
        return {"entity_id": "alpha"}
    return {}


async def build_super_admin_metrics(entity: Optional[str]) -> dict:
    inst = _entity_scope(entity)
    today = now_utc().strftime("%Y-%m-%d")
    this_month = today[:7]

    active = await _count_enrollment(inst)
    baselines = await get_entity_baselines(inst)
    enrollment = []
    for cat in ACADEMY_CATEGORIES:
        active_count = active.get(cat, 0)
        baseline = baselines.get(cat, 0)
        enrollment.append({
            "category": cat,
            "active": active_count,
            "baseline": baseline,
            "gap": max(0, baseline - active_count),
            "utilization_pct": round((active_count / baseline) * 100, 1) if baseline else None,
        })

    open_statuses = ["open", "in_progress", "blocked", "assigned", "delayed"]
    task_q: dict = {"status": {"$in": open_statuses}}
    task_ent = _task_entity_filter(inst)
    if task_ent:
        task_q = {"$and": [task_q, task_ent]}

    approval_q: dict = {"status": "pending"}
    approval_ent = _approval_entity_filter(inst)
    if approval_ent:
        approval_q = {"$and": [approval_q, approval_ent]}

    return {
        "entity": inst,
        "date": today,
        "enrollment": enrollment,
        "revenue": await _revenue_metrics(inst, this_month),
        "aging_dues": await _aging_dues(inst, this_month),
        "attendance_roles": await _attendance_for_roles(inst),
        "open_tasks": await db.tasks.count_documents(task_q),
        "pending_approvals": await db.approval_requests.count_documents(approval_q),
    }


def assert_super_admin(user: dict) -> None:
    if not is_super_admin(user):
        from fastapi import HTTPException
        raise HTTPException(403, "Super Admin required")
