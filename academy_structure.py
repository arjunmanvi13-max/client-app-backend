"""Academy Structure baselines and Super Admin dashboard metrics."""
from __future__ import annotations

from typing import Dict, List, Optional

from core import (
    db,
    now_utc,
    person_entity_filter,
    fee_entity_filter,
    is_super_admin,
)

ACADEMY_CATEGORIES = ["Day Boarding", "Boarding", "Hostel", "Daily Players"]

PWS_CLASS_KEYS = [
    "nursery", "lkg", "ukg",
    "std1", "std2", "std3", "std4", "std5",
    "std6", "std7", "std8", "std9", "std10",
]

PWS_CLASS_LABELS = {
    "nursery": "Nursery",
    "lkg": "LKG",
    "ukg": "UKG",
    "std1": "Std 1",
    "std2": "Std 2",
    "std3": "Std 3",
    "std4": "Std 4",
    "std5": "Std 5",
    "std6": "Std 6",
    "std7": "Std 7",
    "std8": "Std 8",
    "std9": "Std 9",
    "std10": "Std 10",
}

PWS_DB_CLASS_TO_KEY = {
    "Nursery": "nursery",
    "LKG": "lkg",
    "UKG": "ukg",
    "Class I": "std1",
    "Class II": "std2",
    "Class III": "std3",
    "Class IV": "std4",
    "Class V": "std5",
    "Class VI": "std6",
    "Class VII": "std7",
    "Class VIII": "std8",
    "Class IX": "std9",
    "Class X": "std10",
}

ALPHA_CATEGORY_KEYS = ["dayBoarding", "boarding", "hostel", "dailyPlayers"]
ALPHA_CATEGORY_LABELS = {
    "dayBoarding": "Day Boarding",
    "boarding": "Boarding",
    "hostel": "Hostel",
    "dailyPlayers": "Daily Players",
}

PLAYER_TYPE_TO_ALPHA_KEY = {
    "Day Boarding": "dayBoarding",
    "Boarding": "boarding",
    "Hostel": "hostel",
    "Hostel Only": "hostel",
    "Daily": "dailyPlayers",
}

SPORT_KEYS = ["cricket", "football"]
SPORT_DB_TO_KEY = {"Cricket": "cricket", "Football": "football"}

DEFAULT_CATEGORY_BASELINES = {cat: 0 for cat in ACADEMY_CATEGORIES}


def _entity_scope(entity: Optional[str]) -> str:
    raw = (entity or "both").strip().upper()
    if raw in ("PWS", "ALPHA"):
        return raw
    return "BOTH"


def _empty_pws_classes() -> Dict[str, int]:
    return {k: 0 for k in PWS_CLASS_KEYS}


def _empty_alpha_matrix() -> Dict[str, Dict[str, int]]:
    return {cat: {"cricket": 0, "football": 0} for cat in ALPHA_CATEGORY_KEYS}


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


async def get_pws_baselines() -> Dict[str, int]:
    doc = await db.academy_structure.find_one({"entity": "PWS"}, {"_id": 0})
    stored = (doc or {}).get("pws_classes") or {}
    return {k: max(0, int(stored.get(k) or 0)) for k in PWS_CLASS_KEYS}


async def get_alpha_baselines() -> Dict[str, Dict[str, int]]:
    doc = await db.academy_structure.find_one({"entity": "ALPHA"}, {"_id": 0})
    stored = (doc or {}).get("alpha_matrix") or {}
    out = _empty_alpha_matrix()
    for cat in ALPHA_CATEGORY_KEYS:
        row = stored.get(cat) or {}
        out[cat] = {
            "cricket": max(0, int(row.get("cricket") or 0)),
            "football": max(0, int(row.get("football") or 0)),
        }
    return out


async def get_entity_baselines(entity: str) -> Dict[str, int]:
    """Legacy aggregate category totals for revenue widgets."""
    out = dict(DEFAULT_CATEGORY_BASELINES)
    if entity in ("PWS", "BOTH"):
        pws_total = sum((await get_pws_baselines()).values())
        out["Daily Players"] += pws_total
    if entity in ("ALPHA", "BOTH"):
        matrix = await get_alpha_baselines()
        for cat_key, label_key in [
            ("dayBoarding", "Day Boarding"),
            ("boarding", "Boarding"),
            ("hostel", "Hostel"),
            ("dailyPlayers", "Daily Players"),
        ]:
            row = matrix.get(cat_key) or {}
            out[label_key] += int(row.get("cricket") or 0) + int(row.get("football") or 0)
    return out


async def save_pws_baselines(pws_classes: Dict[str, int], user_id: str) -> dict:
    cleaned = {k: max(0, int(pws_classes.get(k) or 0)) for k in PWS_CLASS_KEYS}
    doc = {
        "entity": "PWS",
        "pws_classes": cleaned,
        "updated_at": now_utc().isoformat(),
        "updated_by": user_id,
    }
    await db.academy_structure.update_one({"entity": "PWS"}, {"$set": doc}, upsert=True)
    return doc


async def save_alpha_baselines(alpha_matrix: Dict[str, Dict[str, int]], user_id: str) -> dict:
    cleaned = _empty_alpha_matrix()
    for cat in ALPHA_CATEGORY_KEYS:
        row = alpha_matrix.get(cat) or {}
        cleaned[cat] = {
            "cricket": max(0, int(row.get("cricket") or 0)),
            "football": max(0, int(row.get("football") or 0)),
        }
    doc = {
        "entity": "ALPHA",
        "alpha_matrix": cleaned,
        "updated_at": now_utc().isoformat(),
        "updated_by": user_id,
    }
    await db.academy_structure.update_one({"entity": "ALPHA"}, {"$set": doc}, upsert=True)
    return doc


