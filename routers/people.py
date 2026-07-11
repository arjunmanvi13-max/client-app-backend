import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from core import db, PersonCreate, PersonUpdate, get_current_user, assert_can_manage, assert_player_action, assert_perm, get_perm, is_admin, is_sports_admin, is_super_admin, now_utc
from routers.academic import (
    resolve_section_group,
    assert_teacher_section_access,
    assigned_section_ids_for_teacher,
)

router = APIRouter(prefix="/people", tags=["people"])

_VIEW_PERM_BY_KIND = {"student": "view_students", "player": "view_players", "staff": "view_staff"}
_MARK_PERM_BY_KIND = {"student": "mark_student_attendance", "player": "mark_player_attendance", "staff": "mark_staff_attendance"}


def _can_list_kind(user: dict, kind: str) -> bool:
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
    if is_admin(user):
        return
    if kind == "student":
        assert_perm(user, "edit_students")
        return
    if kind == "player":
        assert_player_action(user, "edit")
        return
    assert_can_manage(user, kind)

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
    include_deactivated: bool = False,
    user: dict = Depends(get_current_user),
):
    if kind:
        _assert_can_list_kind(user, kind)
    if section_id and kind == "student":
        await assert_teacher_section_access(user, section_id)
    q = {}
    if kind: q["kind"] = kind
    if section_id:
        q["section_id"] = section_id
    elif group:
        q["group"] = group
    elif kind == "student" and user.get("role") == "teacher":
        assigned = await assigned_section_ids_for_teacher(user["id"])
        q["section_id"] = {"$in": assigned} if assigned else {"$in": []}
    if sport: q["sport"] = sport
    if resident is not None: q["is_resident"] = resident
    if slot: q["slot"] = slot
    if skill_level: q["skill_level"] = skill_level
    if assigned_coach_id: q["assigned_coach_id"] = assigned_coach_id
    if centre: q["centre"] = centre
    if player_type: q["player_type"] = player_type
    if status:
        q["status"] = status
    elif kind == "player" and not include_deactivated:
        q["status"] = {"$ne": "deactivated"}
    # Sports Admin scope: ALPHA-only, hide PWS people entirely
    if is_sports_admin(user):
        q["organization"] = "ALPHA"
        # Block PWS-only kinds
        if kind in ("student", "teacher"):
            return []
    return await db.people.find(q, {"_id": 0}).sort("name", 1).to_list(1000)

@router.get("/groups")
async def list_groups(kind: str, user: dict = Depends(get_current_user)):
    _assert_can_list_kind(user, kind)
    groups = await db.people.distinct("group", {"kind": kind})
    return {"kind": kind, "groups": sorted([g for g in groups if g])}

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
            "department": person.get("group") or existing.get("department"),
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
        "phone": person.get("mobile") or None,
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
    if person.get("mobile"):
        doc["mobile"] = person["mobile"]
    await db.users.insert_one(doc)
    return {k: v for k, v in doc.items() if k != "password_hash"}


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
    if payload.kind == "student" and payload.section_id:
        sid, label = await resolve_section_group(payload.section_id)
        doc["section_id"] = sid
        doc["group"] = label
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
        # players are no longer coach-assigned; players ALWAYS belong to ALPHA
        # (fee auto-generation depends on this — see bug: player saved as BOTH got no fees)
        doc["organization"] = "ALPHA"
        doc["assigned_coach_id"] = None
        doc.setdefault("status", "active")
    await db.people.insert_one(doc)
    # Auto-create fees for ALPHA player
    if payload.kind == "player":
        try:
            from routers.fees import auto_create_fees_for_player
            await auto_create_fees_for_player(doc)
        except Exception as e:
            # Do not block player creation on fees errors — but LOG loudly
            import logging
            logging.getLogger("fees").exception("Auto fee creation failed for player %s: %s", doc.get("id"), e)
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
    # Sports Admin: PWS records or non-ALPHA records appear as 404 (hide existence)
    if is_sports_admin(user) and (target.get("organization") != "ALPHA" or target.get("kind") in ("student", "teacher")):
        raise HTTPException(404, "Person not found")
    if target["kind"] == "player":
        assert_player_action(user, "edit")
    else:
        _assert_can_edit_kind(user, target["kind"])
    upd = payload.dict(exclude_none=True)
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
    if not is_admin(user):
        raise HTTPException(403, "Admin / Super Admin only")
    target = await db.people.find_one({"id": person_id})
    if not target:
        raise HTTPException(404, "Person not found")
    await db.people.update_one({"id": person_id}, {"$set": {"status": "active"}})
    fresh = await db.people.find_one({"id": person_id}, {"_id": 0})
    if fresh.get("kind") == "staff":
        await ensure_staff_user_account(fresh)
    return fresh

@router.post("/{person_id}/deactivate")
async def deactivate_person(person_id: str, user: dict = Depends(get_current_user)):
    if not is_admin(user):
        raise HTTPException(403, "Admin / Super Admin only")
    target = await db.people.find_one({"id": person_id})
    if not target:
        raise HTTPException(404, "Person not found")
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
