import re
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import DuplicateKeyError
from core import db, PersonCreate, PersonUpdate, get_current_user, assert_can_manage, assert_player_action, assert_perm, get_perm, is_admin, is_sports_admin, is_super_admin, now_utc, resolve_user_institution, person_entity_filter, derive_person_entities, assert_person_entity_access, coach_can, logger
from routers.academic import (
    resolve_section_group,
    assert_teacher_section_access,
    assigned_section_ids_for_teacher,
)
from routers.coach import _coach_visibility_filter, _coach_assignment_lists

router = APIRouter(prefix="/people", tags=["people"])

_VIEW_PERM_BY_KIND = {"student": "view_students", "player": "view_players", "staff": "view_staff"}
_MARK_PERM_BY_KIND = {"student": "mark_student_attendance", "player": "mark_player_attendance", "staff": "mark_staff_attendance"}
_EDIT_PERM_BY_KIND = {"student": "edit_students", "player": "edit_players", "staff": "edit_students"}
_UNIQUE_ID_FIELDS = ("admission_number", "employee_id", "player_id")


def _strip_sparse_user_fields(doc: dict) -> dict:
    """Omit empty mobile/phone so sparse unique indexes are not tripped."""
    out = dict(doc)
    for key in ("mobile", "phone"):
        if key in out and not out[key]:
            out.pop(key)
    return out


def _assert_teacher_no_student_crud(user: dict, action: str) -> None:
    if user.get("role") == "teacher":
        raise HTTPException(403, f"Teachers cannot {action} students")


def _can_manage_person_status(user: dict, kind: str) -> bool:
    if is_admin(user):
        return True
    if kind == "student":
        return get_perm(user, "edit_students")
    if kind == "player":
        return get_perm(user, "edit_players") or coach_can(user, "edit")
    return kind in (user.get("can_manage") or [])


def _normalize_guardian_fields(doc: dict) -> dict:
    """Keep guardian_name and legacy father_name in sync."""
    if doc.get("guardian_name") and not doc.get("father_name"):
        doc["father_name"] = doc["guardian_name"]
    elif doc.get("father_name") and not doc.get("guardian_name"):
        doc["guardian_name"] = doc["father_name"]
    return doc


async def _assert_unique_ids(doc: dict, exclude_id: Optional[str] = None) -> None:
    for field in _UNIQUE_ID_FIELDS:
        val = (doc.get(field) or "").strip()
        if not val:
            continue
        q: dict = {field: val}
        if exclude_id:
            q["id"] = {"$ne": exclude_id}
        if await db.people.find_one(q, {"_id": 1}):
            label = field.replace("_", " ").title()
            raise HTTPException(409, f"{label} already exists")


def _search_filter(qtext: str) -> dict:
    rx = re.escape(qtext.strip())
    if not rx:
        return {}
    return {
        "$or": [
            {"name": {"$regex": rx, "$options": "i"}},
            {"admission_number": {"$regex": rx, "$options": "i"}},
            {"roll_number": {"$regex": rx, "$options": "i"}},
            {"employee_id": {"$regex": rx, "$options": "i"}},
            {"player_id": {"$regex": rx, "$options": "i"}},
            {"mobile": {"$regex": rx, "$options": "i"}},
            {"guardian_name": {"$regex": rx, "$options": "i"}},
            {"father_name": {"$regex": rx, "$options": "i"}},
        ]
    }


def _can_list_kind(user: dict, kind: str) -> bool:
    if user.get("role") == "coach":
        if kind != "player":
            return False
        return get_perm(user, "view_players") or get_perm(user, "mark_player_attendance") or coach_can(user, "view")
    if is_admin(user):
        return True
    if get_perm(user, _VIEW_PERM_BY_KIND.get(kind, "")):
        return True
    if get_perm(user, _MARK_PERM_BY_KIND.get(kind, "")):
        return True
    return kind in (user.get("can_manage") or [])


def _assert_can_list_kind(user: dict, kind: str) -> None:
    if not _can_list_kind(user, kind):
        raise HTTPException(403, f"You don't have permission to view {kind} records")


def _assert_can_add_kind(user: dict, kind: str) -> None:
    if kind == "student":
        _assert_teacher_no_student_crud(user, "add")
    if is_admin(user):
        return
    if kind == "student":
        assert_perm(user, "add_students")
        return
    if kind == "player":
        assert_player_action(user, "add")
        return
    assert_can_manage(user, kind)


