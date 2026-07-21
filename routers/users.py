import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from core import (
    db, UserCreate, UserUpdate, get_current_user,
    is_admin, is_sports_admin, is_super_admin, has_any_manage_rights, assert_can_manage, MANAGE_KINDS,
    hash_password, public_user, directory_user, now_utc,
    validate_domain_email, default_permissions, PERMISSION_KEYS, format_date_display,
    active_status_filter, merge_mongo_query,
)
from coach_scope import normalize_coach_assignments, ERR_MULTI_SPORT
from user_classification import (
    APPROVED_LOGIN_USER_TYPES,
    apply_user_type_fields,
    catalog_export,
    legacy_role_for_user_type,
    migrate_legacy_role,
    resolve_user_type,
    validate_user_type_payload,
    CATALOG_BY_CODE,
)
from rbac.enums import UserRole
from rbac.guards import can_manage_academic, assert_can_create_login_user, assert_can_list_login_users
from teacher_profile_pdf import render_teacher_profile_pdf

router = APIRouter(prefix="/users", tags=["users"])

TEACHER_DESIGNATIONS = frozenset({"CLASS_TEACHER", "TEACHER"})


def _is_teacher_account(doc: dict) -> bool:
    ut = resolve_user_type(doc)
    return ut == UserRole.PWS_TEACHER.value or doc.get("role") == "teacher"


def _can_manage_teacher_account(actor: dict, target: dict) -> bool:
    if is_super_admin(actor):
        return True
    return _is_teacher_account(target) and can_manage_academic(actor)


def _can_toggle_account_status(actor: dict, target: dict) -> bool:
    if is_super_admin(actor):
        return True
    if _is_teacher_account(target) and can_manage_academic(actor):
        return True
    return False


