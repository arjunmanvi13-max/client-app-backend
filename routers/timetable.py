"""PWS Time Table API."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core import db, get_current_user, now_utc
from notifications_service import send_notification
from timetable.constants import DAYS_OF_WEEK, ENTITY_PWS, DEFAULT_MAX_WEEKLY_PERIODS, day_of_week_for_date
from timetable.permissions import (
    assert_pws_timetable_access,
    assert_timetable_manage,
    can_timetable_create,
    can_timetable_delete,
    can_timetable_edit,
    can_timetable_export,
    can_timetable_publish,
    can_timetable_substitute,
    can_timetable_view_all,
    can_timetable_view_own,
    is_pws_teacher,
)
from timetable.service import (
    absences_for_date,
    assert_year_writable,
    count_teacher_weekly_periods,
    get_year_or_404,
    is_teacher_available,
    rank_substitute_candidates,
    resolve_schedule_for_teacher,
    validate_slot_allocation,
    write_audit,
)

router = APIRouter(prefix="/timetable", tags=["timetable"])


class PeriodIn(BaseModel):
    academic_year_id: str
    schedule_group: Literal["PRE_PRIMARY", "PRIMARY_SECONDARY"]
    day_type: Literal["WEEKDAY", "SATURDAY"]
    period_order: int
    period_label: str
    start_time: str
    end_time: str
    period_type: Literal["TEACHING", "ASSEMBLY", "BREAK", "LUNCH", "HOME_ROOM", "CLUB"]
    is_active: bool = True


class PeriodPatch(BaseModel):
    period_label: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    period_type: Optional[str] = None
    is_active: Optional[bool] = None


class SlotIn(BaseModel):
    academic_year_id: str
    class_id: str
    section_id: Optional[str] = None
    day_of_week: Literal["MON", "TUE", "WED", "THU", "FRI", "SAT"]
    period_id: str
    subject_id: Optional[str] = None
    teacher_id: Optional[str] = None
    room: Optional[str] = None
    notes: Optional[str] = None
    allocation_date: Optional[str] = None


class SlotPatch(BaseModel):
    subject_id: Optional[str] = None
    teacher_id: Optional[str] = None
    room: Optional[str] = None
    notes: Optional[str] = None
    allocation_date: Optional[str] = None


class BulkCopyIn(BaseModel):
    academic_year_id: str
    mode: Literal["copy_day", "copy_class"]
    source_day: Optional[str] = None
    target_day: Optional[str] = None
    source_class_id: Optional[str] = None
    source_section_id: Optional[str] = None
    target_class_id: Optional[str] = None
    target_section_id: Optional[str] = None


class PublishIn(BaseModel):
    academic_year_id: str


class SubstitutionIn(BaseModel):
    slot_id: str
    substitution_date: str
    substitute_teacher_id: Optional[str] = None
    reason: Literal["TEACHER_ABSENT", "ON_LEAVE", "OFFICIAL_DUTY", "MEDICAL", "EXAM_DUTY", "OTHER"] = "TEACHER_ABSENT"
    reason_note: Optional[str] = None


class RevokeSubstitutionIn(BaseModel):
    reason_note: Optional[str] = None


def _actor(user: dict) -> dict:
    return {"id": user.get("id"), "role": user.get("role"), "name": user.get("name")}


def _slot_out(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


@router.get("/periods")
async def list_periods(
    academic_year_id: str,
    schedule_group: Optional[str] = None,
    day_type: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    assert_pws_timetable_access(user)
    q: dict = {"entity_id": ENTITY_PWS, "academic_year_id": academic_year_id, "is_active": True}
    if schedule_group:
        q["schedule_group"] = schedule_group
    if day_type:
        q["day_type"] = day_type
    rows = await db.timetable_periods.find(q, {"_id": 0}).sort("period_order", 1).to_list(200)
    return rows


@router.post("/periods")
async def create_period(payload: PeriodIn, user: dict = Depends(get_current_user)):
    assert_pws_timetable_access(user)
    if not can_timetable_create(user):
        raise HTTPException(403, "timetable.create permission required")
    await assert_year_writable(payload.academic_year_id)
    doc = {
        "id": str(uuid.uuid4()),
        "entity_id": ENTITY_PWS,
        **payload.dict(),
        "created_at": now_utc().isoformat(),
        "updated_at": now_utc().isoformat(),
        "created_by": user["id"],
        "updated_by": user["id"],
    }
    await db.timetable_periods.insert_one(doc)
    return _slot_out(doc)


@router.patch("/periods/{period_id}")
async def patch_period(period_id: str, payload: PeriodPatch, user: dict = Depends(get_current_user)):
    assert_pws_timetable_access(user)
    if not can_timetable_edit(user):
        raise HTTPException(403, "timetable.edit permission required")
    existing = await db.timetable_periods.find_one({"id": period_id, "entity_id": ENTITY_PWS}, {"_id": 0})
    if not existing:
        raise HTTPException(404, "Period not found")
    await assert_year_writable(existing["academic_year_id"])
    patch = {k: v for k, v in payload.dict(exclude_none=True).items()}
    patch["updated_at"] = now_utc().isoformat()
    patch["updated_by"] = user["id"]
    await db.timetable_periods.update_one({"id": period_id}, {"$set": patch})
    return _slot_out({**existing, **patch})


@router.get("/slots")
async def list_slots(
    academic_year_id: str,
    class_id: Optional[str] = None,
    section_id: Optional[str] = None,
    teacher_id: Optional[str] = None,
    day: Optional[str] = None,
    date: Optional[str] = None,
    status: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    assert_pws_timetable_access(user)

    if is_pws_teacher(user) and not can_timetable_view_all(user):
        if teacher_id and teacher_id != user["id"]:
            raise HTTPException(403, "Teachers may only view their own timetable")
        teacher_id = user["id"]
        status = "PUBLISHED"

    q: dict = {
        "entity_id": ENTITY_PWS,
        "academic_year_id": academic_year_id,
        "effective_to": None,
    }
    if class_id:
        q["class_id"] = class_id
    if section_id:
        q["section_id"] = section_id
    if teacher_id:
        q["teacher_id"] = teacher_id
    if day:
        q["day_of_week"] = day.upper()
    if status:
        q["status"] = status
    else:
        q["status"] = {"$in": ["DRAFT", "PUBLISHED"]}

    slots = await db.timetable_slots.find(q, {"_id": 0}).to_list(2000)
    date_key = (date or now_utc().strftime("%Y-%m-%d"))[:10]
    for slot in slots:
        slot["substitution"] = await db.timetable_substitutions.find_one({
            "slot_id": slot["id"],
            "substitution_date": date_key,
            "status": "ACTIVE",
        }, {"_id": 0})
    return slots


@router.post("/slots")
async def create_slot(payload: SlotIn, request: Request, user: dict = Depends(get_current_user)):
    assert_timetable_manage(user)
    await assert_year_writable(payload.academic_year_id)
    await validate_slot_allocation(
        academic_year_id=payload.academic_year_id,
        class_id=payload.class_id,
        section_id=payload.section_id,
        day_of_week=payload.day_of_week,
        period_id=payload.period_id,
        subject_id=payload.subject_id,
        teacher_id=payload.teacher_id,
        date_iso=payload.allocation_date or now_utc().strftime("%Y-%m-%d"),
    )
    doc = {
        "id": str(uuid.uuid4()),
        "entity_id": ENTITY_PWS,
        "academic_year_id": payload.academic_year_id,
        "class_id": payload.class_id,
        "section_id": payload.section_id,
        "day_of_week": payload.day_of_week,
        "period_id": payload.period_id,
        "subject_id": payload.subject_id,
        "teacher_id": payload.teacher_id,
        "room": payload.room,
        "notes": payload.notes,
        "status": "DRAFT",
        "effective_from": now_utc().strftime("%Y-%m-%d"),
        "effective_to": None,
        "created_at": now_utc().isoformat(),
        "updated_at": now_utc().isoformat(),
        "created_by": user["id"],
        "updated_by": user["id"],
    }
    await db.timetable_slots.insert_one(doc)
    await write_audit(
        entity_id=ENTITY_PWS,
        action="CREATE",
        actor=_actor(user),
        slot_id=doc["id"],
        after=doc,
        ip_address=request.client.host if request.client else None,
    )
    return _slot_out(doc)


@router.patch("/slots/{slot_id}")
async def update_slot(slot_id: str, payload: SlotPatch, request: Request, user: dict = Depends(get_current_user)):
    assert_timetable_manage(user)
    if not can_timetable_edit(user):
        raise HTTPException(403, "timetable.edit permission required")
    existing = await db.timetable_slots.find_one({"id": slot_id, "entity_id": ENTITY_PWS}, {"_id": 0})
    if not existing:
        raise HTTPException(404, "Slot not found")
    await assert_year_writable(existing["academic_year_id"])

    merged = {**existing, **payload.dict(exclude_none=True)}
    await validate_slot_allocation(
        academic_year_id=existing["academic_year_id"],
        class_id=existing["class_id"],
        section_id=merged.get("section_id"),
        day_of_week=existing["day_of_week"],
        period_id=existing["period_id"],
        subject_id=merged.get("subject_id"),
        teacher_id=merged.get("teacher_id"),
        date_iso=payload.allocation_date or now_utc().strftime("%Y-%m-%d"),
        exclude_slot_id=slot_id,
    )
    patch = {k: v for k, v in payload.dict(exclude_none=True).items() if k != "allocation_date"}
    patch["updated_at"] = now_utc().isoformat()
    patch["updated_by"] = user["id"]
    await db.timetable_slots.update_one({"id": slot_id}, {"$set": patch})
    updated = {**existing, **patch}
    await write_audit(
        entity_id=ENTITY_PWS,
        action="UPDATE",
        actor=_actor(user),
        slot_id=slot_id,
        before=existing,
        after=updated,
        ip_address=request.client.host if request.client else None,
    )
    return _slot_out(updated)


@router.delete("/slots/{slot_id}")
async def delete_slot(slot_id: str, request: Request, user: dict = Depends(get_current_user)):
    assert_timetable_manage(user)
    if not can_timetable_delete(user):
        raise HTTPException(403, "timetable.delete permission required")
    existing = await db.timetable_slots.find_one({"id": slot_id, "entity_id": ENTITY_PWS}, {"_id": 0})
    if not existing:
        raise HTTPException(404, "Slot not found")
    await assert_year_writable(existing["academic_year_id"])
    await db.timetable_slots.update_one({"id": slot_id}, {"$set": {
        "effective_to": now_utc().strftime("%Y-%m-%d"),
        "updated_at": now_utc().isoformat(),
        "updated_by": user["id"],
    }})
    await write_audit(
        entity_id=ENTITY_PWS,
        action="DELETE",
        actor=_actor(user),
        slot_id=slot_id,
        before=existing,
        ip_address=request.client.host if request.client else None,
    )
    return {"deleted": True}


@router.post("/slots/bulk")
async def bulk_copy(payload: BulkCopyIn, user: dict = Depends(get_current_user)):
    assert_timetable_manage(user)
    await assert_year_writable(payload.academic_year_id)
    created = 0
    if payload.mode == "copy_day" and payload.source_day and payload.target_day:
        source_slots = await db.timetable_slots.find({
            "entity_id": ENTITY_PWS,
            "academic_year_id": payload.academic_year_id,
            "day_of_week": payload.source_day,
            "effective_to": None,
            "status": {"$in": ["DRAFT", "PUBLISHED"]},
        }, {"_id": 0}).to_list(500)
        for s in source_slots:
            exists = await db.timetable_slots.find_one({
                "entity_id": ENTITY_PWS,
                "academic_year_id": payload.academic_year_id,
                "class_id": s["class_id"],
                "section_id": s.get("section_id"),
                "day_of_week": payload.target_day,
                "period_id": s["period_id"],
                "effective_to": None,
            })
            if exists:
                continue
            doc = {**s, "id": str(uuid.uuid4()), "day_of_week": payload.target_day, "status": "DRAFT",
                   "created_at": now_utc().isoformat(), "updated_at": now_utc().isoformat(),
                   "created_by": user["id"], "updated_by": user["id"]}
            await db.timetable_slots.insert_one(doc)
            created += 1
    elif payload.mode == "copy_class" and payload.source_class_id and payload.target_class_id:
        q = {
            "entity_id": ENTITY_PWS,
            "academic_year_id": payload.academic_year_id,
            "class_id": payload.source_class_id,
            "effective_to": None,
        }
        if payload.source_section_id:
            q["section_id"] = payload.source_section_id
        source_slots = await db.timetable_slots.find(q, {"_id": 0}).to_list(500)
        for s in source_slots:
            exists = await db.timetable_slots.find_one({
                "entity_id": ENTITY_PWS,
                "academic_year_id": payload.academic_year_id,
                "class_id": payload.target_class_id,
                "section_id": payload.target_section_id,
                "day_of_week": s["day_of_week"],
                "period_id": s["period_id"],
                "effective_to": None,
            })
            if exists:
                continue
            doc = {
                **s,
                "id": str(uuid.uuid4()),
                "class_id": payload.target_class_id,
                "section_id": payload.target_section_id,
                "status": "DRAFT",
                "created_at": now_utc().isoformat(),
                "updated_at": now_utc().isoformat(),
                "created_by": user["id"],
                "updated_by": user["id"],
            }
            await db.timetable_slots.insert_one(doc)
            created += 1
    else:
        raise HTTPException(400, "Invalid bulk copy parameters")
    return {"created": created}


@router.post("/publish")
async def publish_timetable(payload: PublishIn, request: Request, user: dict = Depends(get_current_user)):
    assert_pws_timetable_access(user)
    if not can_timetable_publish(user):
        raise HTTPException(403, "timetable.publish permission required")
    await assert_year_writable(payload.academic_year_id)
    result = await db.timetable_slots.update_many({
        "entity_id": ENTITY_PWS,
        "academic_year_id": payload.academic_year_id,
        "status": "DRAFT",
        "effective_to": None,
    }, {"$set": {"status": "PUBLISHED", "updated_at": now_utc().isoformat(), "updated_by": user["id"]}})
    await write_audit(
        entity_id=ENTITY_PWS,
        action="PUBLISH",
        actor=_actor(user),
        after={"published_count": result.modified_count},
        ip_address=request.client.host if request.client else None,
    )
    return {"published": result.modified_count}


@router.get("/availability")
async def teacher_availability(
    date: str,
    period_id: str,
    academic_year_id: str,
    user: dict = Depends(get_current_user),
):
    assert_pws_timetable_access(user)
    period = await db.timetable_periods.find_one({"id": period_id}, {"_id": 0})
    if not period:
        raise HTTPException(404, "Period not found")
    date_key = (date or now_utc().strftime("%Y-%m-%d"))[:10]
    day_name = day_of_week_for_date(date_key)
    if not day_name:
        return {"available": [], "unavailable": []}
    teachers = await db.users.find({"role": "teacher", "organization": {"$in": ["PWS", "BOTH", None]}}, {"_id": 0}).to_list(500)
    available, unavailable = [], []
    for t in teachers:
        avail = await is_teacher_available(t["id"], date, period_start=period.get("start_time"))
        row = {"teacher_id": t["id"], "name": t.get("name"), **avail}
        if avail["available"]:
            booked = await db.timetable_slots.find_one({
                "entity_id": ENTITY_PWS,
                "academic_year_id": academic_year_id,
                "teacher_id": t["id"],
                "day_of_week": day_name,
                "period_id": period_id,
                "effective_to": None,
                "status": {"$in": ["DRAFT", "PUBLISHED"]},
            })
            if booked:
                row["available"] = False
                row["reason"] = "Already booked in this period"
                unavailable.append(row)
            else:
                available.append(row)
        else:
            unavailable.append(row)
    return {"available": available, "unavailable": unavailable}


@router.get("/absences")
async def list_absences(date: str, academic_year_id: str, user: dict = Depends(get_current_user)):
    assert_pws_timetable_access(user)
    if not can_timetable_view_all(user):
        raise HTTPException(403, "timetable.view_all required")
    return await absences_for_date(academic_year_id, date)


@router.get("/substitutes")
async def list_substitutes(slot_id: str, date: str, academic_year_id: str, user: dict = Depends(get_current_user)):
    assert_pws_timetable_access(user)
    if not can_timetable_substitute(user):
        raise HTTPException(403, "timetable.substitute permission required")
    return await rank_substitute_candidates(academic_year_id=academic_year_id, slot_id=slot_id, date_iso=date)


@router.post("/substitutions")
async def create_substitution(payload: SubstitutionIn, request: Request, user: dict = Depends(get_current_user)):
    assert_pws_timetable_access(user)
    if not can_timetable_substitute(user):
        raise HTTPException(403, "timetable.substitute permission required")
    slot = await db.timetable_slots.find_one({"id": payload.slot_id, "entity_id": ENTITY_PWS}, {"_id": 0})
    if not slot:
        raise HTTPException(404, "Slot not found")
    await assert_year_writable(slot["academic_year_id"])

    existing = await db.timetable_substitutions.find_one({
        "slot_id": payload.slot_id,
        "substitution_date": payload.substitution_date[:10],
        "status": "ACTIVE",
    })
    if existing:
        raise HTTPException(400, "Active substitution already exists for this slot and date")

    if payload.substitute_teacher_id:
        period = await db.timetable_periods.find_one({"id": slot["period_id"]}, {"_id": 0})
        avail = await is_teacher_available(
            payload.substitute_teacher_id,
            payload.substitution_date,
            period_start=period.get("start_time") if period else None,
        )
        if not avail["available"]:
            raise HTTPException(422, f"Substitute unavailable: {avail.get('reason')}")

    doc = {
        "id": str(uuid.uuid4()),
        "entity_id": ENTITY_PWS,
        "slot_id": payload.slot_id,
        "substitution_date": payload.substitution_date[:10],
        "original_teacher_id": slot.get("teacher_id"),
        "substitute_teacher_id": payload.substitute_teacher_id,
        "reason": payload.reason,
        "reason_note": payload.reason_note,
        "status": "ACTIVE",
        "created_by": user["id"],
        "created_at": now_utc().isoformat(),
    }
    await db.timetable_substitutions.insert_one(doc)
    await write_audit(
        entity_id=ENTITY_PWS,
        action="SUBSTITUTE",
        actor=_actor(user),
        slot_id=payload.slot_id,
        after=doc,
        ip_address=request.client.host if request.client else None,
    )

    if payload.substitute_teacher_id:
        await send_notification(
            payload.substitute_teacher_id,
            ntype="task_assigned",
            title="Substitution assigned",
            message=f"You are covering a class on {payload.substitution_date[:10]}",
            ref_id=doc["id"],
            ref_type="timetable_substitution",
            entity_id=ENTITY_PWS,
        )
    if slot.get("teacher_id"):
        await send_notification(
            slot["teacher_id"],
            ntype="task_assigned",
            title="Class covered",
            message=f"Your period on {payload.substitution_date[:10]} has a substitute",
            ref_id=doc["id"],
            ref_type="timetable_substitution",
            entity_id=ENTITY_PWS,
        )
    return doc


@router.patch("/substitutions/{sub_id}/revoke")
async def revoke_substitution(sub_id: str, payload: RevokeSubstitutionIn, request: Request, user: dict = Depends(get_current_user)):
    assert_pws_timetable_access(user)
    if not can_timetable_substitute(user):
        raise HTTPException(403, "timetable.substitute permission required")
    sub = await db.timetable_substitutions.find_one({"id": sub_id, "entity_id": ENTITY_PWS}, {"_id": 0})
    if not sub:
        raise HTTPException(404, "Substitution not found")
    await db.timetable_substitutions.update_one({"id": sub_id}, {"$set": {"status": "REVOKED"}})
    await write_audit(
        entity_id=ENTITY_PWS,
        action="REVOKE_SUBSTITUTION",
        actor=_actor(user),
        slot_id=sub.get("slot_id"),
        before=sub,
        after={"status": "REVOKED", "reason_note": payload.reason_note},
        ip_address=request.client.host if request.client else None,
    )
    return {"revoked": True}


@router.get("/my-schedule")
async def my_schedule(date: Optional[str] = None, academic_year_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    assert_pws_timetable_access(user)
    if not can_timetable_view_own(user):
        raise HTTPException(403, "timetable.view_own required")
    if not is_pws_teacher(user) and not can_timetable_view_all(user):
        raise HTTPException(403, "Teacher access only")
    if can_timetable_view_all(user) and not is_pws_teacher(user):
        raise HTTPException(403, "Use /slots for admin views")
    year_id = academic_year_id
    if not year_id:
        year = await db.academic_years.find_one({"entity_id": ENTITY_PWS, "status": "open"}, {"_id": 0})
        year_id = year["id"] if year else None
    if not year_id:
        return []
    date_key = (date or now_utc().strftime("%Y-%m-%d"))[:10]
    day_name = day_of_week_for_date(date_key)
    if not day_name:
        return {"date": date_key, "periods": [], "duties": [], "note": "No timetable on Sunday"}
    schedule = await resolve_schedule_for_teacher(user["id"], year_id, date_iso=date_key, published_only=True)
    duties = await db.timetable_duty_roster.find({
        "entity_id": ENTITY_PWS,
        "academic_year_id": year_id,
        "teacher_id": user["id"],
        "day_of_week": day_name,
    }, {"_id": 0}).to_list(20)
    return {"date": date_key, "periods": schedule, "duties": duties}


@router.get("/my-week")
async def my_week(academic_year_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    assert_pws_timetable_access(user)
    if not can_timetable_view_own(user):
        raise HTTPException(403, "timetable.view_own required")
    year_id = academic_year_id
    if not year_id:
        year = await db.academic_years.find_one({"entity_id": ENTITY_PWS, "status": "open"}, {"_id": 0})
        year_id = year["id"] if year else None
    if not year_id:
        return []
    schedule = await resolve_schedule_for_teacher(user["id"], year_id, published_only=True)
    return {"periods": schedule}


@router.get("/conflicts")
async def list_conflicts(academic_year_id: str, user: dict = Depends(get_current_user)):
    assert_pws_timetable_access(user)
    if not can_timetable_view_all(user):
        raise HTTPException(403, "timetable.view_all required")
    slots = await db.timetable_slots.find({
        "entity_id": ENTITY_PWS,
        "academic_year_id": academic_year_id,
        "effective_to": None,
        "status": {"$in": ["DRAFT", "PUBLISHED"]},
    }, {"_id": 0}).to_list(5000)
    conflicts = []
    seen = set()
    for s in slots:
        if not s.get("teacher_id"):
            continue
        key = (s["teacher_id"], s["day_of_week"], s["period_id"])
        if key in seen:
            conflicts.append({"type": "teacher_double_booking", "slot_id": s["id"], **s})
        seen.add(key)
    return {"conflicts": conflicts}


@router.get("/teacher-load")
async def teacher_load(academic_year_id: str, user: dict = Depends(get_current_user)):
    assert_pws_timetable_access(user)
    if not can_timetable_view_all(user):
        raise HTTPException(403, "timetable.view_all required")
    teachers = await db.users.find({"role": "teacher"}, {"_id": 0, "id": 1, "name": 1}).to_list(500)
    rows = []
    for t in teachers:
        count = await count_teacher_weekly_periods(academic_year_id, t["id"])
        rows.append({
            "teacher_id": t["id"],
            "name": t.get("name"),
            "weekly_periods": count,
            "over_limit": count > DEFAULT_MAX_WEEKLY_PERIODS,
        })
    rows.sort(key=lambda r: -r["weekly_periods"])
    return rows


@router.get("/meta")
async def timetable_meta(user: dict = Depends(get_current_user)):
    assert_pws_timetable_access(user)
    years = await db.academic_years.find({"entity_id": ENTITY_PWS}, {"_id": 0}).sort("start_date", -1).to_list(20)
    open_year = next((y for y in years if y.get("status") == "open"), years[0] if years else None)
    draft_count = 0
    if open_year:
        draft_count = await db.timetable_slots.count_documents({
            "entity_id": ENTITY_PWS,
            "academic_year_id": open_year["id"],
            "status": "DRAFT",
            "effective_to": None,
        })
    return {
        "permissions": {
            "view_all": can_timetable_view_all(user),
            "view_own": can_timetable_view_own(user),
            "create": can_timetable_create(user),
            "edit": can_timetable_edit(user),
            "delete": can_timetable_delete(user),
            "substitute": can_timetable_substitute(user),
            "publish": can_timetable_publish(user),
            "export": can_timetable_export(user),
        },
        "years": years,
        "open_year_id": open_year["id"] if open_year else None,
        "draft_count": draft_count,
        "days": list(DAYS_OF_WEEK),
    }