def _assert_can_edit_kind(user: dict, kind: str) -> None:
    if kind == "student":
        _assert_teacher_no_student_crud(user, "edit or delete")
    if is_admin(user):
        return
    if kind == "student":
        assert_perm(user, "edit_students")
        return
    if kind == "player":
        assert_player_action(user, "edit")
        return
    assert_can_manage(user, kind)


def _assert_can_view_person(user: dict, person: dict) -> None:
    kind = person.get("kind", "")
    if user.get("role") == "parent":
        if person["id"] not in (user.get("linked_person_ids") or []):
            raise HTTPException(404, "Person not found")
        return
    if kind:
        _assert_can_list_kind(user, kind)
    if user.get("role") == "coach" and kind == "player":
        centres, sports = _coach_assignment_lists(user)
        if centres and person.get("centre") not in centres:
            raise HTTPException(404, "Person not found")
        if sports and person.get("sport") not in sports:
            raise HTTPException(404, "Person not found")
    if user.get("role") == "coach" and kind and kind != "player":
        raise HTTPException(404, "Person not found")


@router.get("")
async def list_people(
    kind: Optional[str] = None,
    group: Optional[str] = None,
    section_id: Optional[str] = None,
    sport: Optional[str] = None,
    resident: Optional[bool] = None,
    slot: Optional[str] = None,
    skill_level: Optional[str] = None,
    assigned_coach_id: Optional[str] = None,
    centre: Optional[str] = None,
    player_type: Optional[str] = None,
    status: Optional[str] = None,
    gender: Optional[str] = None,
    q: Optional[str] = None,
    include_deactivated: bool = False,
    institution: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    if user.get("role") == "parent":
        ids = user.get("linked_person_ids") or []
        if not ids:
            return []
        filt: dict = {"id": {"$in": ids}}
        if kind:
            filt["kind"] = kind
        if q:
            filt.update(_search_filter(q))
        return await db.people.find(filt, {"_id": 0}).sort("name", 1).to_list(100)
    if user.get("role") == "teacher" and not kind:
        raise HTTPException(400, "kind is required (e.g. kind=student)")
    if user.get("role") == "coach" and not kind:
        raise HTTPException(400, "kind is required (e.g. kind=player)")
    if kind:
        _assert_can_list_kind(user, kind)
    if section_id and kind == "student":
        await assert_teacher_section_access(user, section_id)
    query: dict = {}
    if kind:
        query["kind"] = kind
    if section_id:
        query["section_id"] = section_id
    elif group:
        query["group"] = group
    elif kind == "student" and user.get("role") == "teacher":
        assigned = await assigned_section_ids_for_teacher(user["id"])
        query["section_id"] = {"$in": assigned} if assigned else {"$in": []}
    if sport:
        if user.get("role") == "coach" and kind == "player":
            _, assigned_sports = _coach_assignment_lists(user)
            if assigned_sports and sport not in assigned_sports:
                raise HTTPException(403, "Sport not in your assigned sports")
        query["sport"] = sport
    if resident is not None:
        query["is_resident"] = resident
    if slot:
        query["slot"] = slot
    if skill_level:
        query["skill_level"] = skill_level
    if assigned_coach_id:
        query["assigned_coach_id"] = assigned_coach_id
    if centre:
        query["centre"] = centre
    if player_type:
        query["player_type"] = player_type
    if gender:
        query["gender"] = gender
    if status:
        query["status"] = status
    elif kind == "player" and not include_deactivated:
        query["status"] = {"$ne": "deactivated"}
    if q:
        query.update(_search_filter(q))
    if user.get("role") == "coach" and kind == "player":
        coach_q = _coach_visibility_filter(user, include_deactivated=include_deactivated)
        query = {"$and": [query, coach_q]} if query else coach_q
    elif user.get("role") == "coach":
        # Coach may only list players
        return []
    inst = resolve_user_institution(user, institution)
    query.update(person_entity_filter(inst))
    if is_sports_admin(user):
        if kind in ("student", "teacher"):
            return []
    return await db.people.find(query, {"_id": 0}).sort("name", 1).to_list(1000)


@router.get("/groups")
async def list_groups(kind: str, institution: Optional[str] = None, user: dict = Depends(get_current_user)):
    _assert_can_list_kind(user, kind)
    inst = resolve_user_institution(user, institution)
    filt = {"kind": kind, **person_entity_filter(inst)}
    groups = await db.people.distinct("group", filt)
    return {"kind": kind, "groups": sorted([g for g in groups if g])}


@router.get("/{person_id}")
async def get_person(person_id: str, user: dict = Depends(get_current_user)):
    person = await db.people.find_one({"id": person_id}, {"_id": 0})
    if not person:
        raise HTTPException(404, "Person not found")
    assert_person_entity_access(user, person)
    _assert_can_view_person(user, person)
    if person.get("kind") == "student" and user.get("role") == "teacher":
        await assert_teacher_section_access(user, person.get("section_id") or "")
    return person

def _validate_player_centre_type(centre: Optional[str], ptype: Optional[str]):
    if centre == "Harding Park" and ptype and ptype != "Daily":
        raise HTTPException(400, "Harding Park centre allows Daily players only")
    # Balua allows Daily, Day Boarding, Hostel/Hostel Only, Boarding — all valid


async def ensure_staff_user_account(person: dict) -> Optional[dict]:
    """Every STAFF person gets (and stays synced with) a login/user account so they
    automatically appear in the Permissions module for role & access assignment.
    Email auto-generated from the name (@prarambhika.com); default password Staff@123
    with a forced change on first login."""
    if person.get("kind") != "staff":
        return None
    from core import hash_password, default_permissions
    existing = await db.users.find_one({"person_id": person["id"]})
    if existing:
        await db.users.update_one({"id": existing["id"]}, {"$set": {
            "name": person.get("name") or existing.get("name"),
            "organization": person.get("organization") or existing.get("organization", "PWS"),
            "department": person.get("department") or person.get("group") or existing.get("department"),
            "status": "deactivated" if person.get("status") == "deactivated" else "active",
        }})
        return await db.users.find_one({"id": existing["id"]}, {"_id": 0})
    # Generate a unique org-domain email from the name: "Sonu Kumar" → sonu.kumar@prarambhika.com
    import re as _re
    base = _re.sub(r"[^a-z0-9]+", ".", (person.get("name") or "staff").lower()).strip(".") or "staff"
    email = f"{base}@prarambhika.com"
    n = 1
    while await db.users.find_one({"email": email}):
        n += 1
        email = f"{base}{n}@prarambhika.com"
    doc = {
        "id": str(uuid.uuid4()),
        "person_id": person["id"],
        "email": email,
        "password_hash": hash_password("Staff@123"),
        "is_password_set": True,
        "must_change_password": True,
        "name": person.get("name"),
        "role": "staff",
        "organization": person.get("organization") or "PWS",
        "department": person.get("group") or None,
        "can_manage": [],
        "coach_permissions": [],
        "coach_type": None,
        "assigned_sport": None,
        "assigned_centres": [],
        "assigned_sports": [],
        "permissions": default_permissions("staff"),
        "status": "deactivated" if person.get("status") == "deactivated" else "active",
        "created_at": now_utc().isoformat(),
    }
    try:
        result = await db.users.update_one(
            {"email": email},
            {"$setOnInsert": _strip_sparse_user_fields(doc)},
            upsert=True,
        )
        if result.upserted_id is not None:
            return {k: v for k, v in doc.items() if k != "password_hash"}
        return await db.users.find_one({"email": email}, {"_id": 0, "password_hash": 0})
    except DuplicateKeyError:
        logger.warning("Staff user insert skipped — duplicate key for %s", email)
        return await db.users.find_one({"email": email}, {"_id": 0, "password_hash": 0})


def _age_from_dob(dob: Optional[str]) -> Optional[int]:
    if not dob:
        return None
    try:
        from datetime import datetime as _dt
        d = _dt.fromisoformat(dob)
        today = _dt.now()
        yrs = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
        return max(yrs, 0)
    except Exception:
        return None

@router.post("")
async def create_person(payload: PersonCreate, user: dict = Depends(get_current_user)):
    if payload.kind == "player":
        assert_player_action(user, "add")
        _validate_player_centre_type(payload.centre, payload.player_type)
        if not payload.date_of_admission:
            raise HTTPException(400, "Date of admission is required for players")
    else:
        _assert_can_add_kind(user, payload.kind)
    doc = {"id": str(uuid.uuid4()), **payload.dict(), "created_at": now_utc().isoformat()}
    doc = _normalize_guardian_fields(doc)
    doc["entities"] = derive_person_entities(doc)
    await _assert_unique_ids(doc)
    if payload.kind == "student":
        doc.setdefault("organization", "PWS")
        doc.setdefault("date_of_admission", now_utc().strftime("%Y-%m-%d"))
    if payload.kind == "student" and payload.section_id:
        sid, label = await resolve_section_group(payload.section_id)
        doc["section_id"] = sid
        doc["group"] = label
    if payload.kind == "staff":
        if payload.department:
            doc["department"] = payload.department
            if not doc.get("group"):
                doc["group"] = payload.department
        if payload.email:
            doc.setdefault("mobile", doc.get("mobile"))
    # Auto-compute age from DOB if provided
    if doc.get("dob"):
        derived = _age_from_dob(doc["dob"])
        if derived is not None:
            doc["age"] = derived
    # Only Super Admin can set monthly/registration overrides at admission
    if not is_super_admin(user):
        doc.pop("monthly_fee_override", None)
        doc.pop("registration_fee_override", None)
    if payload.kind == "player":
        # Players default to ALPHA; super admin may set entities for dual participation
        if not payload.entities and payload.organization != "BOTH":
            doc["organization"] = "ALPHA"
        doc["entities"] = derive_person_entities(doc)
        doc["assigned_coach_id"] = None
        doc.setdefault("status", "active")
    await db.people.insert_one(doc)
    # Auto-create fees for ALPHA player or PWS student
    if payload.kind == "player":
        try:
            from routers.fees import auto_create_fees_for_player
            await auto_create_fees_for_player(doc)
        except Exception as e:
            import logging
            logging.getLogger("fees").exception("Auto fee creation failed for player %s: %s", doc.get("id"), e)
    elif payload.kind == "student":
        try:
            from routers.fees import auto_create_fees_for_student
            await auto_create_fees_for_student(doc)
        except Exception as e:
            import logging
            logging.getLogger("fees").exception("Auto fee creation failed for student %s: %s", doc.get("id"), e)
    # STAFF ⇄ PERMISSIONS SYNC: staff members automatically get a user account
    if payload.kind == "staff":
        try:
            await ensure_staff_user_account(doc)
        except Exception:
            import logging
            logging.getLogger("people").exception("Staff user-account sync failed for %s", doc.get("id"))
    doc.pop("_id", None)
    return doc

@router.patch("/{person_id}")
async def update_person(person_id: str, payload: PersonUpdate, user: dict = Depends(get_current_user)):
    target = await db.people.find_one({"id": person_id})
    if not target:
        raise HTTPException(404, "Person not found")
    assert_person_entity_access(user, target)
    if target["kind"] == "player":
        assert_player_action(user, "edit")
    else:
        _assert_can_edit_kind(user, target["kind"])
    upd = payload.dict(exclude_none=True)
    upd = _normalize_guardian_fields(upd)
    if target["kind"] == "student" and upd.get("section_id"):
        sid, label = await resolve_section_group(upd["section_id"])
        upd["section_id"] = sid
        upd["group"] = label
    # Only Super Admin can update fee overrides post-admission
    if not is_super_admin(user):
        upd.pop("monthly_fee_override", None)
        upd.pop("registration_fee_override", None)
    # Auto-compute age from DOB when DOB is being changed
    if upd.get("dob"):
        derived = _age_from_dob(upd["dob"])
        if derived is not None:
            upd["age"] = derived
    # Block status change via PATCH — must use dedicated activate/deactivate endpoints (admin-only)
    upd.pop("status", None)
    if not upd:
        raise HTTPException(400, "No fields to update")
    merged = {**target, **upd}
    await _assert_unique_ids(merged, exclude_id=person_id)
    upd["entities"] = derive_person_entities(merged)
    merged = {**target, **upd}
    if merged.get("kind") == "player":
        _validate_player_centre_type(merged.get("centre"), merged.get("player_type"))
        # ignore assigned_coach_id changes — players are centre-based now
        upd.pop("assigned_coach_id", None)
    await db.people.update_one({"id": person_id}, {"$set": upd})
    fresh = await db.people.find_one({"id": person_id}, {"_id": 0})
    if fresh.get("kind") == "staff":
        try:
            await ensure_staff_user_account(fresh)
        except Exception:
            import logging
            logging.getLogger("people").exception("Staff user-account sync failed for %s", person_id)
    return fresh

@router.post("/{person_id}/activate")
async def activate_person(person_id: str, user: dict = Depends(get_current_user)):
    target = await db.people.find_one({"id": person_id})
    if not target:
        raise HTTPException(404, "Person not found")
    assert_person_entity_access(user, target)
    if not _can_manage_person_status(user, target.get("kind", "")):
        raise HTTPException(403, "Not allowed to reactivate this person")
    await db.people.update_one({"id": person_id}, {"$set": {"status": "active"}})
    fresh = await db.people.find_one({"id": person_id}, {"_id": 0})
    if fresh.get("kind") == "staff":
        await ensure_staff_user_account(fresh)
    return fresh

@router.post("/{person_id}/deactivate")
async def deactivate_person(person_id: str, user: dict = Depends(get_current_user)):
    target = await db.people.find_one({"id": person_id})
    if not target:
        raise HTTPException(404, "Person not found")
    assert_person_entity_access(user, target)
    if not _can_manage_person_status(user, target.get("kind", "")):
        raise HTTPException(403, "Not allowed to deactivate this person")

    kind = target.get("kind", "")
    from routers.approvals import _can_approve, _history_entry, _approval_out
    if kind in ("student", "player") and not _can_approve(user):
        approval_type = "student_deactivation" if kind == "student" else "player_deactivation"
        existing = await db.approval_requests.find_one({
            "type": approval_type,
            "subject_id": person_id,
            "status": "pending",
        })
        if existing:
            raise HTTPException(400, "A pending deactivation approval already exists")
        doc = {
            "id": str(uuid.uuid4()),
            "type": approval_type,
            "status": "pending",
            "entity_id": "pws" if kind == "student" else "alpha",
            "subject_id": person_id,
            "subject_label": target.get("name"),
            "reason": "Deactivation requested",
            "payload": {
                "person_id": person_id,
                "centre": target.get("centre"),
                "sport": target.get("sport"),
                "category": target.get("player_type"),
            },
            "requested_by_id": user["id"],
            "requested_by_name": user["name"],
            "requested_at": now_utc().isoformat(),
            "decided_by_id": None,
            "decided_by_name": None,
            "decided_at": None,
            "decision_note": None,
            "history": [_history_entry("submitted", user, "Deactivation requested")],
            "comments": [],
        }
        await db.approval_requests.insert_one(doc)
        from core import notify_role
        await notify_role(
            "super_admin",
            ntype="approval_request",
            title=f"{kind.title()} deactivation request",
            message=f"{user['name']} requested deactivation of {target.get('name')}",
            ref_id=doc["id"],
        )
        return {"approval_required": True, "approval": _approval_out(doc)}

    await db.people.update_one({"id": person_id}, {"$set": {"status": "deactivated"}})
    fresh = await db.people.find_one({"id": person_id}, {"_id": 0})
    if fresh.get("kind") == "staff":
        await ensure_staff_user_account(fresh)
    return fresh

@router.delete("/{person_id}")
async def delete_person(person_id: str, user: dict = Depends(get_current_user)):
    target = await db.people.find_one({"id": person_id})
    if not target:
        raise HTTPException(404, "Person not found")
    assert_person_entity_access(user, target)
    if target["kind"] == "player":
        if not is_admin(user):
            assert_player_action(user, "edit")
    else:
        _assert_can_edit_kind(user, target["kind"])
    await db.people.delete_one({"id": person_id})
    return {"ok": True}


# -------- Parent linking (admin / super admin only) --------
from pydantic import BaseModel as _BaseModel


class ParentLinkIn(_BaseModel):
    user_id: str


@router.post("/{person_id}/link-parent")
async def link_parent(person_id: str, payload: ParentLinkIn, user: dict = Depends(get_current_user)):
    if not is_admin(user):
        raise HTTPException(403, "Admin / Super Admin only")
    target = await db.people.find_one({"id": person_id})
    if not target:
        raise HTTPException(404, "Person not found")
    assert_person_entity_access(user, target)
    if target.get("kind") not in ("student", "player"):
        raise HTTPException(400, "Parents can only be linked to students or players")
    parent_user = await db.users.find_one({"id": payload.user_id, "role": "parent"})
    if not parent_user:
        raise HTTPException(404, "Parent user not found")
    # update both sides
    await db.people.update_one({"id": person_id}, {"$addToSet": {"parent_user_ids": payload.user_id}})
    await db.users.update_one({"id": payload.user_id}, {"$addToSet": {"linked_person_ids": person_id}})
    return await db.people.find_one({"id": person_id}, {"_id": 0})


@router.delete("/{person_id}/link-parent/{user_id}")
async def unlink_parent(person_id: str, user_id: str, user: dict = Depends(get_current_user)):
    if not is_admin(user):
        raise HTTPException(403, "Admin / Super Admin only")
    target = await db.people.find_one({"id": person_id})
    if not target:
        raise HTTPException(404, "Person not found")
    await db.people.update_one({"id": person_id}, {"$pull": {"parent_user_ids": user_id}})
    await db.users.update_one({"id": user_id}, {"$pull": {"linked_person_ids": person_id}})
    return await db.people.find_one({"id": person_id}, {"_id": 0})
