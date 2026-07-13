"""Attendance MVP — unified records with entity, session, marker, timestamp, audit."""
import csv
import io
import uuid
from typing import Optional, List, Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from core import (
    db, AttendanceBatch, AttendanceCorrectionIn, get_current_user, now_utc, is_admin,
    assert_perm, get_perm, resolve_user_institution, attendance_entity_filter,
    attendance_entity_for_kind,
)

router = APIRouter(prefix="/attendance", tags=["attendance"])

_MARK_PERM_BY_KIND = {
    "student": "mark_student_attendance",
    "player": "mark_player_attendance",
    "staff": "mark_staff_attendance",
    "coach": "mark_coach_attendance",
    "teacher": "mark_student_attendance",
    "hostel": "mark_hostel_attendance",
}

_DEFAULT_SESSION = {
    "student": "morning",
    "teacher": "morning",
    "staff": "morning",
    "coach": "morning",
    "player": "morning",
    "hostel": "evening",
}


def normalize_session(session: Optional[str], slot: Optional[str] = None, kind: Optional[str] = None) -> str:
    if session:
        s = session.strip().lower()
        if s == "night":
            return "evening"
        return s
    if slot:
        return slot.strip().lower()
    return _DEFAULT_SESSION.get(kind or "", "morning")


def _dedup_query(kind: str, person_id: str, date: str, session: str) -> dict:
    return {"kind": kind, "person_id": person_id, "date": date, "session": session}


async def log_attendance_audit(
    before: Optional[dict],
    after: dict,
    user: dict,
    *,
    reason: Optional[str] = None,
    action: str = "update",
) -> None:
    await db.attendance_audit.insert_one({
        "id": str(uuid.uuid4()),
        "attendance_id": after.get("id") or (before or {}).get("id"),
        "person_id": after.get("person_id"),
        "date": after.get("date"),
        "session": after.get("session"),
        "kind": after.get("kind"),
        "before_status": (before or {}).get("status"),
        "after_status": after.get("status"),
        "reason": reason,
        "action": action,
        "changed_by": user["id"],
        "changed_by_name": user.get("name"),
        "changed_at": now_utc().isoformat(),
    })


async def upsert_attendance(
    user: dict,
    *,
    kind: str,
    person_id: str,
    date: str,
    status: str,
    session: Optional[str] = None,
    slot: Optional[str] = None,
    entity_id: Optional[str] = None,
    group: Optional[str] = None,
    section_id: Optional[str] = None,
    sport: Optional[str] = None,
    centre: Optional[str] = None,
    organization: Optional[str] = None,
    source: str = "manual",
    extra: Optional[dict] = None,
) -> dict:
    """Upsert one attendance row; dedup on person + date + session + kind."""
    sess = normalize_session(session, slot=slot, kind=kind)
    filt = _dedup_query(kind, person_id, date, sess)
    existing = await db.attendance.find_one(filt, {"_id": 0})
    marked_at = now_utc().isoformat()
    ent = entity_id or attendance_entity_for_kind(kind)
    rec = {
        "id": (existing or {}).get("id") or str(uuid.uuid4()),
        "entity_id": ent,
        "person_id": person_id,
        "date": date,
        "session": sess,
        "kind": kind,
        "status": status,
        "marked_by": user["id"],
        "marked_by_name": user.get("name"),
        "marked_at": marked_at,
        "source": source,
        "updated_at": marked_at,
        "group": group,
        "section_id": section_id,
        "sport": sport,
        "centre": centre,
        "organization": organization,
    }
    if slot:
        rec["slot"] = slot
    if extra:
        rec.update(extra)
    if existing:
        if existing.get("status") != status:
            await log_attendance_audit(existing, rec, user, action="remark")
        rec["created_at"] = existing.get("created_at") or marked_at
    else:
        rec["created_at"] = marked_at
    await db.attendance.update_one(filt, {"$set": rec}, upsert=True)
    return rec


def _can_view_attendance(user: dict) -> bool:
    from rbac.guards import can_view_attendance
    return can_view_attendance(user)


def _can_correct_attendance(user: dict) -> bool:
    from rbac.guards import can_correct_attendance
    return can_correct_attendance(user)


# -------- Staff Attendance (default-present workflow) --------
def _can_mark_pws_staff(user: dict) -> bool:
    from rbac.guards import can_mark_pws_attendance
    return can_mark_pws_attendance(user)


