"""Normalized approval categories, role labels, and API enrichment."""
from __future__ import annotations

from typing import Any, Dict, Optional

APPROVAL_CATEGORIES = (
    "user_deactivation",
    "fee_edit",
    "fee_concession",
    "refund",
)

LEGACY_DEACTIVATION_TYPES = ("student_deactivation", "player_deactivation", "user_deactivation")

TYPE_TO_CATEGORY: Dict[str, str] = {
    "student_deactivation": "user_deactivation",
    "player_deactivation": "user_deactivation",
    "user_deactivation": "user_deactivation",
    "fee_edit": "fee_edit",
    "fee_concession": "fee_concession",
    "refund": "refund",
}

CATEGORY_TYPES: Dict[str, tuple] = {
    "user_deactivation": LEGACY_DEACTIVATION_TYPES,
    "fee_edit": ("fee_edit",),
    "fee_concession": ("fee_concession",),
    "refund": ("refund",),
}


def category_for_type(raw_type: str) -> str:
    return TYPE_TO_CATEGORY.get(raw_type or "", raw_type or "user_deactivation")


def types_for_category(category: Optional[str]) -> Optional[tuple]:
    if not category or category == "all":
        return None
    return CATEGORY_TYPES.get(category)


def entity_label(entity_id: Optional[str], organization: Optional[str] = None) -> str:
    if organization in ("PWS", "ALPHA", "BOTH"):
        return organization
    raw = (entity_id or "").lower()
    if raw == "pws":
        return "PWS"
    if raw == "alpha":
        return "ALPHA"
    if raw == "both":
        return "BOTH"
    return "PWS"


def role_label_from_person(person: dict) -> str:
    kind = (person.get("kind") or "").lower()
    mapping = {
        "student": "Student",
        "player": "Player",
        "staff": "Staff",
        "coach": "Coach",
        "teacher": "Teacher",
    }
    if kind in mapping:
        return mapping[kind]
    return (kind or "Person").replace("_", " ").title()


def role_label_from_user(user: dict) -> str:
    from core import role_display
    from user_classification import resolve_user_type

    ut = resolve_user_type(user) or user.get("user_type")
    designation = (user.get("designation") or "").strip()
    if ut == "pws_admin" and designation:
        return designation.replace("_", " ").title()
    rd = role_display(user.get("role") or "", ut, designation or None)
    if " · " in rd:
        return rd.split(" · ", 1)[1]
    role = (user.get("role") or "").lower()
    mapping = {
        "super_admin": "Super Admin",
        "admin": "ALPHA Admin",
        "alpha_admin": "ALPHA Admin",
        "pws_admin": "PWS Admin",
        "pws_accounts": "PWS Accounts",
        "alpha_accounts": "ALPHA Accounts",
        "principal": "Principal",
        "vice_principal": "Vice Principal",
        "teacher": "Teacher",
        "pws_teacher": "Teacher",
        "coach": "Coach",
        "alpha_coach": "Coach",
        "warden": "Warden",
        "staff": "Staff",
    }
    return mapping.get(role, rd or "User")


def entity_from_person(person: dict) -> str:
    org = (person.get("organization") or "").upper()
    if org in ("PWS", "ALPHA", "BOTH"):
        return org
    kind = person.get("kind")
    if kind in ("student", "teacher"):
        return "PWS"
    if kind in ("player", "coach"):
        return "ALPHA"
    ents = person.get("entities") or []
    cleaned = [str(e).upper() for e in ents if str(e).upper() in ("PWS", "ALPHA")]
    if len(cleaned) == 2:
        return "BOTH"
    if cleaned:
        return cleaned[0]
    return "PWS"


def entity_from_user(user: dict) -> str:
    org = (user.get("organization") or user.get("entity_scope") or "").upper()
    if org in ("PWS", "ALPHA", "BOTH"):
        return org
    from user_classification import organization_for_user_type, resolve_user_type

    ut = resolve_user_type(user)
    if ut:
        o = organization_for_user_type(ut)
        return o if o in ("PWS", "ALPHA", "BOTH") else "PWS"
    role = (user.get("role") or "").lower()
    if role in ("admin", "coach", "alpha_accounts", "alpha_coach"):
        return "ALPHA"
    return "PWS"


def infer_target_role(doc: dict) -> Optional[str]:
    payload = doc.get("payload") or {}
    if payload.get("target_role"):
        return payload["target_role"]
    raw_type = doc.get("type") or ""
    if raw_type == "student_deactivation":
        return "Student"
    if raw_type == "player_deactivation":
        return "Player"
    target_kind = payload.get("target_kind")
    if target_kind:
        return role_label_from_person({"kind": target_kind})
    return None


def build_details(doc: dict) -> Dict[str, Any]:
    payload = doc.get("payload") or {}
    cat = category_for_type(doc.get("type") or "")
    details: Dict[str, Any] = dict(payload)
    details["reason"] = doc.get("reason")
    if cat == "fee_concession":
        details.setdefault("discount_amount", payload.get("discount_amount"))
        details.setdefault("subject_name", payload.get("person_name") or doc.get("subject_label"))
    if cat == "fee_edit":
        details.setdefault("previous_amount_due", payload.get("previous_amount_due"))
        details.setdefault("new_amount_due", payload.get("new_amount_due"))
        details.setdefault("subject_name", payload.get("person_name") or doc.get("subject_label"))
    if cat == "refund":
        details.setdefault("amount", payload.get("amount"))
        details.setdefault("invoice_id", payload.get("invoice_id"))
    if cat == "user_deactivation":
        details.setdefault("person_id", payload.get("person_id"))
        details.setdefault("user_id", payload.get("user_id"))
    return details


def enrich_approval(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k != "_id"}
    out.setdefault("history", [])
    out.setdefault("comments", [])
    raw_type = out.get("type") or ""
    category = category_for_type(raw_type)
    entity = entity_label(out.get("entity_id"), out.get("organization"))
    target_role = infer_target_role(out)
    out.update({
        "category": category,
        "target_user_role": target_role,
        "target_user_name": out.get("subject_label") or out.get("target_user_name") or "",
        "entity": entity,
        "requested_by": out.get("requested_by_name") or "",
        "details": build_details(out),
        "created_at": out.get("requested_at") or out.get("created_at"),
    })
    return out
