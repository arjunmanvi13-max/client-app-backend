"""Layer 1 (Command Centre) and Layer 2 (Department) aggregator endpoints."""
from datetime import datetime, timedelta
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException
from core import db, get_current_user, is_admin, now_utc, active_status_filter, merge_mongo_query

router = APIRouter(tags=["command"])

# ---------- helpers ----------
ENTITY_KINDS = ["teacher", "staff", "coach", "student", "player"]

async def _attendance_today_by_kind() -> dict:
    today = now_utc().strftime("%Y-%m-%d")
    pipeline = [
        {"$match": {"date": today}},
        {"$group": {"_id": {"kind": "$kind", "status": "$status"}, "count": {"$sum": 1}}},
    ]
    rows = await db.attendance.aggregate(pipeline).to_list(200)
    out = {}
    for r in rows:
        kind = r["_id"]["kind"]
        st = r["_id"]["status"]
        out.setdefault(kind, {"present": 0, "absent": 0, "late": 0, "leave": 0})
        out[kind][st] = r["count"]
    return out

async def _hostel_today() -> dict:
    today = now_utc().strftime("%Y-%m-%d")
    morning_present = await db.roll_calls.count_documents({"date": today, "session": "morning", "present": True})
    morning_absent = await db.roll_calls.count_documents({"date": today, "session": "morning", "present": False})
    night_present = await db.roll_calls.count_documents({"date": today, "session": "night", "present": True})
    night_absent = await db.roll_calls.count_documents({"date": today, "session": "night", "present": False})
    out_on_pass = await db.gate_passes.count_documents({"status": "approved"})
    pending_pass = await db.gate_passes.count_documents({"status": "pending"})
    residents = await db.people.count_documents({"is_resident": True})
    return {
        "residents": residents,
        "morning_present": morning_present,
        "morning_absent": morning_absent,
        "night_present": night_present,
        "night_absent": night_absent,
        "out_on_pass": out_on_pass,
        "pending_pass": pending_pass,
    }

async def _task_snapshot() -> dict:
    statuses = ["open", "in_progress", "blocked", "completed", "cancelled"]
    legacy_map = {"assigned": "open", "delayed": "blocked", "reviewed": "completed"}
    by_status = {s: 0 for s in statuses}
    async for doc in db.tasks.find({}, {"status": 1}):
        raw = doc.get("status") or "open"
        norm = legacy_map.get(raw, raw)
        if norm not in by_status:
            norm = "open"
        by_status[norm] += 1
    total = sum(by_status.values())
    # Department breakdown — derive from `department` field; fallback to "Other"
    pipeline = [{"$group": {"_id": "$department", "count": {"$sum": 1}}}]
    rows = await db.tasks.aggregate(pipeline).to_list(50)
    by_dept = {(r["_id"] or "Other"): r["count"] for r in rows}
    completed = by_status.get("completed", 0)
    completion_pct = round((completed / total) * 100) if total else 0
    return {"total": total, "by_status": by_status, "by_department": by_dept, "completion_pct": completion_pct}

async def _alerts() -> list:
    alerts = []
    today = now_utc().strftime("%Y-%m-%d")
    # Overdue tasks
    overdue = await db.tasks.count_documents({
        "status": {"$nin": ["completed", "reviewed", "cancelled"]},
        "$or": [
            {"due_date": {"$lt": now_utc().isoformat()}},
            {"deadline": {"$lt": now_utc().isoformat()}},
        ],
    })
    if overdue:
        alerts.append({"type": "overdue_tasks", "severity": "high", "message": f"{overdue} task(s) overdue", "count": overdue})
    # Unmarked player attendance — players without a record today
    total_players = await db.people.count_documents({"kind": "player"})
    marked_player_ids = await db.attendance.distinct("person_id", {"kind": "player", "date": today})
    unmarked = max(total_players - len(marked_player_ids), 0)
    if unmarked:
        alerts.append({"type": "unmarked_player_attendance", "severity": "medium", "message": f"{unmarked} player(s) attendance not marked today", "count": unmarked})
    # Pending gate passes
    pending = await db.gate_passes.count_documents({"status": "pending"})
    if pending:
        alerts.append({"type": "pending_gate_passes", "severity": "medium", "message": f"{pending} hostel gate pass(es) awaiting approval", "count": pending})
    # Late hostel entries — gate passes expected_return < now still open (out on pass past expected)
    late = await db.gate_passes.count_documents({"status": "approved", "expected_return": {"$lt": now_utc().isoformat()}})
    if late:
        alerts.append({"type": "late_hostel_entries", "severity": "high", "message": f"{late} resident(s) past expected return", "count": late})
    return alerts

async def _kpis(att: dict, tasks: dict) -> dict:
    # Attendance % across all kinds today
    total_marks = 0
    present_marks = 0
    for kind, vals in att.items():
        total_marks += sum(vals.values())
        present_marks += vals.get("present", 0)
    att_pct = round((present_marks / total_marks) * 100) if total_marks else 0
    # Department efficiency: % of completed tasks per department
    return {
        "attendance_pct_today": att_pct,
        "task_completion_pct": tasks["completion_pct"],
    }

def _require_admin(user: dict):
    if not is_admin(user):
        raise HTTPException(403, "Super Admin / Admin required")

