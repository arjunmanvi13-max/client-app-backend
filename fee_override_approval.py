"""Fee override approval workflow for player/student create and edit."""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional, Tuple

from core import db, get_perm, is_super_admin, now_utc
from pws_fee_structure import pws_student_profile_from_person, resolve_category_amounts
from approval_types import enrich_approval, entity_from_person, role_label_from_person

FEE_OVERRIDE_KEYS = (
    "registration_fee_override",
    "monthly_fee_override",
    "hostel_fee_override",
    "transport_fee_monthly",
    "pws_fee_overrides",
)

APPROVAL_TYPE = "fee_override_admission"


def can_bypass_fee_approval(user: dict) -> bool:
    return (
        is_super_admin(user)
        or get_perm(user, "approve_requests")
        or get_perm(user, "approve_deactivation")
        or user.get("role") in ("principal", "vice_principal")
    )


async def default_fees_player(person: dict) -> Dict[str, Any]:
    from routers.fees import _canonical_category, _rates_for_person

    base = {**person}
    for key in FEE_OVERRIDE_KEYS:
        base.pop(key, None)
    base["transport_fee_monthly"] = 0
    rates = await _rates_for_person(base)
    defaults: Dict[str, Any] = {
        "registration": int(rates.get("registration") or 0),
        "monthly": int(rates.get("monthly") or 0),
        "transport": 0,
    }
    if person.get("player_type") == "Boarding" and person.get("pws_class"):
        profile = pws_student_profile_from_person(base)
        pws_defaults = resolve_category_amounts(
            profile["pws_class"],
            profile["transport_enabled"],
            profile["transport_distance"],
        )
        defaults["pws_tuition"] = int(pws_defaults.get("Tuition") or 0)
    return defaults


async def effective_fees_player(person: dict) -> Dict[str, Any]:
    from routers.fees import _canonical_category, _rates_for_person

    rates = await _rates_for_person(person)
    reg = int(person.get("registration_fee_override") or 0) or int(rates.get("registration") or 0)
    category = _canonical_category(person.get("player_type") or "Daily")
    monthly_override = int(person.get("monthly_fee_override") or 0)
    hostel_override = int(person.get("hostel_fee_override") or 0)
    if monthly_override:
        monthly = monthly_override
    elif category in ("Hostel", "Hostel Only") and hostel_override:
        monthly = hostel_override
    else:
        monthly = int(rates.get("monthly") or 0)
    effective: Dict[str, Any] = {
        "registration": reg,
        "monthly": monthly,
        "transport": int(person.get("transport_fee_monthly") or 0),
    }
    if person.get("player_type") == "Boarding":
        overrides = person.get("pws_fee_overrides") or {}
        if overrides.get("Tuition") is not None:
            effective["pws_tuition"] = int(overrides["Tuition"])
        else:
            profile = pws_student_profile_from_person(person)
            pws_amounts = resolve_category_amounts(
                profile["pws_class"],
                profile["transport_enabled"],
                profile["transport_distance"],
            )
            effective["pws_tuition"] = int(pws_amounts.get("Tuition") or 0)
    return effective


async def default_fees_student(person: dict) -> Dict[str, int]:
    profile = pws_student_profile_from_person({**person, "pws_fee_overrides": {}})
    return resolve_category_amounts(
        profile["pws_class"],
        profile["transport_enabled"],
        profile["transport_distance"],
    )


async def effective_fees_student(person: dict) -> Dict[str, int]:
    profile = pws_student_profile_from_person(person)
    return resolve_category_amounts(
        profile["pws_class"],
        profile["transport_enabled"],
        profile["transport_distance"],
        profile.get("overrides") or person.get("pws_fee_overrides"),
    )