def _can_mark_alpha_staff(user: dict, centre: Optional[str]) -> bool:
    from rbac.guards import can_mark_alpha_attendance
    if can_mark_alpha_attendance(user) and user.get("role") != "coach":
        return True
    if user.get("role") == "coach" and user.get("coach_type") == "head":
        if centre is None:
            return True
        return centre in (user.get("assigned_centres") or [])
    return False


async def _staff_query_for_user(user: dict, centre: Optional[str] = None, organization: Optional[str] = None) -> dict:
    q: dict = {"kind": "staff"}
    if is_admin(user):
        if user.get("role") == "admin":
            q["organization"] = "ALPHA"
        elif organization:
            q["organization"] = organization
        if centre:
            q["centre"] = centre
        return q
    role = user.get("role")
    if role in ("principal", "vice_principal"):
        q["organization"] = "PWS"
        return q
    if role == "coach" and user.get("coach_type") == "head":
        q["organization"] = "ALPHA"
        centres = user.get("assigned_centres") or []
        if centre:
            if centres and centre not in centres:
                raise HTTPException(403, "Centre not in your assigned centres")
            q["centre"] = centre
        elif centres:
            q["centre"] = {"$in": centres}
        return q
    raise HTTPException(403, "Not allowed to view staff")


class StaffAttendanceIn(BaseModel):
    date: str
    organization: Optional[Literal["PWS", "ALPHA"]] = None
    centre: Optional[Literal["Balua", "Harding Park"]] = None
    absent_staff_ids: List[str] = []
    session: Optional[str] = "morning"