# ---------- Layer 1 ----------
@router.get("/command-center")
async def command_center(user: dict = Depends(get_current_user)):
    _require_admin(user)
    from core import is_sports_admin
    sports_only = is_sports_admin(user)

    att = await _attendance_today_by_kind()
    hostel = await _hostel_today()
    tasks = await _task_snapshot()
    alerts = await _alerts()
    kpis = await _kpis(att, tasks)

    # Per-entity counts — Sports Admin sees ALPHA only
    if sports_only:
        roster_counts = {
            "teachers": 0,
            "coaches": await db.users.count_documents({"role": "coach"}),
            "staff": await db.people.count_documents({"kind": "staff", "organization": "ALPHA"}),
            "students": 0,
            "players": await db.people.count_documents({"kind": "player", "organization": "ALPHA", "status": {"$ne": "deactivated"}}),
            "deactivated_players": await db.people.count_documents({"kind": "player", "organization": "ALPHA", "status": "deactivated"}),
        }
        deactivated_players = await db.people.find(
            {"kind": "player", "organization": "ALPHA", "status": "deactivated"}, {"_id": 0}
        ).sort("name", 1).to_list(500)
        # Strip PWS-only attendance kinds
        att = {k: v for k, v in att.items() if k not in ("student", "teacher")}
    else:
        roster_counts = {
            "teachers": await db.users.count_documents({"role": "teacher"}),
            "coaches": await db.users.count_documents({"role": "coach"}),
            "staff": await db.people.count_documents({"kind": "staff"}),
            "students": await db.people.count_documents({"kind": "student"}),
            "players": await db.people.count_documents({"kind": "player", "status": {"$ne": "deactivated"}}),
            "deactivated_players": await db.people.count_documents({"kind": "player", "status": "deactivated"}),
        }
        deactivated_players = await db.people.find(
            {"kind": "player", "status": "deactivated"}, {"_id": 0}
        ).sort("name", 1).to_list(500)

    # Department quick snapshots
    departments = {
        "school": {
            "students": roster_counts["students"],
            "teachers": roster_counts["teachers"],
            "today_attendance": att.get("student", {}),
        },
        "sports": {
            "players": roster_counts["players"],
            "coaches": roster_counts["coaches"],
            "today_attendance": att.get("player", {}),
        },
        "hostel": hostel,
    }

    return {
        "date": now_utc().strftime("%Y-%m-%d"),
        "roster_counts": roster_counts,
        "deactivated_players": deactivated_players,
        "attendance_by_kind": att,
        "tasks": tasks,
        "alerts": alerts,
        "kpis": kpis,
        "departments": departments,
    }

# ---------- Layer 2: department drill-downs ----------
@router.get("/departments/school")
async def dept_school(user: dict = Depends(get_current_user)):
    _require_admin(user)
    today = now_utc().strftime("%Y-%m-%d")
    # Class-wise attendance
    pipeline = [
        {"$match": {"kind": "student", "date": today}},
        {"$group": {"_id": {"group": "$group", "status": "$status"}, "count": {"$sum": 1}}},
    ]
    rows = await db.attendance.aggregate(pipeline).to_list(200)
    by_class: dict = defaultdict(lambda: {"present": 0, "absent": 0, "late": 0, "leave": 0})
    for r in rows:
        by_class[r["_id"]["group"] or "Unassigned"][r["_id"]["status"]] = r["count"]
    teachers = await db.users.find(
        merge_mongo_query({"role": "teacher"}, active_status_filter()),
        {"_id": 0, "password_hash": 0},
    ).to_list(500)
    students = await db.people.count_documents({"kind": "student"})
    return {
        "date": today,
        "students": students,
        "teachers_count": len(teachers),
        "teachers": teachers,
        "by_class": dict(by_class),
    }

@router.get("/departments/sports")
async def dept_sports(user: dict = Depends(get_current_user)):
    _require_admin(user)
    today = now_utc().strftime("%Y-%m-%d")
    pipeline = [
        {"$match": {"kind": "player", "date": today}},
        {"$group": {"_id": {"slot": "$slot", "status": "$status"}, "count": {"$sum": 1}}},
    ]
    rows = await db.attendance.aggregate(pipeline).to_list(200)
    by_slot: dict = defaultdict(lambda: {"present": 0, "absent": 0})
    for r in rows:
        by_slot[r["_id"]["slot"] or "Unassigned"][r["_id"]["status"]] = r["count"]
    coaches = await db.users.find(
        merge_mongo_query({"role": "coach"}, active_status_filter()),
        {"_id": 0, "password_hash": 0},
    ).to_list(500)
    pipeline2 = [
        {"$match": {"kind": "player"}},
        {"$group": {"_id": "$sport", "count": {"$sum": 1}}},
    ]
    sport_rows = await db.people.aggregate(pipeline2).to_list(50)
    by_sport = {(r["_id"] or "Other"): r["count"] for r in sport_rows}
    return {
        "date": today,
        "coaches_count": len(coaches),
        "coaches": coaches,
        "players": await db.people.count_documents({"kind": "player"}),
        "by_slot": dict(by_slot),
        "by_sport": by_sport,
    }

@router.get("/departments/hostel")
async def dept_hostel(user: dict = Depends(get_current_user)):
    _require_admin(user)
    return await _hostel_today()
