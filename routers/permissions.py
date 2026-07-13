"""Permission Control Panel — Super Admin only (legacy + RBAC)."""
import uuid
from typing import Dict, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core import (
    db, get_current_user, is_super_admin, now_utc, public_user,
    PERMISSION_KEYS, PERMISSION_GROUPS, PERMISSION_TEMPLATES, default_permissions,
)
from rbac.bridge import RBAC_PERMISSION_GROUPS, RBAC_PERMISSION_LABELS
from rbac.enums import Permission
from rbac.guards import assert_manage_access, can_manage_access

router = APIRouter(tags=["permissions"])


def _require_super_admin(user: dict):
    if not can_manage_access(user):
        raise HTTPException(403, "Super Admin only")


@router.get("/permissions/templates")
async def list_templates(user: dict = Depends(get_current_user)):
    _require_super_admin(user)
    return {
        "groups": PERMISSION_GROUPS,
        "keys": PERMISSION_KEYS,
        "templates": PERMISSION_TEMPLATES,
        "rbac_groups": RBAC_PERMISSION_GROUPS,
        "rbac_keys": [p.value for p in Permission],
        "rbac_labels": RBAC_PERMISSION_LABELS,
    }


@router.get("/permissions/audit")
async def list_audit(limit: int = 50, user: dict = Depends(get_current_user)):
    _require_super_admin(user)
    rows = await db.permission_audit.find({}, {"_id": 0}).sort("at", -1).to_list(min(limit, 200))
    return rows


class PermissionPatch(BaseModel):
    permissions: Optional[Dict[str, bool]] = None
    permissions_rbac: Optional[Dict[str, bool]] = None
    role: Optional[Literal[
        "admin", "principal", "vice_principal", "teacher", "coach", "warden",
        "pws_accounts", "alpha_accounts", "student", "player",
    ]] = None
    coach_type: Optional[Literal["head", "assistant"]] = None
    template: Optional[str] = None


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

    if payload.template:
        if payload.template not in PERMISSION_TEMPLATES:
            raise HTTPException(400, "Unknown template")
        new_perms = dict(PERMISSION_TEMPLATES[payload.template]["permissions"])
    else:
        new_perms = dict(target.get("permissions") or default_permissions(new_role, new_coach_type))
    if payload.permissions:
        for k, v in payload.permissions.items():
            if k not in PERMISSION_KEYS:
                raise HTTPException(400, f"Unknown permission key: {k}")
            new_perms[k] = bool(v)

    rbac_overrides = dict(target.get("permissions_rbac") or {})
    if payload.permissions_rbac:
        valid = {p.value for p in Permission}
        for k, v in payload.permissions_rbac.items():
            if k not in valid:
                raise HTTPException(400, f"Unknown RBAC permission: {k}")
            rbac_overrides[k] = bool(v)

    all_off = all(not v for v in new_perms.values())
    set_doc["permissions"] = new_perms
    set_doc["permissions_rbac"] = rbac_overrides

    old_perms = target.get("permissions") or default_permissions(target["role"], target.get("coach_type"))
    perm_diff = {k: {"from": old_perms.get(k, False), "to": new_perms.get(k, False)}
                 for k in PERMISSION_KEYS if old_perms.get(k, False) != new_perms.get(k, False)}
    if perm_diff:
        changes["permissions"] = perm_diff

    old_rbac = target.get("permissions_rbac") or {}
    rbac_diff = {k: {"from": old_rbac.get(k), "to": rbac_overrides.get(k)}
                 for k in set(old_rbac) | set(rbac_overrides)
                 if old_rbac.get(k) != rbac_overrides.get(k)}
    if rbac_diff:
        changes["permissions_rbac"] = rbac_diff

    if not changes:
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