async def analyze_fee_overrides(person: dict) -> Tuple[bool, Dict[str, Any], Dict[str, Any]]:
    kind = person.get("kind")
    if kind == "player":
        defaults = await default_fees_player(person)
        effective = await effective_fees_player(person)
    elif kind == "student":
        defaults = await default_fees_student(person)
        effective = await effective_fees_student(person)
    else:
        return False, {}, {}

    custom: Dict[str, Any] = {}
    for key, default_val in defaults.items():
        eff_val = effective.get(key, default_val)
        if eff_val != default_val:
            custom[key] = eff_val
    for key, eff_val in effective.items():
        if key not in defaults and eff_val:
            custom[key] = eff_val
    return bool(custom), defaults, custom


def extract_override_fields(person: dict) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in FEE_OVERRIDE_KEYS:
        val = person.get(key)
        if val is None:
            continue
        if key == "transport_fee_monthly" and int(val or 0) <= 0:
            continue
        if key == "pws_fee_overrides" and not val:
            continue
        out[key] = val
    return out


def strip_fee_overrides_from_person(doc: dict) -> dict:
    out = dict(doc)
    out.pop("registration_fee_override", None)
    out.pop("monthly_fee_override", None)
    out.pop("hostel_fee_override", None)
    out["transport_fee_monthly"] = 0
    out.pop("pws_fee_overrides", None)
    return out


def _override_fields_from_custom(person: dict, custom_fees: Dict[str, Any]) -> Dict[str, Any]:
    """Map comparison keys back to person document override fields."""
    kind = person.get("kind")
    out: Dict[str, Any] = {}
    if kind == "player":
        if "registration" in custom_fees:
            out["registration_fee_override"] = int(custom_fees["registration"])
        if "monthly" in custom_fees:
            from routers.fees import _canonical_category

            category = _canonical_category(person.get("player_type") or "Daily")
            if category in ("Hostel", "Hostel Only") and not person.get("monthly_fee_override"):
                out["hostel_fee_override"] = int(custom_fees["monthly"])
            else:
                out["monthly_fee_override"] = int(custom_fees["monthly"])
        if "transport" in custom_fees and int(custom_fees["transport"]) > 0:
            out["transport_fee_monthly"] = int(custom_fees["transport"])
        if "pws_tuition" in custom_fees:
            out["pws_fee_overrides"] = {"Tuition": int(custom_fees["pws_tuition"])}
    elif kind == "student":
        defaults_keys = set()
        overrides: Dict[str, int] = {}
        for cat, amount in custom_fees.items():
            overrides[str(cat)] = int(amount)
        if overrides:
            out["pws_fee_overrides"] = overrides
    return out


def apply_override_fields(person: dict, override_fields: Dict[str, Any]) -> dict:
    out = strip_fee_overrides_from_person(person)
    out.update(override_fields)
    return out


async def create_fee_override_approval_request(
    *,
    person: dict,
    user: dict,
    default_fees: Dict[str, Any],
    custom_fees: Dict[str, Any],
    override_fields: Dict[str, Any],
    is_create: bool,
    reason: str = "Custom fee override requested at admission/edit",
) -> dict:
    existing = await db.approval_requests.find_one({
        "type": APPROVAL_TYPE,
        "subject_id": person["id"],
        "status": "pending",
    })
    if existing:
        raise ValueError("A pending fee override approval already exists for this person")

    entity = entity_from_person(person)
    entity_id = "pws" if entity == "PWS" else "alpha" if entity == "ALPHA" else "pws"
    entity_type = "STUDENT" if person.get("kind") == "student" else "PLAYER"
    label = f"{person.get('name')} · custom fees"

    payload = {
        "entity_type": entity_type,
        "person_id": person["id"],
        "default_fees": default_fees,
        "custom_fees": custom_fees,
        "override_fields": override_fields,
        "is_create": is_create,
        "target_role": role_label_from_person(person),
    }

    doc = {
        "id": str(uuid.uuid4()),
        "type": APPROVAL_TYPE,
        "status": "pending",
        "entity_id": entity_id,
        "organization": entity,
        "subject_id": person["id"],
        "subject_label": label,
        "reason": reason.strip() or "Custom fee override",
        "payload": payload,
        "requested_by_id": user["id"],
        "requested_by_name": user["name"],
        "requested_at": now_utc().isoformat(),
        "decided_by_id": None,
        "decided_by_name": None,
        "decided_at": None,
        "decision_note": None,
        "history": [{
            "id": str(uuid.uuid4()),
            "action": "submitted",
            "user_id": user["id"],
            "user_name": user["name"],
            "note": reason.strip() or "Custom fee override",
            "at": now_utc().isoformat(),
        }],
        "comments": [],
    }
    await db.approval_requests.insert_one(doc)

    from notifications_service import send_to_role

    title = "Fee override approval required"
    message = f"{user['name']} requested custom fees for {person.get('name')} ({entity_type.title()})"
    for role in ("super_admin", "principal", "vice_principal"):
        await send_to_role(
            role,
            ntype="approval_requested",
            title=title,
            message=message,
            ref_id=doc["id"],
            ref_type="approval",
            entity_id=entity_id,
        )
    return doc