@router.get("/staff-list")
async def staff_list(
    organization: Optional[str] = None,
    centre: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    q = await _staff_query_for_user(user, centre=centre, organization=organization)
    return await db.people.find(q, {"_id": 0}).sort("name", 1).to_list(500)


@router.post("/staff")
async def mark_staff_attendance(payload: StaffAttendanceIn, user: dict = Depends(get_current_user)):
    org = payload.organization
    role = user.get("role")
    if not is_admin(user):
        if role in ("principal", "vice_principal"):
            org = "PWS"
        elif role == "coach" and user.get("coach_type") == "head":
            org = "ALPHA"
        else:
            raise HTTPException(403, "Not allowed to mark staff attendance")
    if org == "ALPHA" and not _can_mark_alpha_staff(user, payload.centre):
        raise HTTPException(403, "Head coach role required for ALPHA staff attendance")
    if org == "PWS" and not _can_mark_pws_staff(user):
        raise HTTPException(403, "Principal / Vice Principal required for PWS staff")
    if not org:
        raise HTTPException(400, "organization required (PWS/ALPHA)")

    q = await _staff_query_for_user(user, centre=payload.centre, organization=org)
    staff_list_docs = await db.people.find(q, {"_id": 0}).to_list(500)
    if not staff_list_docs:
        raise HTTPException(400, "No staff found in scope")
    absent_set = set(payload.absent_staff_ids or [])
    records = []
    for s in staff_list_docs:
        status = "absent" if s["id"] in absent_set else "present"
        rec = await upsert_attendance(
            user,
            kind="staff",
            person_id=s["id"],
            date=payload.date,
            status=status,
            session=payload.session,
            entity_id="pws" if org == "PWS" else "alpha",
            organization=s.get("organization"),
            centre=s.get("centre"),
            source="staff_default_present",
        )
        records.append(rec)
    return {
        "count": len(records),
        "present": len(staff_list_docs) - len(absent_set),
        "absent": len(absent_set),
        "organization": org,
        "centre": payload.centre,
    }


@router.get("/staff")
async def list_staff_attendance(
    date: Optional[str] = None,
    organization: Optional[str] = None,
    centre: Optional[str] = None,
    session: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    await _staff_query_for_user(user, centre=centre, organization=organization)
    q: dict = {"kind": "staff"}
    if date:
        q["date"] = date
    if organization:
        q["organization"] = organization
    if centre:
        q["centre"] = centre
    if session:
        q["session"] = normalize_session(session, kind="staff")
    return await db.attendance.find(q, {"_id": 0}).sort("date", -1).to_list(2000)


# -------- Coach Attendance (default-present workflow) --------
def _can_mark_coach_attendance(user: dict) -> bool:
    if is_admin(user):
        return True
    if user.get("role") == "coach" and user.get("coach_type") == "head":
        return True
    return False


async def _coach_scope_filter(user: dict) -> dict:
    base: dict = {"role": "coach", "status": {"$ne": "deactivated"}}
    if user.get("role") == "super_admin":
        return base
    if user.get("role") == "admin":
        return base
    if user.get("role") == "coach" and user.get("coach_type") == "head":
        centres = user.get("assigned_centres") or []
        clauses: list = [{"id": user["id"]}]
        asst_clause: dict = {"coach_type": "assistant"}
        if centres:
            asst_clause["assigned_centres"] = {"$in": centres}
        clauses.append(asst_clause)
        base["$or"] = clauses
        return base
    if user.get("role") == "coach" and user.get("coach_type") == "assistant":
        base["id"] = user["id"]
        return base
    return {"_block": True}


@router.get("/coaches-list")
async def coaches_list(user: dict = Depends(get_current_user)):
    if not (_can_mark_coach_attendance(user) or user.get("role") == "coach"):
        raise HTTPException(403, "Not allowed to view coach list")
    q = await _coach_scope_filter(user)
    if q.get("_block"):
        raise HTTPException(403, "Not allowed")
    coaches = await db.users.find(q, {"_id": 0, "password_hash": 0}).sort("name", 1).to_list(500)
    return [{
        "id": c["id"],
        "name": c["name"],
        "email": c["email"],
        "coach_type": c.get("coach_type"),
        "assigned_centres": c.get("assigned_centres", []),
        "assigned_sports": c.get("assigned_sports", []),
    } for c in coaches]


class CoachAttendanceIn(BaseModel):
    date: str
    absent_coach_ids: List[str] = []
    session: Optional[str] = "morning"


@router.post("/coaches")
async def mark_coach_attendance(payload: CoachAttendanceIn, user: dict = Depends(get_current_user)):
    if not _can_mark_coach_attendance(user):
        raise HTTPException(403, "Sports Admin / Super Admin / Head Coach required")
    q = await _coach_scope_filter(user)
    if q.get("_block"):
        raise HTTPException(403, "Not allowed")
    coaches = await db.users.find(q, {"_id": 0, "password_hash": 0}).to_list(500)
    if not coaches:
        raise HTTPException(400, "No coaches found")
    absent_set = set(payload.absent_coach_ids or [])
    records = []
    for c in coaches:
        status = "absent" if c["id"] in absent_set else "present"
        rec = await upsert_attendance(
            user,
            kind="coach",
            person_id=c["id"],
            date=payload.date,
            status=status,
            session=payload.session,
            entity_id="alpha",
            source="coach_default_present",
            extra={
                "user_id": c["id"],
                "name": c["name"],
                "coach_type": c.get("coach_type"),
            },
        )
        records.append(rec)
    return {
        "count": len(records),
        "present": len(coaches) - len(absent_set),
        "absent": len(absent_set),
    }


@router.get("/coaches")
async def list_coach_attendance(
    date: Optional[str] = None,
    session: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    if not (_can_mark_coach_attendance(user) or user.get("role") == "coach"):
        raise HTTPException(403, "Not allowed")
    q: dict = {"kind": "coach"}
    if date:
        q["date"] = date
    if session:
        q["session"] = normalize_session(session, kind="coach")
    return await db.attendance.find(q, {"_id": 0}).sort("date", -1).to_list(2000)


async def _validate_batch_marks(
    user: dict,
    *,
    kind: str,
    marks: list,
    section_id: Optional[str] = None,
    group: Optional[str] = None,
) -> None:
    """Ensure each person_id in a batch belongs to the declared section/roster scope."""
    if not marks:
        return
    person_ids = [m.person_id for m in marks]

    if kind == "student":
        allowed_ids: Optional[set] = None
        if section_id:
            allowed_ids = set(await db.people.distinct(
                "id",
                {"kind": "student", "section_id": section_id, "status": {"$ne": "deactivated"}},
            ))
        elif group:
            allowed_ids = set(await db.people.distinct(
                "id",
                {"kind": "student", "group": group, "status": {"$ne": "deactivated"}},
            ))
        elif user.get("role") == "teacher" and not is_admin(user):
            from routers.academic import assigned_section_ids_for_teacher
            assigned = await assigned_section_ids_for_teacher(user["id"])
            if not assigned:
                raise HTTPException(403, "No class assignments")
            allowed_ids = set(await db.people.distinct(
                "id",
                {"kind": "student", "section_id": {"$in": assigned}, "status": {"$ne": "deactivated"}},
            ))
        for pid in person_ids:
            person = await db.people.find_one(
                {"id": pid, "kind": "student"},
                {"_id": 0, "id": 1, "section_id": 1},
            )
            if not person:
                raise HTTPException(404, f"Student not found: {pid}")
            if allowed_ids is not None and pid not in allowed_ids:
                raise HTTPException(403, "Student is not in the selected section or group")

    elif kind == "player" and not is_admin(user):
        from routers.coach import _coach_visibility_filter
        roster_ids = set(await db.people.distinct("id", _coach_visibility_filter(user)))
        for pid in person_ids:
            if pid not in roster_ids:
                raise HTTPException(403, "Player is not in your assigned roster")


@router.post("/batch")
async def mark_attendance_batch(payload: AttendanceBatch, user: dict = Depends(get_current_user)):
    perm = _MARK_PERM_BY_KIND.get(payload.kind)
    if not perm:
        raise HTTPException(400, "Invalid attendance kind")
    if not is_admin(user):
        assert_perm(user, perm)
    group = payload.group
    section_id = payload.section_id
    if payload.kind == "student" and section_id:
        from routers.academic import resolve_section_group, assert_teacher_section_access
        await assert_teacher_section_access(user, section_id)
        _, group = await resolve_section_group(section_id)
    if payload.kind == "student" and not group:
        raise HTTPException(400, "Section or group is required for student attendance")
    await _validate_batch_marks(
        user,
        kind=payload.kind,
        marks=payload.marks,
        section_id=section_id,
        group=group,
    )
    from routers.parents import push_parent_notification
    records = []
    today_str = now_utc().strftime("%Y-%m-%d")
    sess = normalize_session(payload.session, kind=payload.kind)
    for m in payload.marks:
        rec = await upsert_attendance(
            user,
            kind=payload.kind,
            person_id=m.person_id,
            date=payload.date,
            status=m.status,
            session=sess,
            group=group,
            section_id=section_id,
            sport=payload.sport,
            source="batch",
        )
        records.append(rec)
        if payload.date == today_str and payload.kind in ("student", "player"):
            person = await db.people.find_one({"id": m.person_id}, {"_id": 0, "name": 1})
            pname = (person or {}).get("name", "Ward")
            try:
                if m.status == "absent":
                    await push_parent_notification(
                        m.person_id,
                        title="Absence recorded",
                        body=f"{pname} was marked absent on {today_str}.",
                        ntype="absence",
                        ref_id=m.person_id,
                    )
                elif m.status in ("present", "late"):
                    await push_parent_notification(
                        m.person_id,
                        title="Attendance marked",
                        body=f"{pname} was marked {m.status} on {today_str}.",
                        ntype="attendance_marked",
                        ref_id=m.person_id,
                    )
            except Exception:
                pass
    return {"count": len(records), "records": records}


@router.get("")
async def list_attendance(
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    kind: Optional[str] = None,
    group: Optional[str] = None,
    sport: Optional[str] = None,
    session: Optional[str] = None,
    section_id: Optional[str] = None,
    person_id: Optional[str] = None,
    institution: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    if kind and not is_admin(user):
        perm = _MARK_PERM_BY_KIND.get(kind)
        if perm:
            assert_perm(user, perm)
    elif not _can_view_attendance(user) and user.get("role") not in ("teacher", "coach", "warden"):
        if kind:
            perm = _MARK_PERM_BY_KIND.get(kind)
            if perm:
                assert_perm(user, perm)

    q: dict = {}
    if date:
        q["date"] = date
    elif start_date or end_date:
        dr: dict = {}
        if start_date:
            dr["$gte"] = start_date
        if end_date:
            dr["$lte"] = end_date
        q["date"] = dr
    if kind:
        q["kind"] = kind
    if group:
        q["group"] = group
    if sport:
        q["sport"] = sport
    if session:
        q["session"] = normalize_session(session, kind=kind)
    if section_id:
        q["section_id"] = section_id
    if person_id:
        q["person_id"] = person_id

    if user.get("role") == "coach" and (kind == "player" or not kind):
        roster_ids = await _coach_player_roster_ids(user)
        q = _apply_coach_attendance_scope(user, q, sport)
        person_clause = {"person_id": {"$in": roster_ids}}
        q = {"$and": [q, person_clause]} if q else person_clause
        if person_id and person_id not in roster_ids:
            return []

    if user.get("role") == "teacher":
        from routers.academic import assigned_section_ids_for_teacher
        assigned = await assigned_section_ids_for_teacher(user["id"])
        if not assigned:
            return []
        if kind == "student" or not kind:
            sec_filter = {"$or": [{"section_id": {"$in": assigned}}, {"group": {"$in": []}}]}
            sections = await db.sections.find({"id": {"$in": assigned}}, {"label": 1, "_id": 0}).to_list(50)
            labels = [s["label"] for s in sections]
            sec_filter = {"$or": [{"section_id": {"$in": assigned}}, {"group": {"$in": labels}}]}
            q = {"$and": [q, sec_filter]} if q else sec_filter

    inst = resolve_user_institution(user, institution)
    ent_filt = attendance_entity_filter(inst)
    if ent_filt:
        q = {"$and": [q, ent_filt]} if q else ent_filt
    return await db.attendance.find(q, {"_id": 0}).sort("date", -1).to_list(2000)


def _pct(counts: dict) -> float:
    total = sum(counts.values())
    if not total:
        return 0.0
    present = counts.get("present", 0) + counts.get("late", 0)
    return round(100.0 * present / total, 1)


async def _coach_player_roster_ids(user: dict) -> List[str]:
    if is_admin(user) or user.get("role") != "coach":
        return []
    from routers.coach import _coach_visibility_filter
    from coach_scope import assert_coach_sport_assigned
    try:
        assert_coach_sport_assigned(user)
    except ValueError as e:
        raise HTTPException(403, str(e)) from e
    return await db.people.distinct("id", _coach_visibility_filter(user))


def _apply_coach_attendance_scope(user: dict, q: dict, sport: Optional[str]) -> dict:
    if user.get("role") != "coach":
        return q
    from coach_scope import validate_coach_sport_param, ERR_SPORT_ACCESS
    try:
        effective = validate_coach_sport_param(user, sport, is_admin_fn=is_admin)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(403, str(e)) from e
    if effective:
        q["sport"] = effective
    return q


@router.get("/summary")
async def attendance_summary(
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    kind: Optional[str] = None,
    group: Optional[str] = None,
    sport: Optional[str] = None,
    session: Optional[str] = None,
    section_id: Optional[str] = None,
    institution: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    if not _can_view_attendance(user) and not is_admin(user):
        if kind:
            perm = _MARK_PERM_BY_KIND.get(kind)
            if perm:
                assert_perm(user, perm)
        else:
            raise HTTPException(403, "view_attendance permission required")

    today = now_utc().strftime("%Y-%m-%d")
    if date:
        start_date = end_date = date
    if not start_date and not end_date:
        start_date = end_date = today

    match: dict = {"date": {"$gte": start_date, "$lte": end_date}}
    if kind:
        match["kind"] = kind
    if group:
        match["group"] = group
    if sport:
        match["sport"] = sport
    if session:
        match["session"] = normalize_session(session, kind=kind)
    if section_id:
        match["section_id"] = section_id

    if user.get("role") == "coach":
        roster_ids = await _coach_player_roster_ids(user)
        match = _apply_coach_attendance_scope(user, match, sport)
        person_clause = {"person_id": {"$in": roster_ids}}
        match = {"$and": [match, person_clause]} if match else person_clause

    inst = resolve_user_institution(user, institution)
    ent_filt = attendance_entity_filter(inst)
    if ent_filt:
        match = {"$and": [match, ent_filt]}

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {"kind": "$kind", "status": "$status", "group": "$group", "sport": "$sport"},
            "count": {"$sum": 1},
        }},
    ]
    rows = await db.attendance.aggregate(pipeline).to_list(500)

    by_kind: dict = {}
    by_group: dict = {}
    by_sport: dict = {}
    totals = {"present": 0, "absent": 0, "late": 0, "leave": 0}

    for r in rows:
        kid = r["_id"]["kind"]
        st = r["_id"]["status"]
        grp = r["_id"].get("group") or "—"
        sp = r["_id"].get("sport") or "—"
        cnt = r["count"]
        by_kind.setdefault(kid, {"present": 0, "absent": 0, "late": 0, "leave": 0})
        by_kind[kid][st] = by_kind[kid].get(st, 0) + cnt
        if grp != "—":
            by_group.setdefault(grp, {"present": 0, "absent": 0, "late": 0, "leave": 0})
            by_group[grp][st] = by_group[grp].get(st, 0) + cnt
        if sp != "—":
            by_sport.setdefault(sp, {"present": 0, "absent": 0, "late": 0, "leave": 0})
            by_sport[sp][st] = by_sport[sp].get(st, 0) + cnt
        if st in totals:
            totals[st] += cnt

    total_n = sum(totals.values())
    return {
        "start_date": start_date,
        "end_date": end_date,
        "filters": {"kind": kind, "group": group, "sport": sport, "session": session, "section_id": section_id},
        "totals": {**totals, "total": total_n, "percentage": _pct(totals)},
        "by_kind": {k: {**v, "total": sum(v.values()), "percentage": _pct(v)} for k, v in by_kind.items()},
        "by_group": {k: {**v, "total": sum(v.values()), "percentage": _pct(v)} for k, v in by_group.items()},
        "by_sport": {k: {**v, "total": sum(v.values()), "percentage": _pct(v)} for k, v in by_sport.items()},
        "summary": by_kind,
    }


@router.post("/correct")
async def correct_attendance(payload: AttendanceCorrectionIn, user: dict = Depends(get_current_user)):
    if not _can_correct_attendance(user):
        raise HTTPException(403, "correct_attendance permission required")
    if not payload.reason.strip():
        raise HTTPException(400, "Audit reason is required")
    row = await db.attendance.find_one({"id": payload.record_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Attendance record not found")
    marked_at = now_utc().isoformat()
    updated = {**row, "status": payload.status, "marked_by": user["id"], "marked_by_name": user.get("name"),
               "marked_at": marked_at, "updated_at": marked_at, "source": "correction"}
    await db.attendance.update_one({"id": payload.record_id}, {"$set": updated})
    await log_attendance_audit(row, updated, user, reason=payload.reason.strip(), action="correction")
    return updated


@router.get("/audit")
async def attendance_audit_history(
    record_id: Optional[str] = None,
    person_id: Optional[str] = None,
    date: Optional[str] = None,
    limit: int = Query(100, le=500),
    user: dict = Depends(get_current_user),
):
    if not _can_view_attendance(user) and not is_admin(user):
        raise HTTPException(403, "view_attendance permission required")
    q: dict = {}
    if record_id:
        q["attendance_id"] = record_id
    if person_id:
        q["person_id"] = person_id
    if date:
        q["date"] = date
    return await db.attendance_audit.find(q, {"_id": 0}).sort("changed_at", -1).to_list(limit)


@router.get("/export")
async def export_attendance(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    kind: Optional[str] = None,
    group: Optional[str] = None,
    sport: Optional[str] = None,
    session: Optional[str] = None,
    institution: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    if not _can_view_attendance(user):
        raise HTTPException(403, "view_attendance permission required")
    today = now_utc().strftime("%Y-%m-%d")
    if not start_date:
        start_date = today
    if not end_date:
        end_date = today

    q: dict = {"date": {"$gte": start_date, "$lte": end_date}}
    if kind:
        q["kind"] = kind
    if group:
        q["group"] = group
    if sport:
        q["sport"] = sport
    if session:
        q["session"] = normalize_session(session, kind=kind)
    if user.get("role") == "coach":
        roster_ids = await _coach_player_roster_ids(user)
        q = _apply_coach_attendance_scope(user, q, sport)
        person_clause = {"person_id": {"$in": roster_ids}}
        q = {"$and": [q, person_clause]} if q else person_clause
    inst = resolve_user_institution(user, institution)
    ent_filt = attendance_entity_filter(inst)
    if ent_filt:
        q = {"$and": [q, ent_filt]}

    rows = await db.attendance.find(q, {"_id": 0}).sort([("date", -1), ("kind", 1)]).to_list(5000)
    person_ids = list({r["person_id"] for r in rows})
    people = await db.people.find({"id": {"$in": person_ids}}, {"_id": 0, "id": 1, "name": 1}).to_list(5000)
    users = await db.users.find({"id": {"$in": person_ids}}, {"_id": 0, "id": 1, "name": 1}).to_list(5000)
    names = {p["id"]: p["name"] for p in people}
    names.update({u["id"]: u["name"] for u in users})

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "kind", "session", "entity", "person_id", "person_name", "status", "group", "sport", "centre", "marked_by_name", "marked_at", "source"])
    for r in rows:
        writer.writerow([
            r.get("date"), r.get("kind"), r.get("session"), r.get("entity_id"),
            r.get("person_id"), names.get(r.get("person_id"), ""),
            r.get("status"), r.get("group") or "", r.get("sport") or "", r.get("centre") or "",
            r.get("marked_by_name") or "", r.get("marked_at") or r.get("created_at") or "",
            r.get("source") or "",
        ])
    buf.seek(0)
    filename = f"attendance_{start_date}_{end_date}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
