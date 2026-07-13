"""Central entity branding for fee payment receipts (modal + PDF).

Entity identity must always be resolved from persisted fee/payment records — never
from the logged-in user or client UI state alone.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from pymongo import ReturnDocument

logger = logging.getLogger(__name__)

BRAND_ASSETS_DIR = Path(__file__).resolve().parent / "brand_assets"

ENTITY_RECEIPT_BRANDING: Dict[str, Dict[str, Any]] = {
    "pws": {
        "entity_code": "PWS",
        "display_name": "Prarambhika World School",
        "short_name": "PWS",
        "address_lines": ["Balua Ahmedpur", "Patna 801113"],
        "logo_filename": "prarambhika-world-school-logo.png",
        "logo_alt": "Prarambhika World School logo",
        "receipt_prefix": "PWS",
        "receipt_title": "Fee Payment Receipt",
    },
    "alpha": {
        "entity_code": "ALPHA",
        "display_name": "ALPHA Sports Academy",
        "short_name": "ALPHA",
        "address_lines": [],
        "logo_filename": "alpha-sports-logo.png",
        "logo_alt": "ALPHA Sports Academy logo",
        "receipt_prefix": "ALPHA",
        "receipt_title": "Fee Payment Receipt",
    },
}

FEE_HEAD_LABELS: Dict[str, Dict[str, str]] = {
    "Registration": {"default": "Registration Fee"},
    "Monthly": {"pws": "Monthly Tuition Fee", "alpha": "Monthly Coaching Fee", "default": "Monthly Fee"},
    "Hostel": {"default": "Hostel Fee"},
    "Transport": {"default": "Transport Fee"},
    "Exam": {"default": "Exam Fee"},
    "Uniform": {"default": "Uniform Fee"},
    "Kit": {"default": "Kit Fee"},
    "Tournament": {"default": "Tournament Fee"},
    "Books": {"default": "Books Fee"},
    "Event": {"default": "Event Fee"},
    "Other": {"default": "Other Fee"},
}


def normalize_entity_id(entity_id: Optional[str]) -> Optional[str]:
    if not entity_id:
        return None
    eid = str(entity_id).strip().lower()
    if eid in ("pws", "alpha"):
        return eid
    logger.warning("Unknown entity_id %r — branding fallback may apply", entity_id)
    return None


def infer_entity_id_from_fee(fee: dict) -> Optional[str]:
    """Resolve entity from a single fee row (including legacy rows without entity_id)."""
    eid = normalize_entity_id(fee.get("entity_id"))
    if eid:
        return eid
    if fee.get("student_id"):
        return "pws"
    org = (fee.get("organization") or "").upper()
    if org == "PWS":
        return "pws"
    if org == "ALPHA":
        return "alpha"
    category = fee.get("category") or ""
    if category in ("Hostel", "Day Scholar") and not fee.get("sport"):
        return "pws"
    return None


def _derive_person_entities(person: dict) -> List[str]:
    """Lightweight copy of core.derive_person_entities (no DB dependency)."""
    raw = person.get("entities") or []
    cleaned = sorted({str(e).upper() for e in raw if str(e).upper() in ("PWS", "ALPHA")})
    if cleaned:
        return cleaned
    org = (person.get("organization") or "").upper()
    if org == "BOTH":
        return ["PWS", "ALPHA"]
    kind = person.get("kind")
    if kind in ("student", "teacher"):
        return ["PWS"] if org in ("", "PWS", "BOTH") else [org]
    if kind in ("player", "coach"):
        return ["ALPHA"] if org in ("", "ALPHA", "BOTH") else [org]
    if org in ("PWS", "ALPHA"):
        return [org]
    return ["PWS"]


def entity_id_from_person(player: dict) -> str:
    """Resolve entity from a person record (server-side)."""
    kind = player.get("kind")
    org = (player.get("organization") or "").upper()
    ents = _derive_person_entities(player)

    if kind == "student":
        return "pws"
    if kind == "player":
        return "alpha"
    if "PWS" in ents and "ALPHA" not in ents:
        return "pws"
    if "ALPHA" in ents and "PWS" not in ents:
        return "alpha"
    if org == "PWS":
        return "pws"
    if org == "ALPHA":
        return "alpha"
    return "pws" if "PWS" in ents else "alpha"


def entity_id_from_fee_batch(fees: List[dict], player: Optional[dict] = None) -> str:
    """Resolve entity from persisted fee rows. Raises ValueError if ambiguous."""
    if not fees:
        if player:
            return entity_id_from_person(player)
        raise ValueError("Cannot resolve entity from empty fee batch")
    ids = set()
    for fee in fees:
        eid = infer_entity_id_from_fee(fee)
        if eid:
            ids.add(eid)
    if len(ids) > 1:
        raise ValueError("Fee batch spans multiple entities")
    if len(ids) == 1:
        resolved = ids.pop()
        if player:
            expected = entity_id_from_person(player)
            if resolved != expected:
                raise ValueError(
                    f"Fee batch entity {resolved!r} does not match person entity {expected!r}"
                )
        return resolved
    if player:
        return entity_id_from_person(player)
    logger.error("Cannot resolve entity: fees lack entity_id and no player context")
    raise ValueError("Cannot resolve entity for fee batch")


def resolve_entity_branding(entity_id: Optional[str], *, allow_fallback: bool = True) -> Dict[str, Any]:
    """Return branding config for a validated entity id."""
    eid = normalize_entity_id(entity_id)
    if eid and eid in ENTITY_RECEIPT_BRANDING:
        branding = dict(ENTITY_RECEIPT_BRANDING[eid])
        branding["entity_id"] = eid
        branding["logo_path"] = str(BRAND_ASSETS_DIR / branding["logo_filename"])
        return branding
    if allow_fallback:
        logger.error("Missing/unknown entity_id %r — using ALPHA branding fallback", entity_id)
        return resolve_entity_branding("alpha", allow_fallback=False)
    raise KeyError(f"No branding for entity {entity_id!r}")


def branding_for_receipt_response(entity_id: str) -> Dict[str, Any]:
    """Trusted branding payload returned to clients after payment."""
    branding = resolve_entity_branding(entity_id)
    return {
        "entityCode": branding["entity_code"],
        "entityId": branding.get("entity_id", entity_id),
        "displayName": branding["display_name"],
        "shortName": branding["short_name"],
        "addressLines": list(branding.get("address_lines") or []),
        "receiptTitle": branding["receipt_title"],
        "receiptPrefix": branding["receipt_prefix"],
        "logoAlt": branding["logo_alt"],
    }


def fee_head_label(fee_type: str, entity_id: str) -> str:
    key = (fee_type or "").strip()
    if not key:
        return "Fee"
    mapped = FEE_HEAD_LABELS.get(key, {})
    if entity_id == "pws":
        return mapped.get("pws") or mapped.get("default") or f"{key} Fee"
    return mapped.get("alpha") or mapped.get("default") or f"{key} Fee"


def format_receipt_number_display(receipt_number: Optional[str], entity_id: str) -> str:
    if not receipt_number:
        return "—"
    num = str(receipt_number).strip()
    # Legacy invoice-engine receipts used RCP-PREFIX-YYYY-NNNN
    if num.startswith("RCP-"):
        num = num[4:]
    prefix = "PWS" if entity_id == "pws" else "ALPHA"
    if num.upper().startswith(f"{prefix}-"):
        return num
    return num


async def next_legacy_fee_receipt_number(entity_id: str) -> str:
    """Entity-specific legacy fee receipt sequence: PWS-YYYY-NNNNNN / ALPHA-YYYY-NNNNNN."""
    from core import db, now_utc

    eid = normalize_entity_id(entity_id) or "alpha"
    branding = resolve_entity_branding(eid, allow_fallback=False)
    year = now_utc().year
    prefix = branding["receipt_prefix"]
    key = f"legacy_fee_receipt_{eid}_{year}"
    doc = await db.counters.find_one_and_update(
        {"_id": key},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    seq = int(doc.get("seq", 1))
    return f"{prefix}-{year}-{seq:06d}"


def validate_player_entity_match(player: dict, entity_id: str) -> None:
    """Ensure player belongs to the fee batch entity (server-side guard)."""
    expected = entity_id_from_person(player)
    if expected != entity_id:
        raise ValueError(
            f"Player entity {expected!r} does not match fee batch entity {entity_id!r}"
        )
