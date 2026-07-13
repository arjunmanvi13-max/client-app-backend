import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core import (
    db, UserCreate, UserUpdate, get_current_user,
    is_admin, is_sports_admin, is_super_admin, has_any_manage_rights, assert_can_manage, MANAGE_KINDS,
    hash_password, public_user, directory_user, now_utc,
    validate_domain_email, default_permissions, PERMISSION_KEYS,
)

router = APIRouter(prefix="/users", tags=["users"])

@router.get("")
async def list_users(role: Optional[str] = None, user: dict = Depends(get_current_user)):
    """Full user listing — restricted to admins and users with management rights."""
    if not has_any_manage_rights(user):
        raise HTTPException(403, "Requires management rights")
    q = {}
    if role:
        q["role"] = role
    # Sports Admin scope: hide PWS-only roles entirely (principal/vice_principal/teacher) and only ALPHA/BOTH orgs
    if is_sports_admin(user):
        if role in ("principal", "vice_principal", "teacher"):
            return []
        q["role"] = {"$nin": ["principal", "vice_principal", "teacher"]} if not role else q.get("role")
        q["organization"] = {"$in": ["ALPHA", "BOTH"]}
    docs = await db.users.find(q, {"_id": 0, "password_hash": 0}).to_list(1000)
    return docs

@router.get("/directory")
async def directory(role: Optional[str] = None, user: dict = Depends(get_current_user)):
    """Lightweight directory — any authenticated user. No emails/phones."""
    if user.get("role") == "coach":
        raise HTTPException(403, "Directory is not available for coach accounts")
    q = {}
    if role:
        q["role"] = role
    if is_sports_admin(user):
        if role in ("principal", "vice_principal", "teacher"):
            return []
        q["role"] = {"$nin": ["principal", "vice_principal", "teacher"]} if not role else q.get("role")
        q["organization"] = {"$in": ["ALPHA", "BOTH"]}
    docs = await db.users.find(q, {"_id": 0}).to_list(1000)
    return [directory_user(u) for u in docs]

@router.post("")
async def create_user(payload: UserCreate, user: dict = Depends(get_current_user)):
    target_kind = payload.role if payload.role in MANAGE_KINDS else None
    if target_kind:
        assert_can_manage(user, target_kind)
    elif not is_admin(user):
        raise HTTPException(403, "Only admins can create users with this role")
    # Block creation of additional super_admins via this API — those must come from seed (allowed mobiles)
    if payload.role == "super_admin":
        raise HTTPException(403, "Super Admin accounts are seed-managed and cannot be created via API")
    # Only Super Admin can create Sports Admins (role=admin) or other privileged roles
    if payload.role in ("admin", "principal", "vice_principal", "warden") and not is_super_admin(user):
        raise HTTPException(403, f"Only Super Admin can create '{payload.role}' accounts")
    can_manage_to_set = payload.can_manage if is_admin(user) else []

    # Email + password are mandatory — email must belong to the org domain.
    if not payload.email or not payload.password:
        raise HTTPException(400, "Email and password are required")
    if len(payload.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    email = validate_domain_email(payload.email)
    mobile = payload.mobile.strip() if payload.mobile else None
    if await db.users.find_one({"email": email}):
        raise HTTPException(400, "Email already exists")
    if mobile and await db.users.find_one({"mobile": mobile}):
        raise HTTPException(400, "Mobile already exists")

    # Tick-box permissions (admins only) — else sensible role defaults.
    if payload.permissions and is_admin(user):
        perms = {k: bool(payload.permissions.get(k, False)) for k in PERMISSION_KEYS}
    else:
        perms = default_permissions(payload.role, payload.coach_type)

    doc = {
        "id": str(uuid.uuid4()),
        "email": email,
        "password_hash": hash_password(payload.password),
        "is_password_set": True,
        "must_change_password": True,   # admin-assigned password — user must set their own on first login
        "name": payload.name,
        "role": payload.role,
        "organization": payload.organization,
        "department": payload.department,
        "phone": payload.phone,
        "can_manage": can_manage_to_set,
        "coach_permissions": payload.coach_permissions,
        "coach_type": payload.coach_type,
        "assigned_sport": payload.assigned_sport,
        "assigned_centres": payload.assigned_centres,
        "assigned_sports": payload.assigned_sports,
        "linked_person_ids": payload.linked_person_ids,
        "permissions": perms,
        "created_at": now_utc().isoformat(),
    }
    # Only include sparse-indexed fields when set so the sparse unique index works.
    if mobile:
        doc["mobile"] = mobile
    await db.users.insert_one(doc)
    return public_user(doc)

@router.patch("/{user_id}")
async def update_user(user_id: str, payload: UserUpdate, user: dict = Depends(get_current_user)):
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "User not found")
    target_kind = target["role"] if target["role"] in MANAGE_KINDS else None
    if not is_admin(user):
        if target_kind is None:
            raise HTTPException(403, "Only admins can edit this user")
        assert_can_manage(user, target_kind)
    upd: dict = {}
    body = payload.dict(exclude_none=True)
    if not is_admin(user):
        body.pop("can_manage", None)
        body.pop("role", None)
        body.pop("email", None)
    if "email" in body:
        new_email = validate_domain_email(body["email"])
        if await db.users.find_one({"email": new_email, "id": {"$ne": user_id}}):
            raise HTTPException(400, "Email already exists")
        body["email"] = new_email
    for k, v in body.items():
        if k == "password":
            if len(v) < 6:
                raise HTTPException(400, "Password must be at least 6 characters")
            upd["password_hash"] = hash_password(v)
            upd["is_password_set"] = True
            upd["must_change_password"] = True  # admin-assigned — user must set their own next login
        else:
            upd[k] = v
    if not upd:
        raise HTTPException(400, "No fields to update")
    await db.users.update_one({"id": user_id}, {"$set": upd})
    doc = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    return doc

@router.post("/{user_id}/deactivate")
async def deactivate_user(user_id: str, user: dict = Depends(get_current_user)):
    if not is_admin(user):
        raise HTTPException(403, "Admin / Super Admin only")
    if user_id == user["id"]:
        raise HTTPException(400, "Cannot deactivate yourself")
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "User not found")
    if target.get("role") == "super_admin":
        raise HTTPException(400, "Cannot deactivate Super Admin")
    await db.users.update_one({"id": user_id}, {"$set": {"status": "deactivated"}})
    return await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})


@router.post("/{user_id}/activate")
async def activate_user(user_id: str, user: dict = Depends(get_current_user)):
    if not is_admin(user):
        raise HTTPException(403, "Admin / Super Admin only")
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "User not found")
    await db.users.update_one({"id": user_id}, {"$set": {"status": "active"}})
    return await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})


class ResetPasswordIn(BaseModel):
    new_password: str


@router.post("/{user_id}/reset-password")
async def reset_user_password(user_id: str, payload: ResetPasswordIn, user: dict = Depends(get_current_user)):
    """Super Admin assigns a temporary password — the user must change it on next login."""
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
    if user_id == user["id"]:
        raise HTTPException(400, "Cannot delete yourself")
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "User not found")
    target_kind = target["role"] if target["role"] in MANAGE_KINDS else None
    if not is_admin(user):
        if target_kind is None:
            raise HTTPException(403, "Only admins can delete this user")
        assert_can_manage(user, target_kind)
    await db.users.delete_one({"id": user_id})
    return {"ok": True}