async def save_entity_baselines(entity: str, categories: Dict[str, int], user_id: str) -> dict:
    """Legacy saver — maps flat categories into ALPHA matrix totals."""
    ent = entity.upper()
    if ent == "PWS":
        daily = int(categories.get("Daily Players") or 0)
        return await save_pws_baselines({"nursery": daily}, user_id)
    if ent == "ALPHA":
        matrix = _empty_alpha_matrix()
        matrix["dayBoarding"]["cricket"] = int(categories.get("Day Boarding") or 0)
        matrix["boarding"]["cricket"] = int(categories.get("Boarding") or 0)
        matrix["hostel"]["cricket"] = int(categories.get("Hostel") or 0)
        matrix["dailyPlayers"]["cricket"] = int(categories.get("Daily Players") or 0)
        return await save_alpha_baselines(matrix, user_id)
    raise ValueError("entity must be PWS or ALPHA")


async def _count_pws_by_class(inst: str) -> Dict[str, int]:
    counts = _empty_pws_classes()
    if inst == "ALPHA":
        return counts
    base_q: dict = {"status": {"$ne": "deactivated"}, "kind": "student"}
    ent_f = person_entity_filter(inst if inst != "BOTH" else "PWS")
    if ent_f:
        base_q = {"$and": [base_q, ent_f]}
    students = await db.people.find(base_q, {"_id": 0, "pws_class": 1}).to_list(5000)
    for row in students:
        key = PWS_DB_CLASS_TO_KEY.get(row.get("pws_class") or "")
        if key:
            counts[key] += 1
    return counts


async def _count_alpha_matrix(inst: str) -> Dict[str, Dict[str, int]]:
    counts = _empty_alpha_matrix()
    if inst == "PWS":
        return counts
    base_q: dict = {"status": {"$ne": "deactivated"}, "kind": "player"}
    ent_f = person_entity_filter(inst if inst != "BOTH" else "ALPHA")
    if ent_f:
        base_q = {"$and": [base_q, ent_f]}
    players = await db.people.find(
        base_q,
        {"_id": 0, "player_type": 1, "sport": 1},
    ).to_list(5000)
    for row in players:
        cat_key = PLAYER_TYPE_TO_ALPHA_KEY.get(row.get("player_type") or "")
        sport_key = SPORT_DB_TO_KEY.get(row.get("sport") or "")
        if cat_key and sport_key:
            counts[cat_key][sport_key] += 1
    return counts


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

    async def role_stats(kind: str) -> dict:
        roster = 0
        if kind == "coach":
            if inst == "PWS":
                roster = 0
            else:
                roster = await db.users.count_documents({"role": "coach", "status": {"$ne": "deactivated"}})
        elif kind == "staff":
            if inst == "PWS":
                roster_q = {
                    "kind": "staff",
                    "status": {"$ne": "deactivated"},
                    "$or": [{"organization": "PWS"}, {"organization": "BOTH"}, {"organization": {"$exists": False}}],
                }
            elif inst == "ALPHA":
                roster_q = {"kind": "staff", "status": {"$ne": "deactivated"}, "organization": "ALPHA"}
            else:
                roster_q = {"kind": "staff", "status": {"$ne": "deactivated"}}
            roster = await db.people.count_documents(roster_q)

        att_q: dict = {"date": today, "kind": kind}
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

    active_legacy = await _count_enrollment(inst)
    baselines_legacy = await get_entity_baselines(inst)
    enrollment = []
    for cat in ACADEMY_CATEGORIES:
        active_count = active_legacy.get(cat, 0)
        baseline = baselines_legacy.get(cat, 0)
        enrollment.append({
            "category": cat,
            "active": active_count,
            "baseline": baseline,
            "gap": max(0, baseline - active_count),
            "utilization_pct": round((active_count / baseline) * 100, 1) if baseline else None,
        })

    pws_active = await _count_pws_by_class(inst)
    pws_baselines = await get_pws_baselines()
    pws_enrollment = []
    for key in PWS_CLASS_KEYS:
        active_count = pws_active.get(key, 0)
        baseline = pws_baselines.get(key, 0)
        pws_enrollment.append({
            "key": key,
            "label": PWS_CLASS_LABELS[key],
            "active": active_count,
            "baseline": baseline,
            "gap": max(0, baseline - active_count),
        })

    alpha_active = await _count_alpha_matrix(inst)
    alpha_baselines = await get_alpha_baselines()
    alpha_enrollment = []
    for cat_key in ALPHA_CATEGORY_KEYS:
        active_row = alpha_active.get(cat_key) or {}
        baseline_row = alpha_baselines.get(cat_key) or {}
        sports = {}
        for sport in SPORT_KEYS:
            active_count = int(active_row.get(sport) or 0)
            baseline = int(baseline_row.get(sport) or 0)
            sports[sport] = {
                "active": active_count,
                "baseline": baseline,
                "gap": max(0, baseline - active_count),
            }
        alpha_enrollment.append({
            "key": cat_key,
            "category": ALPHA_CATEGORY_LABELS[cat_key],
            "sports": sports,
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
        "pws_enrollment": pws_enrollment,
        "alpha_enrollment": alpha_enrollment,
        "pws_total_baseline": sum(pws_baselines.values()),
        "pws_total_active": sum(pws_active.values()),
        "alpha_totals": {
            "cricket": sum(int((alpha_baselines.get(c) or {}).get("cricket") or 0) for c in ALPHA_CATEGORY_KEYS),
            "football": sum(int((alpha_baselines.get(c) or {}).get("football") or 0) for c in ALPHA_CATEGORY_KEYS),
        },
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
