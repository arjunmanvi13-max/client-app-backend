"""Permission Control Panel — Super Admin only.

Endpoints:
- GET  /api/permissions/templates                — list of pre-configured templates
- GET  /api/permissions/audit                    — last 50 audit entries
- PATCH /api/users/{user_id}/permissions         — update toggles + role/coach_type, logs audit

Defaults are derived from role; existing accounts behave identically until super admin overrides.
"""
import uuid
from typing import Dict, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core import (
    db, get_current_user, is_super_admin, now_utc, public_user,
    PERMISSION_KEYS, PERMISSION_GROUPS, PERMISSION_TEMPLATES, default_permissions,
)

router = APIRouter(tags=["permissions"])


def _require_super_admin(user: dict):
    if not is_super_admin(user):
        raise HTTPException(403, "Super Admin only")


@router.get("/permissions/templates")
async def list_templates(user: dict = Depends(get_current_user)):
    _require_super_admin(user)
    return {
        "groups": PERMISSION_GROUPS,
        "keys": PERMISSION_KEYS,
        "templates": PERMISSION_TEMPLATES,
    }


@router.get("/permissions/audit")
async def list_audit(limit: int = 50, user: dict = Depends(get_current_user)):
    _require_super_admin(user)
    rows = await db.permission_audit.find({}, {"_id": 0}).sort("at", -1).to_list(min(limit, 200))
    return rows


class PermissionPatch(BaseModel):
    permissions: Optional[Dict[str, bool]] = None
    role: Optional[Literal["admin", "principal", "vice_principal", "teacher", "coach", "warden", "student", "player"]] = None
    coach_type: Optional[Literal["head", "assistant"]] = None
    template: Optional[str] = None  # apply a template's permissions wholesale


@router.patch("/users/{user_id}/permissions")
async def update_permissions(user_id: str, payload: PermissionPatch, user: dict = Depends(get_current_user)):
    _require_super_admin(user)
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "User not found")
    if target.get("role") == "super_admin":
        raise HTTPException(400, "Super Admin permissions cannot be modified")

    set_doc: dict = {}
    changes: dict = {}

    new_role = payload.role or target["role"]
    new_coach_type = payload.coach_type if payload.coach_type is not None else target.get("coach_type")
    if payload.role and payload.role != target["role"]:
        set_doc["role"] = payload.role
        changes["role"] = {"from": target["role"], "to": payload.role}
    if payload.coach_type is not None and payload.coach_type != target.get("coach_type"):
        set_doc["coach_type"] = payload.coach_type
        changes["coach_type"] = {"from": target.get("coach_type"), "to": payload.coach_type}

    # Resolve new permission map
    if payload.template:
        if payload.template not in PERMISSION_TEMPLATES:
            raise HTTPException(400, "Unknown template")
        new_perms = dict(PERMISSION_TEMPLATES[payload.template]["permissions"])  # copy
    else:
        new_perms = dict(target.get("permissions") or default_permissions(new_role, new_coach_type))
    if payload.permissions:
        for k, v in payload.permissions.items():
            if k not in PERMISSION_KEYS:
                raise HTTPException(400, f"Unknown permission key: {k}")
            new_perms[k] = bool(v)

    # Safety: warn if all permissions are off — still allow but log
    all_off = all(not v for v in new_perms.values())
    set_doc["permissions"] = new_perms

    # Diff old/new perms for audit
    old_perms = target.get("permissions") or default_permissions(target["role"], target.get("coach_type"))
    perm_diff = {k: {"from": old_perms.get(k, False), "to": new_perms.get(k, False)}
                 for k in PERMISSION_KEYS if old_perms.get(k, False) != new_perms.get(k, False)}
    if perm_diff:
        changes["permissions"] = perm_diff

    if not changes:
        # No-op
        return {**public_user(target), "warning": None}

    await db.users.update_one({"id": user_id}, {"$set": set_doc})
    audit_doc = {
        "id": str(uuid.uuid4()),
        "at": now_utc().isoformat(),
        "actor_id": user["id"],
        "actor_name": user["name"],
        "actor_email": user["email"],
        "target_id": target["id"],
        "target_name": target["name"],
        "target_email": target["email"],
        "template_applied": payload.template,
        "changes": changes,
    }
    await db.permission_audit.insert_one(audit_doc)

    updated = await db.users.find_one({"id": user_id}, {"_id": 0})
    out = public_user(updated)
    out["warning"] = "All permissions are turned OFF — user will have no access." if all_off else None
    return out
