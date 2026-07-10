import uuid
from typing import Optional, List, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core import db, AttendanceBatch, get_current_user, now_utc, is_admin

router = APIRouter(prefix="/attendance", tags=["attendance"])


# -------- Staff Attendance (default-present workflow) --------
def _can_mark_pws_staff(user: dict) -> bool:
    return is_admin(user) or user.get("role") in ("principal", "vice_principal")


def _can_mark_alpha_staff(user: dict, centre: Optional[str]) -> bool:
    if is_admin(user):
        return True
    if user.get("role") == "coach" and user.get("coach_type") == "head":
        if centre is None:
            return True
        return centre in (user.get("assigned_centres") or [])
    return False


async def _staff_query_for_user(user: dict, centre: Optional[str] = None, organization: Optional[str] = None) -> dict:
    q: dict = {"kind": "staff"}
    if is_admin(user):
        # Sports Admin (admin role) is ALPHA-only
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
    organization: Optional[Literal["PWS", "ALPHA"]] = None  # required for admin; inferred otherwise
    centre: Optional[Literal["Balua", "Harding Park"]] = None  # for ALPHA only
    absent_staff_ids: List[str] = []


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
    # Determine organization scope
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
    for s in staff_list_docs:
        status = "absent" if s["id"] in absent_set else "present"
        rec = {
            "date": payload.date,
            "kind": "staff",
            "organization": s.get("organization"),
            "centre": s.get("centre"),
            "person_id": s["id"],
            "status": status,
            "marked_by": user["id"],
            "marked_by_name": user["name"],
            "created_at": now_utc().isoformat(),
        }
        await db.attendance.update_one(
            {"date": payload.date, "kind": "staff", "person_id": s["id"]},
            {"$set": rec},
            upsert=True,
        )
    return {
        "count": len(staff_list_docs),
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
    user: dict = Depends(get_current_user),
):
    # enforce visibility
    await _staff_query_for_user(user, centre=centre, organization=organization)  # raises on bad role
    q: dict = {"kind": "staff"}
    if date: q["date"] = date
    if organization: q["organization"] = organization
    if centre: q["centre"] = centre
    return await db.attendance.find(q, {"_id": 0}).sort("date", -1).to_list(2000)


# -------- Coach Attendance (default-present workflow) --------
def _can_mark_coach_attendance(user: dict) -> bool:
    if is_admin(user):
        return True
    if user.get("role") == "coach" and user.get("coach_type") == "head":
        return True
    return False


async def _coach_scope_filter(user: dict) -> dict:
    """Build mongo query scoping which coaches the marker may see/mark.
    - Super Admin: every active coach.
    - Sports Admin: every active coach (head + assistant) — ALPHA only by design.
    - Head Coach: themselves + active assistant coaches in their assigned_centres.
    """
    base: dict = {"role": "coach", "status": {"$ne": "deactivated"}}
    if user.get("role") == "super_admin":
        return base
    if user.get("role") == "admin":
        # Sports Admin oversees all ALPHA coaches (head + assistant)
        return base
    if user.get("role") == "coach" and user.get("coach_type") == "head":
        centres = user.get("assigned_centres") or []
        # self OR assistant coaches restricted to overlapping centres
        clauses: list = [{"id": user["id"]}]
        asst_clause: dict = {"coach_type": "assistant"}
        if centres:
            asst_clause["assigned_centres"] = {"$in": centres}
        clauses.append(asst_clause)
        base["$or"] = clauses
        return base
    if user.get("role") == "coach" and user.get("coach_type") == "assistant":
        # Assistants can only view themselves on the list (read-only on UI)
        base["id"] = user["id"]
        return base
    # Other roles: no scope (caller will 403)
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
    for c in coaches:
        status = "absent" if c["id"] in absent_set else "present"
        rec = {
            "date": payload.date,
            "kind": "coach",
            "person_id": c["id"],
            "user_id": c["id"],
            "name": c["name"],
            "coach_type": c.get("coach_type"),
            "status": status,
            "marked_by": user["id"],
            "marked_by_name": user["name"],
            "created_at": now_utc().isoformat(),
        }
        await db.attendance.update_one(
            {"date": payload.date, "kind": "coach", "person_id": c["id"]},
            {"$set": rec},
            upsert=True,
        )
    return {
        "count": len(coaches),
        "present": len(coaches) - len(absent_set),
        "absent": len(absent_set),
    }


@router.get("/coaches")
async def list_coach_attendance(date: Optional[str] = None, user: dict = Depends(get_current_user)):
    if not (_can_mark_coach_attendance(user) or user.get("role") == "coach"):
        raise HTTPException(403, "Not allowed")
    q: dict = {"kind": "coach"}
    if date: q["date"] = date
    return await db.attendance.find(q, {"_id": 0}).sort("date", -1).to_list(2000)


@router.post("/batch")
async def mark_attendance_batch(payload: AttendanceBatch, user: dict = Depends(get_current_user)):
    from routers.parents import push_parent_notification
    records = []
    today_str = now_utc().strftime("%Y-%m-%d")
    for m in payload.marks:
        rec = {
            "id": str(uuid.uuid4()),
            "date": payload.date,
            "kind": payload.kind,
            "group": payload.group,
            "sport": payload.sport,
            "session": payload.session,
            "person_id": m.person_id,
            "status": m.status,
            "marked_by": user["id"],
            "marked_by_name": user["name"],
            "created_at": now_utc().isoformat(),
        }
        await db.attendance.update_one(
            {
                "date": payload.date,
                "kind": payload.kind,
                "group": payload.group,
                "session": payload.session,
                "person_id": m.person_id,
            },
            {"$set": rec},
            upsert=True,
        )
        records.append(rec)
        # Parent notification — only when today's mark is absent and target is a student/player
        if m.status == "absent" and payload.date == today_str and payload.kind in ("student", "player"):
            try:
                await push_parent_notification(
                    m.person_id,
                    title="Absent today",
                    body=f"Your ward was marked absent on {today_str}.",
                    ntype="absent_today",
                )
            except Exception:
                pass
    return {"count": len(records), "records": records}

@router.get("")
async def list_attendance(
    date: Optional[str] = None,
    kind: Optional[str] = None,
    group: Optional[str] = None,
    person_id: Optional[str] = None,
    _user: dict = Depends(get_current_user),
):
    q = {}
    if date: q["date"] = date
    if kind: q["kind"] = kind
    if group: q["group"] = group
    if person_id: q["person_id"] = person_id
    return await db.attendance.find(q, {"_id": 0}).sort("date", -1).to_list(2000)

@router.get("/summary")
async def attendance_summary(_user: dict = Depends(get_current_user)):
    today = now_utc().strftime("%Y-%m-%d")
    pipeline = [
        {"$match": {"date": today}},
        {"$group": {"_id": {"kind": "$kind", "status": "$status"}, "count": {"$sum": 1}}},
    ]
    rows = await db.attendance.aggregate(pipeline).to_list(100)
    summary = {}
    for r in rows:
        kind = r["_id"]["kind"]
        st = r["_id"]["status"]
        summary.setdefault(kind, {"present": 0, "absent": 0, "late": 0, "leave": 0})
        summary[kind][st] = r["count"]
    return {"date": today, "summary": summary}