def _normalize_mobile(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    digits = "".join(ch for ch in raw.strip() if ch.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) != 10 or digits[0] not in "6789":
        raise HTTPException(400, "Mobile must be a valid 10-digit Indian number")
    return digits


def _apply_teacher_profile_fields(doc: dict, fields: dict, *, user_type: Optional[str] = None) -> None:
    ut = user_type or doc.get("user_type") or resolve_user_type(doc)
    if "date_of_joining" in fields and fields["date_of_joining"] is not None:
        doc["date_of_joining"] = fields["date_of_joining"]
    if "address" in fields:
        addr = (fields.get("address") or "").strip()
        doc["address"] = addr or None
    if "teacher_designation" in fields:
        td = fields.get("teacher_designation")
        if td:
            if td not in TEACHER_DESIGNATIONS:
                raise HTTPException(400, "Teacher designation must be CLASS_TEACHER or TEACHER")
            doc["teacher_designation"] = td
    if ut == UserRole.PWS_TEACHER.value and not doc.get("teacher_designation"):
        doc["teacher_designation"] = "TEACHER"


def _user_type_list_query(user_type: str) -> dict:
    """Match users by canonical user_type, with legacy role fallback."""
    legacy_roles = {legacy_role_for_user_type(user_type)}
    if user_type == UserRole.PWS_ADMIN.value:
        legacy_roles = {"principal", "vice_principal"}
    return {
        "$or": [
            {"user_type": user_type},
            {"user_type": {"$exists": False}, "role": {"$in": list(legacy_roles)}},
        ]
    }


async def _log_coach_sport_audit(
    coach_id: str,
    coach_name: str,
    previous_sport: Optional[str],
    new_sport: Optional[str],
    changed_by: dict,
) -> None:
    if previous_sport == new_sport:
        return
    await db.coach_assignment_audit.insert_one({
        "id": str(uuid.uuid4()),
        "coach_id": coach_id,
        "coach_name": coach_name,
        "previous_sport": previous_sport,
        "new_sport": new_sport,
        "changed_by_id": changed_by["id"],
        "changed_by_name": changed_by.get("name"),
        "at": now_utc().isoformat(),
    })


async def _log_user_type_audit(
    *,
    action: str,
    target: dict,
    actor: dict,
    previous_user_type: Optional[str] = None,
    new_user_type: Optional[str] = None,
    previous_designation: Optional[str] = None,
    new_designation: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    await db.user_type_audit.insert_one({
        "id": str(uuid.uuid4()),
        "action": action,
        "target_user_id": target.get("id"),
        "target_user_name": target.get("name"),
        "previous_user_type": previous_user_type,
        "new_user_type": new_user_type,
        "previous_designation": previous_designation,
        "new_designation": new_designation,
        "legacy_role": target.get("legacy_role") or target.get("role"),
        "actor_id": actor["id"],
        "actor_name": actor.get("name"),
        "note": note,
        "at": now_utc().isoformat(),
    })


def _apply_coach_assignment_fields(doc: dict) -> None:
    role = doc.get("role")
    ut = doc.get("user_type") or resolve_user_type(doc)
    is_coach = role == "coach" or ut == UserRole.ALPHA_COACH.value
    if not is_coach:
        return
    doc["role"] = "coach"
    try:
        normalize_coach_assignments(doc)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not doc.get("assigned_sports"):
        raise HTTPException(400, "Assign exactly one sport (Cricket or Football) for ALPHA Coach accounts")
    if len(doc.get("assigned_sports") or []) != 1:
        raise HTTPException(400, ERR_MULTI_SPORT)


@router.get("/classification")
async def user_classification_catalog(_user: dict = Depends(get_current_user)):
    """Approved login user types — server-owned catalog for UI."""
    return {"userTypes": catalog_export(), "approvedCodes": list(APPROVED_LOGIN_USER_TYPES)}


APPROVED_LEGACY_ROLES = (
    "super_admin", "admin", "principal", "vice_principal",
    "pws_accounts", "alpha_accounts", "teacher", "coach",
)


@router.get("")
async def list_users(
    user_type: Optional[str] = None,
    role: Optional[str] = None,
    include_deactivated: bool = False,
    user: dict = Depends(get_current_user),
):
    """Login account listing — Super Admin, or PWS teacher provisioning when permitted."""
    assert_can_list_login_users(user, user_type)
    if user_type:
        if user_type not in APPROVED_LOGIN_USER_TYPES:
            raise HTTPException(400, f"Invalid user type: {user_type}")
        q = _user_type_list_query(user_type)
    elif role:
        mapped = migrate_legacy_role(role)
        q = _user_type_list_query(mapped[0]) if mapped[0] else {"role": role}
    else:
        q = {
            "$or": [
                {"user_type": {"$in": list(APPROVED_LOGIN_USER_TYPES)}},
                {"user_type": {"$exists": False}, "role": {"$in": list(APPROVED_LEGACY_ROLES)}},
            ]
        }
    q = merge_mongo_query(q, active_status_filter(include_deactivated))
    docs = await db.users.find(q, {"_id": 0, "password_hash": 0}).sort("name", 1).to_list(1000)
    return docs


@router.get("/directory")
async def directory(
    role: Optional[str] = None,
    include_deactivated: bool = False,
    user: dict = Depends(get_current_user),
):
    """Lightweight directory — any authenticated user. No emails/phones."""
    ut = resolve_user_type(user)
    if ut == UserRole.ALPHA_COACH.value or user.get("role") == "coach":
        raise HTTPException(403, "Directory is not available for coach accounts")
    q: dict = {}
    if role:
        q["role"] = role
    if is_sports_admin(user):
        if role in ("principal", "vice_principal", "teacher"):
            return []
        q["role"] = {"$nin": ["principal", "vice_principal", "teacher"]} if not role else q.get("role")
        q["organization"] = {"$in": ["ALPHA", "BOTH"]}
    q = merge_mongo_query(q, active_status_filter(include_deactivated))
    docs = await db.users.find(q, {"_id": 0}).to_list(1000)
    return [directory_user(u) for u in docs]


@router.post("")
async def create_user(payload: UserCreate, user: dict = Depends(get_current_user)):
    assert_can_create_login_user(user, payload.user_type)
    if payload.user_type == UserRole.SUPER_ADMIN.value:
        raise HTTPException(403, "Super Admin accounts are seed-managed and cannot be created via API")

    if not payload.email or not payload.password:
        raise HTTPException(400, "Email and password are required")
    if len(payload.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    email = validate_domain_email(payload.email)
    mobile = payload.mobile.strip() if payload.mobile else None
    if mobile:
        mobile = _normalize_mobile(mobile)
    if payload.phone and not mobile:
        mobile = _normalize_mobile(payload.phone)
    if await db.users.find_one({"email": email}):
        raise HTTPException(400, "Email already exists")
    if mobile and await db.users.find_one({"mobile": mobile}):
        raise HTTPException(400, "Mobile already exists")

    try:
        validate_user_type_payload(
            payload.user_type,
            designation=payload.designation,
            assigned_sports=payload.assigned_sports,
            organization=payload.organization,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    legacy_role = legacy_role_for_user_type(payload.user_type, payload.designation)
    rbac_overrides: dict = {}
    if payload.permissions:
        perms = {k: bool(payload.permissions.get(k, False)) for k in PERMISSION_KEYS}
    else:
        from category_permissions_service import permissions_for_user_type
        cat_perms = await permissions_for_user_type(payload.user_type)
        if cat_perms:
            perms = {k: bool(cat_perms["permissions"].get(k, False)) for k in PERMISSION_KEYS}
            rbac_overrides = dict(cat_perms.get("permissions_rbac") or {})
        else:
            perms = default_permissions(legacy_role, payload.coach_type)

    doc = {
        "id": str(uuid.uuid4()),
        "email": email,
        "password_hash": hash_password(payload.password),
        "is_password_set": True,
        "must_change_password": True,
        "name": payload.name,
        "department": payload.department,
        "phone": payload.phone,
        "can_manage": payload.can_manage or [],
        "coach_permissions": payload.coach_permissions,
        "coach_type": payload.coach_type,
        "assigned_sport": payload.assigned_sport,
        "assigned_centres": payload.assigned_centres,
        "assigned_sports": payload.assigned_sports,
        "linked_person_ids": payload.linked_person_ids,
        "permissions": perms,
        "permissions_rbac": rbac_overrides,
        "legacy_role": legacy_role,
        "created_at": now_utc().isoformat(),
        "status": "active",
    }
    apply_user_type_fields(doc, user_type=payload.user_type, designation=payload.designation)
    _apply_teacher_profile_fields(
        doc,
        {
            "date_of_joining": payload.date_of_joining,
            "address": payload.address,
            "teacher_designation": payload.teacher_designation,
        },
        user_type=payload.user_type,
    )
    _apply_coach_assignment_fields(doc)
    if doc.get("user_type") == UserRole.ALPHA_COACH.value:
        await _log_coach_sport_audit(doc["id"], doc["name"], None, doc.get("assigned_sport"), user)
    if mobile:
        doc["mobile"] = mobile
    await db.users.insert_one(doc)
    await _log_user_type_audit(
        action="created",
        target=doc,
        actor=user,
        new_user_type=doc.get("user_type"),
        new_designation=doc.get("designation"),
    )
    return public_user(doc)


@router.patch("/{user_id}")
async def update_user(user_id: str, payload: UserUpdate, user: dict = Depends(get_current_user)):
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "User not found")
    if user_id == user["id"] and (payload.user_type or payload.designation):
        raise HTTPException(403, "You cannot change your own user type or designation")

    body = payload.dict(exclude_none=True)
    is_type_change = "user_type" in body or "designation" in body
    if is_type_change and not is_super_admin(user):
        raise HTTPException(403, "Only Super Admin can change user type")

    if not is_super_admin(user):
        legacy_kind = target.get("role")
        if legacy_kind not in MANAGE_KINDS:
            raise HTTPException(403, "Only Super Admin can edit this user")
        if not (legacy_kind == "teacher" and can_manage_academic(user)):
            assert_can_manage(user, legacy_kind)
        body.pop("can_manage", None)
        body.pop("role", None)
        body.pop("email", None)
        body.pop("user_type", None)
        body.pop("designation", None)

    upd: dict = {}
    if "email" in body:
        new_email = validate_domain_email(body["email"])
        if await db.users.find_one({"email": new_email, "id": {"$ne": user_id}}):
            raise HTTPException(400, "Email already exists")
        body["email"] = new_email

    if "mobile" in body:
        body["mobile"] = _normalize_mobile(body["mobile"])

    if body.get("permissions") is not None:
        if not is_super_admin(user) and not _can_manage_teacher_account(user, target):
            raise HTTPException(403, "Only Super Admin can change module permissions")

    prev_user_type = resolve_user_type(target)
    prev_designation = target.get("designation")

    for k, v in body.items():
        if k == "password":
            if len(v) < 6:
                raise HTTPException(400, "Password must be at least 6 characters")
            upd["password_hash"] = hash_password(v)
            upd["is_password_set"] = True
            upd["must_change_password"] = True
        elif k == "permissions":
            perms = {pk: bool(v.get(pk, False)) for pk in PERMISSION_KEYS}
            upd["permissions"] = perms
        elif k == "teacher_designation":
            if v not in TEACHER_DESIGNATIONS:
                raise HTTPException(400, "Teacher designation must be CLASS_TEACHER or TEACHER")
            upd[k] = v
        elif k not in ("user_type", "designation", "role"):
            upd[k] = v

    merged = {**target, **upd, **body}
    new_user_type = body.get("user_type") or prev_user_type
    new_designation = body.get("designation", target.get("designation"))
    teacher_field_keys = ("date_of_joining", "address", "teacher_designation")
    if any(k in body for k in teacher_field_keys):
        _apply_teacher_profile_fields(merged, body, user_type=new_user_type)
        for k in teacher_field_keys:
            if k in merged:
                upd[k] = merged[k]

    if body.get("user_type") or body.get("designation"):
        try:
            validate_user_type_payload(
                new_user_type,
                designation=new_designation,
                assigned_sports=merged.get("assigned_sports"),
                organization=body.get("organization"),
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        apply_user_type_fields(merged, user_type=new_user_type, designation=new_designation)
        for k in ("user_type", "designation", "role", "organization", "entity_scope", "requires_user_type_review"):
            upd[k] = merged[k]

    if not upd:
        raise HTTPException(400, "No fields to update")

    prev_sport = target.get("assigned_sport")
    is_coach = (resolve_user_type(merged) == UserRole.ALPHA_COACH.value)
    if is_coach or any(k in upd for k in ("assigned_sport", "assigned_sports", "assigned_centres")):
        _apply_coach_assignment_fields(merged)
        for k in ("assigned_sport", "assigned_sports", "assigned_centres", "sport_assignment_status", "role"):
            if k in merged:
                upd[k] = merged[k]

    await db.users.update_one({"id": user_id}, {"$set": upd})

    if is_coach and any(k in upd for k in ("assigned_sport", "assigned_sports")):
        await _log_coach_sport_audit(user_id, target.get("name", ""), prev_sport, upd.get("assigned_sport"), user)
    if is_type_change:
        await _log_user_type_audit(
            action="type_changed",
            target={**target, **upd},
            actor=user,
            previous_user_type=prev_user_type,
            new_user_type=new_user_type,
            previous_designation=prev_designation,
            new_designation=new_designation,
        )

    doc = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    return doc


@router.get("/coach-assignment-audit")
async def coach_assignment_audit(limit: int = 50, user: dict = Depends(get_current_user)):
    if not is_super_admin(user):
        raise HTTPException(403, "Super Admin only")
    rows = await db.coach_assignment_audit.find({}, {"_id": 0}).sort("at", -1).to_list(min(limit, 200))
    return rows


@router.get("/user-type-audit")
async def user_type_audit(limit: int = 50, user: dict = Depends(get_current_user)):
    if not is_super_admin(user):
        raise HTTPException(403, "Super Admin only")
    rows = await db.user_type_audit.find({}, {"_id": 0}).sort("at", -1).to_list(min(limit, 200))
    return rows


@router.get("/coaches-needing-sport")
async def coaches_needing_sport(user: dict = Depends(get_current_user)):
    if not is_super_admin(user) and not is_admin(user):
        raise HTTPException(403, "Admin only")
    q = {
        "$and": [
            {"$or": [{"user_type": UserRole.ALPHA_COACH.value}, {"role": "coach"}]},
            {"status": {"$ne": "deactivated"}},
            {"$or": [
                {"sport_assignment_status": {"$in": ["required", "ambiguous"]}},
                {"assigned_sports": {"$size": 0}},
                {"assigned_sports.1": {"$exists": True}},
            ]},
        ]
    }
    docs = await db.users.find(q, {"_id": 0, "password_hash": 0}).sort("name", 1).to_list(500)
    return docs


@router.get("/needing-type-review")
async def users_needing_type_review(user: dict = Depends(get_current_user)):
    if not is_super_admin(user):
        raise HTTPException(403, "Super Admin only")
    q = {
        "$or": [
            {"requires_user_type_review": True},
            {"user_type": {"$exists": False}, "role": {"$nin": list(
                {legacy_role_for_user_type(ut) for ut in APPROVED_LOGIN_USER_TYPES}
                | {"principal", "vice_principal", "admin", "super_admin", "coach", "teacher",
                   "pws_accounts", "alpha_accounts"}
            )}},
        ]
    }
    docs = await db.users.find(q, {"_id": 0, "password_hash": 0}).sort("name", 1).to_list(500)
    return docs


@router.get("/{user_id}")
async def get_user(user_id: str, user: dict = Depends(get_current_user)):
    if not is_super_admin(user):
        raise HTTPException(403, "Super Admin only")
    doc = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    if not doc:
        raise HTTPException(404, "User not found")
    return doc


@router.post("/{user_id}/deactivate")
async def deactivate_user(user_id: str, user: dict = Depends(get_current_user)):
    if user_id == user["id"]:
        raise HTTPException(400, "Cannot deactivate yourself")
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "User not found")
    if not _can_toggle_account_status(user, target):
        raise HTTPException(403, "Not permitted to deactivate this account")
    if target.get("role") == "super_admin":
        raise HTTPException(400, "Cannot deactivate Super Admin")
    await db.users.update_one({"id": user_id}, {"$set": {"status": "deactivated"}})
    await _log_user_type_audit(action="deactivated", target=target, actor=user)
    return await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})


@router.post("/{user_id}/activate")
async def activate_user(user_id: str, user: dict = Depends(get_current_user)):
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "User not found")
    if not _can_toggle_account_status(user, target):
        raise HTTPException(403, "Not permitted to activate this account")
    if target.get("requires_user_type_review"):
        raise HTTPException(409, "Assign an approved user type before activating this account")
    await db.users.update_one({"id": user_id}, {"$set": {"status": "active"}})
    await _log_user_type_audit(action="activated", target=target, actor=user)
    return await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})


