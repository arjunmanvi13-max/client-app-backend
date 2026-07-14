"""Persist and apply category-level module access configuration."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from category_module_catalog import (
    LOCKED_USER_TYPES,
    _walk_modules,
    default_enabled_map,
    derive_permissions_from_modules,
    filter_catalog_for_user_type,
)
from core import db, now_utc
from user_classification import APPROVED_LOGIN_USER_TYPES, LEGACY_ROLE_TO_USER_TYPE, CATALOG_BY_CODE


def _legacy_roles_for_user_type(user_type: str) -> List[str]:
    roles = [user_type]
    for legacy, canonical in LEGACY_ROLE_TO_USER_TYPE.items():
        if canonical == user_type and legacy not in roles:
            roles.append(legacy)
    # Principal/VP map to PWS Admin
    if user_type == "pws_admin":
        roles.extend(["principal", "vice_principal"])
    if user_type == "alpha_admin":
        roles.append("admin")
    if user_type == "pws_teacher":
        roles.append("teacher")
    if user_type == "alpha_coach":
        roles.append("coach")
    return list(dict.fromkeys(roles))


async def get_category_modules(user_type: str) -> Dict[str, Any]:
    if user_type not in APPROVED_LOGIN_USER_TYPES:
        raise ValueError(f"Unknown user type: {user_type}")

    stored = await db.category_module_access.find_one({"user_type": user_type}, {"_id": 0})
    catalog = filter_catalog_for_user_type(user_type)
    locked = user_type in LOCKED_USER_TYPES

    if stored and stored.get("modules"):
        modules = {k: bool(v) for k, v in stored["modules"].items()}
    else:
        modules = default_enabled_map(user_type)

    # Ensure all catalog modules have a value
    for mid, val in default_enabled_map(user_type).items():
        modules.setdefault(mid, val)

    if locked:
        modules = {k: True for k in modules}

    return {
        "user_type": user_type,
        "display_name": CATALOG_BY_CODE.get(user_type, {}).get("displayName", user_type),
        "entity_scope": CATALOG_BY_CODE.get(user_type, {}).get("entityScope", ""),
        "locked": locked,
        "catalog": catalog,
        "modules": modules,
        "updated_at": stored.get("updated_at") if stored else None,
        "updated_by_name": stored.get("updated_by_name") if stored else None,
    }


async def _active_user_counts_by_type() -> Dict[str, int]:
    """Count non-deactivated login accounts per canonical user type."""
    counts = {ut: 0 for ut in APPROVED_LOGIN_USER_TYPES}
    cursor = db.users.find(
        {"status": {"$ne": "deactivated"}},
        {"_id": 0, "user_type": 1, "role": 1},
    )
    async for doc in cursor:
        ut = doc.get("user_type")
        if ut in counts:
            counts[ut] += 1
            continue
        legacy = doc.get("role") or ""
        canonical = LEGACY_ROLE_TO_USER_TYPE.get(legacy)
        if canonical in counts:
            counts[canonical] += 1
    return counts


async def list_category_permissions() -> List[Dict[str, Any]]:
    user_counts = await _active_user_counts_by_type()
    rows = []
    for ut in APPROVED_LOGIN_USER_TYPES:
        doc = await get_category_modules(ut)
        enabled_count = sum(1 for v in doc["modules"].values() if v)
        rows.append({
            "user_type": ut,
            "display_name": doc["display_name"],
            "entity_scope": doc["entity_scope"],
            "locked": doc["locked"],
            "enabled_count": enabled_count,
            "total_count": len(doc["modules"]),
            "active_user_count": user_counts.get(ut, 0),
        })
    return rows


async def save_category_modules(
    user_type: str,
    modules: Dict[str, bool],
    actor: dict,
) -> Dict[str, Any]:
    if user_type not in APPROVED_LOGIN_USER_TYPES:
        raise ValueError(f"Unknown user type: {user_type}")
    if user_type in LOCKED_USER_TYPES:
        raise PermissionError("Super Admin category access cannot be modified")

    catalog = filter_catalog_for_user_type(user_type)
    valid_ids = {m["id"] for _, m in _walk_modules(catalog)}
    normalized = {mid: bool(modules.get(mid)) for mid in valid_ids}

    legacy, rbac = derive_permissions_from_modules(user_type, normalized)
    now = now_utc().isoformat()

    await db.category_module_access.update_one(
        {"user_type": user_type},
        {"$set": {
            "user_type": user_type,
            "modules": normalized,
            "permissions": legacy,
            "permissions_rbac": rbac,
            "updated_at": now,
            "updated_by": actor["id"],
            "updated_by_name": actor["name"],
        }},
        upsert=True,
    )

    # Propagate to all accounts of this category
    roles = _legacy_roles_for_user_type(user_type)
    result = await db.users.update_many(
        {"$or": [{"user_type": user_type}, {"role": {"$in": roles}}]},
        {"$set": {"permissions": legacy, "permissions_rbac": rbac}},
    )

    audit_doc = {
        "id": str(uuid.uuid4()),
        "at": now,
        "actor_id": actor["id"],
        "actor_name": actor["name"],
        "target_user_type": user_type,
        "target_name": CATALOG_BY_CODE.get(user_type, {}).get("displayName", user_type),
        "changes": {"modules": normalized},
        "users_updated": result.modified_count,
    }
    await db.permission_audit.insert_one(audit_doc)

    out = await get_category_modules(user_type)
    out["users_updated"] = result.modified_count
    out["saved_at"] = now
    return out


async def permissions_for_user_type(user_type: str) -> Optional[Dict[str, Any]]:
    """Return stored legacy + rbac permissions for new user provisioning."""
    if user_type in LOCKED_USER_TYPES:
        return None
    stored = await db.category_module_access.find_one({"user_type": user_type}, {"_id": 0})
    if not stored:
        modules = default_enabled_map(user_type)
        legacy, rbac = derive_permissions_from_modules(user_type, modules)
        return {"permissions": legacy, "permissions_rbac": rbac}
    return {
        "permissions": stored.get("permissions") or {},
        "permissions_rbac": stored.get("permissions_rbac") or {},
    }
