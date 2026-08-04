"""PWS Time Table domain service."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from core import db, is_login_user_active, now_utc
from timetable.constants import (
    DAYS_OF_WEEK,
    DEFAULT_MAX_WEEKLY_PERIODS,
    ENTITY_PWS,
    day_of_week_for_date,
    is_teaching_period,
    schedule_group_for_grade,
)

DAY_TO_PYTHON = {d: i for i, d in enumerate(DAYS_OF_WEEK)}  # MON=0


def _parse_time(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m


async def get_year_or_404(year_id: str) -> dict:
    year = await db.academic_years.find_one({"id": year_id, "entity_id": ENTITY_PWS}, {"_id": 0})
    if not year:
        raise HTTPException(404, "Academic year not found")
    return year


async def assert_year_writable(year_id: str) -> dict:
    year = await get_year_or_404(year_id)
    if year.get("status") == "archived":
        raise HTTPException(400, "Archived academic year timetables are read-only")
    return year


async def get_grade_or_404(grade_id: str, year_id: str) -> dict:
    grade = await db.grades.find_one({"id": grade_id, "academic_year_id": year_id, "entity_id": ENTITY_PWS}, {"_id": 0})
    if not grade:
        raise HTTPException(404, "Class not found for PWS academic year")
    return grade


async def get_teacher_or_404(teacher_id: str) -> dict:
    teacher = await db.users.find_one({"id": teacher_id, "role": "teacher"}, {"_id": 0})
    if not teacher:
        raise HTTPException(404, "Teacher not found")
    org = (teacher.get("organization") or "PWS").upper()
    if org not in ("PWS", "BOTH"):
        raise HTTPException(400, "Teacher does not belong to PWS")
    return teacher


async def is_teacher_available(
    teacher_id: str,
    date_iso: str,
    *,
    period_start: Optional[str] = None,
) -> Dict[str, Any]:
    """Derived availability from staff attendance + account status."""
    teacher = await db.users.find_one({"id": teacher_id}, {"_id": 0})
    if not teacher:
        return {"available": False, "reason": "Teacher not found"}
    if not is_login_user_active(teacher) and teacher.get("status") == "deactivated":
        return {"available": False, "reason": "Staff account deactivated"}

    att = await db.attendance.find_one({
        "person_id": teacher_id,
        "date": date_iso[:10],
        "kind": "teacher",
    }, {"_id": 0})

    if not att:
        return {"available": True, "reason": None}

    status = (att.get("status") or "present").lower()
    if status in ("absent", "leave"):
        return {"available": False, "reason": f"Marked {status} on {date_iso[:10]}"}

    if att.get("half_day") and period_start:
        midpoint = _parse_time("10:30")
        if _parse_time(period_start) >= midpoint:
            return {"available": False, "reason": "Half-day leave — afternoon periods blocked"}

    if status == "on_leave":
        return {"available": False, "reason": "On leave"}

    return {"available": True, "reason": None}


async def _active_slot_query(extra: dict) -> dict:
    q = {"entity_id": ENTITY_PWS, "effective_to": None, **extra}
    return q


async def _teacher_booked(
    academic_year_id: str,
    teacher_id: str,
    day_of_week: str,
    period_id: str,
    *,
    exclude_slot_id: Optional[str] = None,
) -> Optional[dict]:
    q = await _active_slot_query({
        "academic_year_id": academic_year_id,
        "teacher_id": teacher_id,
        "day_of_week": day_of_week,
        "period_id": period_id,
        "status": {"$in": ["DRAFT", "PUBLISHED"]},
    })
    if exclude_slot_id:
        q["id"] = {"$ne": exclude_slot_id}
    slot = await db.timetable_slots.find_one(q, {"_id": 0})
    if not slot:
        return None
    grade = await db.grades.find_one({"id": slot.get("class_id")}, {"_id": 0, "name": 1})
    subj = await db.subjects.find_one({"id": slot.get("subject_id")}, {"_id": 0, "name": 1})
    return {
        "slot_id": slot["id"],
        "class_name": grade.get("name") if grade else slot.get("class_id"),
        "subject_name": subj.get("name") if subj else slot.get("subject_id"),
    }


async def _class_booked(
    academic_year_id: str,
    class_id: str,
    section_id: Optional[str],
    day_of_week: str,
    period_id: str,
    *,
    exclude_slot_id: Optional[str] = None,
) -> Optional[dict]:
    q = await _active_slot_query({
        "academic_year_id": academic_year_id,
        "class_id": class_id,
        "day_of_week": day_of_week,
        "period_id": period_id,
        "status": {"$in": ["DRAFT", "PUBLISHED"]},
    })
    if section_id:
        q["section_id"] = section_id
    else:
        q["section_id"] = None
    if exclude_slot_id:
        q["id"] = {"$ne": exclude_slot_id}
    return await db.timetable_slots.find_one(q, {"_id": 0})


async def validate_slot_allocation(
    *,
    academic_year_id: str,
    class_id: str,
    section_id: Optional[str],
    day_of_week: str,
    period_id: str,
    subject_id: Optional[str],
    teacher_id: Optional[str],
    date_iso: Optional[str] = None,
    exclude_slot_id: Optional[str] = None,
) -> None:
    period = await db.timetable_periods.find_one({"id": period_id}, {"_id": 0})
    if not period:
        raise HTTPException(404, "Period not found")

    grade = await get_grade_or_404(class_id, academic_year_id)
    expected_group = schedule_group_for_grade(grade.get("name", ""))
    if period.get("schedule_group") != expected_group:
        raise HTTPException(
            422,
            f"Schedule group mismatch — {grade.get('name')} uses {expected_group} periods",
        )

    if not is_teaching_period(period.get("period_type", "")):
        raise HTTPException(422, "Only teaching periods accept subject/teacher allocation")

    if subject_id:
        subj = await db.subjects.find_one({"id": subject_id, "academic_year_id": academic_year_id}, {"_id": 0})
        if not subj:
            raise HTTPException(404, "Subject not found")
        grade_ids = subj.get("grade_ids") or []
        if grade_ids and class_id not in grade_ids:
            raise HTTPException(422, f"{subj.get('name')} is not mapped to this class")

    existing_class = await _class_booked(
        academic_year_id, class_id, section_id, day_of_week, period_id, exclude_slot_id=exclude_slot_id,
    )
    if existing_class:
        raise HTTPException(422, "This class already has an allocation in this day and period")

    if teacher_id:
        teacher = await get_teacher_or_404(teacher_id)
        if date_iso:
            avail = await is_teacher_available(teacher_id, date_iso, period_start=period.get("start_time"))
            if not avail["available"]:
                raise HTTPException(
                    422,
                    f"{teacher.get('name')} is marked unavailable on {date_iso[:10]} and cannot be allocated a class"
                    + (f" ({avail['reason']})" if avail.get("reason") else ""),
                )
        conflict = await _teacher_booked(
            academic_year_id, teacher_id, day_of_week, period_id, exclude_slot_id=exclude_slot_id,
        )
        if conflict:
            raise HTTPException(
                422,
                f"{teacher.get('name')} is already allocated to {conflict.get('class_name')} — "
                f"{conflict.get('subject_name') or 'subject'} in this period",
            )


async def write_audit(
    *,
    entity_id: str,
    action: str,
    actor: dict,
    slot_id: Optional[str] = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> None:
    await db.timetable_audit_log.insert_one({
        "id": str(uuid.uuid4()),
        "entity_id": entity_id,
        "action": action,
        "slot_id": slot_id,
        "before_json": before,
        "after_json": after,
        "actor_user_id": actor.get("id"),
        "actor_role": actor.get("role"),
        "timestamp": now_utc().isoformat(),
        "ip_address": ip_address,
    })


async def count_teacher_weekly_periods(academic_year_id: str, teacher_id: str) -> int:
    return await db.timetable_slots.count_documents({
        "entity_id": ENTITY_PWS,
        "academic_year_id": academic_year_id,
        "teacher_id": teacher_id,
        "effective_to": None,
        "status": {"$in": ["DRAFT", "PUBLISHED"]},
    })


async def rank_substitute_candidates(
    *,
    academic_year_id: str,
    slot_id: str,
    date_iso: str,
) -> List[dict]:
    slot = await db.timetable_slots.find_one({"id": slot_id}, {"_id": 0})
    if not slot:
        raise HTTPException(404, "Slot not found")
    period = await db.timetable_periods.find_one({"id": slot["period_id"]}, {"_id": 0})
    teachers = await db.users.find({
        "role": "teacher",
        "organization": {"$in": ["PWS", "BOTH", None]},
        "status": {"$ne": "deactivated"},
    }, {"_id": 0}).to_list(500)

    ranked: List[Tuple[int, dict]] = []
    subject_id = slot.get("subject_id")
    for t in teachers:
        tid = t["id"]
        if tid == slot.get("teacher_id"):
            continue
        avail = await is_teacher_available(tid, date_iso, period_start=period.get("start_time") if period else None)
        if not avail["available"]:
            continue
        booked = await _teacher_booked(academic_year_id, tid, slot["day_of_week"], slot["period_id"])
        if booked:
            continue
        weekly = await count_teacher_weekly_periods(academic_year_id, tid)
        subs_today = await db.timetable_substitutions.count_documents({
            "entity_id": ENTITY_PWS,
            "substitution_date": date_iso[:10],
            "substitute_teacher_id": tid,
            "status": "ACTIVE",
        })
        qualified = False
        if subject_id:
            qualified = bool(await db.teacher_class_assignments.find_one({
                "teacher_user_id": tid,
                "academic_year_id": academic_year_id,
                "subject_id": subject_id,
            }))
        score = 0
        if qualified:
            score += 100
        score += 50  # free in period
        if weekly >= DEFAULT_MAX_WEEKLY_PERIODS:
            score -= 30
        ranked.append((score, {
            "teacher_id": tid,
            "name": t.get("name"),
            "weekly_periods": weekly,
            "substitutions_today": subs_today,
            "qualified_in_subject": qualified,
            "high_load": weekly >= DEFAULT_MAX_WEEKLY_PERIODS,
        }))

    ranked.sort(key=lambda x: (-x[0], x[1]["weekly_periods"], x[1]["name"] or ""))
    return [r[1] for r in ranked]


async def absences_for_date(academic_year_id: str, date_iso: str) -> List[dict]:
    """Teachers absent on date and their affected published slots."""
    wd = datetime.fromisoformat(date_iso[:10]).weekday()
    day_name = day_of_week_for_date(date_iso[:10])
    if not day_name:
        return []

    absent_ids = await db.attendance.distinct("person_id", {
        "kind": "teacher",
        "date": date_iso[:10],
        "status": {"$in": ["absent", "leave", "on_leave"]},
    })
    out: List[dict] = []
    for tid in absent_ids:
        teacher = await db.users.find_one({"id": tid}, {"_id": 0, "name": 1})
        slots = await db.timetable_slots.find({
            "academic_year_id": academic_year_id,
            "teacher_id": tid,
            "day_of_week": day_name,
            "effective_to": None,
            "status": "PUBLISHED",
        }, {"_id": 0}).to_list(50)
        for slot in slots:
            period = await db.timetable_periods.find_one({"id": slot["period_id"]}, {"_id": 0})
            grade = await db.grades.find_one({"id": slot.get("class_id")}, {"_id": 0, "name": 1, "label": 1})
            subj = await db.subjects.find_one({"id": slot.get("subject_id")}, {"_id": 0, "name": 1})
            sub = await db.timetable_substitutions.find_one({
                "slot_id": slot["id"],
                "substitution_date": date_iso[:10],
                "status": "ACTIVE",
            }, {"_id": 0})
            out.append({
                "slot_id": slot["id"],
                "class_label": grade.get("name") if grade else slot.get("class_id"),
                "period_label": period.get("period_label") if period else slot.get("period_id"),
                "start_time": period.get("start_time") if period else None,
                "end_time": period.get("end_time") if period else None,
                "subject_name": subj.get("name") if subj else None,
                "absent_teacher_id": tid,
                "absent_teacher_name": teacher.get("name") if teacher else tid,
                "substitution": sub,
                "status": "substituted" if sub else "pending",
            })
    return out


async def resolve_schedule_for_teacher(
    teacher_id: str,
    academic_year_id: str,
    *,
    date_iso: Optional[str] = None,
    published_only: bool = True,
) -> List[dict]:
    status_filter = ["PUBLISHED"] if published_only else ["DRAFT", "PUBLISHED"]
    slots = await db.timetable_slots.find({
        "entity_id": ENTITY_PWS,
        "academic_year_id": academic_year_id,
        "teacher_id": teacher_id,
        "effective_to": None,
        "status": {"$in": status_filter},
    }, {"_id": 0}).to_list(500)

    date_key = (date_iso or now_utc().strftime("%Y-%m-%d"))[:10]
    day_filter = None
    if date_iso:
        day_filter = day_of_week_for_date(date_key)
        if day_filter is None:
            return []

    rows: List[dict] = []
    for slot in slots:
        if day_filter and slot.get("day_of_week") != day_filter:
            continue
        period = await db.timetable_periods.find_one({"id": slot["period_id"]}, {"_id": 0})
        grade = await db.grades.find_one({"id": slot.get("class_id")}, {"_id": 0})
        section = None
        if slot.get("section_id"):
            section = await db.sections.find_one({"id": slot["section_id"]}, {"_id": 0})
        subj = await db.subjects.find_one({"id": slot.get("subject_id")}, {"_id": 0})
        sub = await db.timetable_substitutions.find_one({
            "slot_id": slot["id"],
            "substitution_date": date_key,
            "status": "ACTIVE",
        }, {"_id": 0})

        row = {
            **slot,
            "period": period,
            "class_name": grade.get("name") if grade else None,
            "section_label": section.get("label") if section else None,
            "subject_name": subj.get("name") if subj else None,
            "is_substitute": False,
            "is_covered": False,
            "substitution": sub,
        }
        if sub and sub.get("substitute_teacher_id") == teacher_id:
            orig = await db.users.find_one({"id": sub.get("original_teacher_id")}, {"_id": 0, "name": 1})
            row["is_substitute"] = True
            row["covering_for"] = orig.get("name") if orig else sub.get("original_teacher_id")
        if sub and sub.get("original_teacher_id") == teacher_id and sub.get("substitute_teacher_id"):
            sub_teacher = await db.users.find_one({"id": sub["substitute_teacher_id"]}, {"_id": 0, "name": 1})
            row["is_covered"] = True
            row["covered_by"] = sub_teacher.get("name") if sub_teacher else sub["substitute_teacher_id"]
        rows.append(row)

    # Substitute assignments where this teacher covers someone else
    subs = await db.timetable_substitutions.find({
        "substitute_teacher_id": teacher_id,
        "substitution_date": date_key,
        "status": "ACTIVE",
    }, {"_id": 0}).to_list(50)
    existing_slot_ids = {r["id"] for r in rows}
    for sub in subs:
        slot = await db.timetable_slots.find_one({"id": sub["slot_id"]}, {"_id": 0})
        if not slot or slot["id"] in existing_slot_ids:
            continue
        if day_filter and slot.get("day_of_week") != day_filter:
            continue
        period = await db.timetable_periods.find_one({"id": slot["period_id"]}, {"_id": 0})
        grade = await db.grades.find_one({"id": slot.get("class_id")}, {"_id": 0})
        subj = await db.subjects.find_one({"id": slot.get("subject_id")}, {"_id": 0})
        orig = await db.users.find_one({"id": sub.get("original_teacher_id")}, {"_id": 0, "name": 1})
        rows.append({
            **slot,
            "period": period,
            "class_name": grade.get("name") if grade else None,
            "subject_name": subj.get("name") if subj else None,
            "is_substitute": True,
            "covering_for": orig.get("name") if orig else sub.get("original_teacher_id"),
            "substitution": sub,
        })

    rows.sort(key=lambda r: (
        DAYS_OF_WEEK.index(r.get("day_of_week", "MON")) if r.get("day_of_week") in DAYS_OF_WEEK else 99,
        _parse_time((r.get("period") or {}).get("start_time", "99:99")),
    ))
    return rows