class ResetPasswordIn(BaseModel):
    new_password: str


@router.post("/{user_id}/reset-password")
async def reset_user_password(user_id: str, payload: ResetPasswordIn, user: dict = Depends(get_current_user)):
    if user.get("role") != "super_admin":
        raise HTTPException(403, "Super Admin only")
    if user_id == user["id"]:
        raise HTTPException(400, "Use profile > change password for your own account")
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "User not found")
    if len(payload.new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    await db.users.update_one({"id": user_id}, {"$set": {
        "password_hash": hash_password(payload.new_password),
        "is_password_set": True,
        "must_change_password": True,
        "password_reset_by": user["id"],
        "password_reset_at": now_utc().isoformat(),
    }})
    return await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})


@router.delete("/{user_id}")
async def delete_user(user_id: str, user: dict = Depends(get_current_user)):
    if not is_super_admin(user):
        raise HTTPException(403, "Super Admin only")
    if user_id == user["id"]:
        raise HTTPException(400, "Cannot delete yourself")
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "User not found")
    await db.users.delete_one({"id": user_id})
    await _log_user_type_audit(action="deleted", target=target, actor=user)
    return {"ok": True}


def _section_letter(label: str) -> str:
    import re
    m = re.search(r"-([A-G])$", (label or "").strip(), re.I)
    return m.group(1).upper() if m else ""


