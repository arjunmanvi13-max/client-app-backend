"""Fee Setup report — configured PWS/ALPHA fee details per student or player."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

FEE_SETUP_COLUMNS = [
    "Player/Student Name",
    "Unique ID",
    "Entity",
    "Campus / Location",
    "Sport / Grade",
    "Player Type",
    "Skill Level",
    "Base Fee Amount",
    "Registration Fee",
    "Discounts / Waivers",
    "Net Payable Fee",
    "Billing Frequency",
]

FEE_SETUP_KEYS = [
    "name",
    "unique_id",
    "entity_label",
    "campus",
    "sport_or_grade",
    "player_type",
    "skill_level",
    "base_fee",
    "registration_fee",
    "discounts",
    "net_payable",
    "billing_frequency",
]


def _status_query(status: Optional[str]) -> Optional[Any]:
    raw = (status or "").strip().lower()
    if not raw or raw == "all":
        return None
    if raw in ("inactive", "deactivated"):
        return {"$in": ["deactivated", "inactive"]}
    if raw == "active":
        return "active"
    return raw


def _unique_id(person: dict) -> str:
    return (
        person.get("admission_number")
        or person.get("player_id")
        or person.get("roll_number")
        or person.get("id")
        or "—"
    )


def _pws_amounts(person: dict) -> Dict[str, Any]:
    from pws_fee_structure import pws_student_profile_from_person, resolve_category_amounts

    profile = pws_student_profile_from_person(person)
    catalog = resolve_category_amounts(
        profile["pws_class"],
        profile["transport_enabled"],
        profile["transport_distance"],
        None,
    )
    actual = resolve_category_amounts(
        profile["pws_class"],
        profile["transport_enabled"],
        profile["transport_distance"],
        profile.get("overrides") or {},
    )
    catalog_base = int(catalog.get("Tuition") or 0)
    catalog_reg = int(catalog.get("Registration") or 0)
    actual_base = int(actual.get("Tuition") or 0)
    actual_reg = int(actual.get("Registration") or 0)
    discounts = max(0, catalog_base - actual_base) + max(0, catalog_reg - actual_reg)
    return {
        "base_fee": actual_base,
        "registration_fee": actual_reg,
        "discounts": discounts,
        "net_payable": actual_base + actual_reg,
        "billing_frequency": "Monthly",
        "player_type": profile.get("pws_student_type") or "—",
        "sport_or_grade": profile.get("pws_class") or person.get("group") or "—",
        "campus": person.get("centre") or "PWS Campus",
        "skill_level": "—",
    }


# Mirrors routers.fees.RATE_CARDS so this module can load without Mongo.
ALPHA_FEE_CARDS = {
    "Daily": {
        "Cricket": {"registration": 3000, "monthly": 2500},
        "Football": {"registration": 3000, "monthly": 2000},
    },
    "Hostel Only": {
        "Cricket": {"registration": 3000, "monthly": 12000},
        "Football": {"registration": 3000, "monthly": 15000},
    },
    "Hostel": {
        "Cricket": {"registration": 3000, "monthly": 12000},
        "Football": {"registration": 3000, "monthly": 15000},
    },
    "Day Boarding": {
        "Cricket": {"registration": 3000, "monthly": 7500},
        "Football": {"registration": 3000, "monthly": 7500},
    },
    "Boarding": {
        "Cricket": {"registration": 20000, "monthly": 15000},
        "Football": {"registration": 20000, "monthly": 15000},
    },
}


def _canonical_player_type(category: str) -> str:
    if category == "Hostel":
        return "Hostel Only"
    return category or "Daily"


def _alpha_amounts(person: dict, catalog_rates: Optional[dict] = None) -> Dict[str, Any]:
    from alpha_centre_rules import apply_defense_colony_rates_for_person

    category = _canonical_player_type(person.get("player_type") or "Daily")
    sport = person.get("sport") or ""
    hardcoded = (ALPHA_FEE_CARDS.get(category) or {}).get(sport) or {}
    rates = apply_defense_colony_rates_for_person(person, {**hardcoded, **(catalog_rates or {})})
    catalog_base = int(rates.get("monthly") or 0)
    catalog_reg = int(rates.get("registration") or 0)
    actual_base = int(person.get("monthly_fee_override") or 0) or catalog_base
    if not int(person.get("monthly_fee_override") or 0) and category in ("Hostel", "Hostel Only"):
        actual_base = int(person.get("hostel_fee_override") or 0) or actual_base
    actual_reg = int(person.get("registration_fee_override") or 0) or catalog_reg
    discounts = max(0, catalog_base - actual_base) + max(0, catalog_reg - actual_reg)
    return {
        "base_fee": actual_base,
        "registration_fee": actual_reg,
        "discounts": discounts,
        "net_payable": actual_base + actual_reg,
        "billing_frequency": "Monthly",
        "player_type": person.get("player_type") or category or "—",
        "sport_or_grade": sport or "—",
        "campus": person.get("centre") or "—",
        "skill_level": person.get("skill_level") or "—",
    }


def build_fee_setup_row(person: dict, catalog_rates: Optional[dict] = None) -> dict:
    if person.get("kind") == "student":
        amounts = _pws_amounts(person)
        entity = "PWS"
    else:
        amounts = _alpha_amounts(person, catalog_rates)
        entity = "ALPHA"
    return {
        "name": person.get("name") or "—",
        "unique_id": _unique_id(person),
        "entity_label": entity,
        "campus": amounts["campus"],
        "sport_or_grade": amounts["sport_or_grade"],
        "player_type": amounts["player_type"],
        "skill_level": amounts["skill_level"],
        "base_fee": amounts["base_fee"],
        "registration_fee": amounts["registration_fee"],
        "discounts": amounts["discounts"],
        "net_payable": amounts["net_payable"],
        "billing_frequency": amounts["billing_frequency"],
    }


def fee_setup_people_query(entity: str, centre: Optional[str], status: Optional[str]) -> dict:
    from core import person_entity_filter

    kind = "student" if entity == "PWS" else "player"
    q: dict = {"kind": kind}
    ent_f = person_entity_filter(entity)
    if ent_f:
        q = {"$and": [q, ent_f]}
    if centre and centre.lower() != "all":
        q["centre"] = centre
    st = _status_query(status)
    if st is not None:
        q["status"] = st
    return q


async def run_fee_setup(user: dict, entity: str, filters: dict) -> dict:
    from core import db
    from reports_engine import build_meta

    scope = entity if entity in ("PWS", "ALPHA") else "PWS"
    q = fee_setup_people_query(scope, filters.get("centre"), filters.get("status"))
    ids_raw = (filters.get("person_ids") or "").strip()
    if ids_raw:
        ids = [x.strip() for x in ids_raw.split(",") if x.strip()]
        if ids:
            q["id"] = {"$in": ids}

    people = await db.people.find(q, {"_id": 0}).sort("name", 1).to_list(4000)
    rows = []
    total_base = total_reg = total_disc = total_net = 0
    for person in people:
        catalog = None
        if person.get("kind") == "player":
            try:
                from routers.fee_catalog import resolve_rates_for_person
                catalog = await resolve_rates_for_person(person)
            except Exception:
                catalog = None
        row = build_fee_setup_row(person, catalog)
        rows.append(row)
        total_base += int(row["base_fee"] or 0)
        total_reg += int(row["registration_fee"] or 0)
        total_disc += int(row["discounts"] or 0)
        total_net += int(row["net_payable"] or 0)

    summary = {
        "total_rows": len(rows),
        "total_base_fee": total_base,
        "total_registration": total_reg,
        "total_discounts": total_disc,
        "total_net_payable": total_net,
    }
    return build_meta(
        "fee-setup",
        "Fee Setup",
        user,
        scope,
        filters,
        FEE_SETUP_COLUMNS,
        rows,
        summary,
        row_keys=FEE_SETUP_KEYS,
    )