async def apply_approved_fee_override(req: dict, modified_custom_fees: Optional[Dict[str, Any]] = None) -> None:
    p = req.get("payload") or {}
    person_id = p.get("person_id") or req.get("subject_id")
    person = await db.people.find_one({"id": person_id}, {"_id": 0})
    if not person:
        raise ValueError("Person not found")

    custom_fees = modified_custom_fees if modified_custom_fees is not None else (p.get("custom_fees") or {})
    override_fields = _override_fields_from_custom(person, custom_fees)
    if not override_fields and p.get("override_fields"):
        override_fields = dict(p["override_fields"])

    set_fields: Dict[str, Any] = {
        "status": "active",
        "pending_fee_defaults": None,
        "pending_fee_custom": None,
    }
    for key in FEE_OVERRIDE_KEYS:
        set_fields[key] = override_fields.get(key) if key in override_fields else None

    unset_fields = {
        "pending_fee_defaults": "",
        "pending_fee_custom": "",
    }
    for key in FEE_OVERRIDE_KEYS:
        if key not in override_fields:
            unset_fields[key] = ""

    await db.people.update_one(
        {"id": person_id},
        {"$set": set_fields, "$unset": unset_fields},
    )

    fresh = await db.people.find_one({"id": person_id}, {"_id": 0})
    if fresh.get("kind") == "player":
        from routers.fees import auto_create_fees_for_player
        await auto_create_fees_for_player(fresh)
    elif fresh.get("kind") == "student":
        from routers.pws_fees import sync_pws_fees_for_student
        await sync_pws_fees_for_student(fresh)


async def apply_rejected_fee_override(req: dict) -> None:
    p = req.get("payload") or {}
    person_id = p.get("person_id") or req.get("subject_id")
    person = await db.people.find_one({"id": person_id}, {"_id": 0})
    if not person:
        return

    await db.people.update_one(
        {"id": person_id},
        {"$set": {
            "status": "active",
            "transport_fee_monthly": 0,
            "pending_fee_defaults": None,
            "pending_fee_custom": None,
        }, "$unset": {
            "pending_fee_defaults": "",
            "pending_fee_custom": "",
            "registration_fee_override": "",
            "monthly_fee_override": "",
            "hostel_fee_override": "",
            "pws_fee_overrides": "",
        }},
    )

    fresh = await db.people.find_one({"id": person_id}, {"_id": 0})
    if not fresh:
        return
    if fresh.get("kind") == "player":
        from routers.fees import auto_create_fees_for_player
        await auto_create_fees_for_player(fresh)
    elif fresh.get("kind") == "student":
        from routers.pws_fees import sync_pws_fees_for_student
        await sync_pws_fees_for_student(fresh)


def approval_out(doc: dict) -> dict:
    return enrich_approval(doc)