async def _teacher_class_rows_for_pdf(teacher_id: str) -> list:
    open_year = await db.academic_years.find_one({"status": "open"}, {"_id": 0})
    q: dict = {"teacher_user_id": teacher_id}
    if open_year:
        q["academic_year_id"] = open_year["id"]
    rows = await db.teacher_class_assignments.find(q, {"_id": 0}).to_list(500)
    grouped: dict = {}
    for r in rows:
        grade = await db.grades.find_one({"id": r["grade_id"]}, {"_id": 0, "name": 1})
        section = await db.sections.find_one({"id": r["section_id"]}, {"_id": 0, "label": 1})
        subject = await db.subjects.find_one({"id": r["subject_id"]}, {"_id": 0, "name": 1})
        class_name = (grade or {}).get("name") or "—"
        sec = _section_letter((section or {}).get("label") or "")
        key = f"{class_name}:{sec}"
        if key not in grouped:
            grouped[key] = {"class_name": class_name, "section": sec, "subjects": []}
        subj_name = (subject or {}).get("name")
        if subj_name and subj_name not in grouped[key]["subjects"]:
            grouped[key]["subjects"].append(subj_name)
    return list(grouped.values())


@router.get("/{user_id}/profile-pdf")
async def teacher_profile_pdf(user_id: str, user: dict = Depends(get_current_user)):
    target = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    if not target:
        raise HTTPException(404, "User not found")
    if not _can_manage_teacher_account(user, target):
        raise HTTPException(403, "Not permitted")
    class_rows = await _teacher_class_rows_for_pdf(user_id)
    pdf_bytes = render_teacher_profile_pdf(
        target,
        class_rows=class_rows,
        format_date=format_date_display,
    )
    safe_name = (target.get("name") or "teacher").replace(" ", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{safe_name}_profile.pdf"'},
    )
