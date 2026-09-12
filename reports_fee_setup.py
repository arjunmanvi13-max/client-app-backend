"""Fee Setup report — configured PWS/ALPHA fee details per student or player."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

IDENTITY_COLUMNS = [
    "Player/Student Name",
    "Unique ID",
    "Entity",
    "Campus / Location",
    "Sport / Grade",
    "Player Type",
    "Skill Level",
]
IDENTITY_KEYS = [
    "name",
    "unique_id",
    "entity_label",
    "campus",
    "sport_or_grade",
    "player_type",
    "skill_level",
]

# Display category in pws_fee_structure.FEE_CATEGORIES → row key
PWS_COMPONENTS: List[Tuple[str, str, str]] = [
    ("Registration", "registration", "Registration"),
    ("Admission Charges", "admission", "Admission Charges"),
    ("Security (Refundable)", "security", "Security (Refundable)"),
    ("Annual Charges", "annual", "Annual Charges"),
    ("Tuition", "tuition", "Tuition"),
    ("Physical Education", "physical_education", "Physical Education"),
    ("Exam Fee", "exam", "Exam Fee"),
    ("Transport", "transport", "Transport"),
]

ALPHA_CORE_COMPONENTS: List[Tuple[str, str]] = [
    ("registration", "Registration"),
    ("monthly", "Monthly Fee"),
    ("transport", "Transport"),
]
ALPHA_OPTIONAL_COMPONENTS: List[Tuple[str, str]] = [
    ("hostel", "Hostel"),
    ("exam", "Exam Fee"),
    ("uniform", "Uniform"),
    ("kit", "Kit"),
    ("tournament", "Tournament"),
]
ALPHA_RATE_KEY_MAP = {
    "registration": "registration",
    "monthly": "monthly",
    "transport": "transport",
    "hostel_monthly": "hostel",
    "exam": "exam",
}

TAIL_COLUMNS = ["Discounts / Waivers", "Net Payable Fee", "Billing Frequency"]
TAIL_KEYS = ["discounts", "net_payable", "billing_frequency"]

# Kept so older imports of the report module still resolve.
FEE_SETUP_COLUMNS = IDENTITY_COLUMNS + [label for _, _, label in PWS_COMPONENTS] + TAIL_COLUMNS
FEE_SETUP_KEYS = IDENTITY_KEYS + [key for _, key, _ in PWS_COMPONENTS] + TAIL_KEYS


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


def _discount_and_net(catalog: Dict[str, int], actual: Dict[str, int], keys: Sequence[str]) -> Tuple[int, int]:
    discounts = 0
    net = 0
    for key in keys:
        c = int(catalog.get(key) or 0)
        a = int(actual.get(key) or 0)
        discounts += max(0, c - a)
        net += a
    return discounts, net


def _pws_amounts(person: dict) -> Dict[str, Any]:
    from pws_fee_structure import pws_student_profile_from_person, resolve_category_amounts

    profile = pws_student_profile_from_person(person)
    catalog_raw = resolve_category_amounts(
        profile["pws_class"],
        profile["transport_enabled"],
        profile["transport_distance"],
        None,
    )
    actual_raw = resolve_category_amounts(
        profile["pws_class"],
        profile["transport_enabled"],
        profile["transport_distance"],
        profile.get("overrides") or {},
    )
    catalog: Dict[str, int] = {}
    actual: Dict[str, int] = {}
    for category, key, _label in PWS_COMPONENTS:
        catalog[key] = int(catalog_raw.get(category) or 0)
        actual[key] = int(actual_raw.get(category) or 0)
    keys = [key for _, key, _ in PWS_COMPONENTS]
    discounts, net = _discount_and_net(catalog, actual, keys)
    return {
        **actual,
        "discounts": discounts,
        "net_payable": net,
        "billing_frequency": "Mixed",
        "player_type": profile.get("pws_student_type") or "—",
        "sport_or_grade": profile.get("pws_class") or person.get("group") or "—",
        "campus": person.get("centre") or "PWS Campus",
        "skill_level": "—",
        "component_keys": keys,
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


def _alpha_catalog_components(catalog_rates: Optional[dict], resolved_items: Optional[list]) -> Dict[str, int]:
    catalog: Dict[str, int] = {}
    for rate_key, row_key in ALPHA_RATE_KEY_MAP.items():
        if catalog_rates and rate_key in catalog_rates:
            catalog[row_key] = int(catalog_rates.get(rate_key) or 0)
    for item in resolved_items or []:
        fee_type = (item.get("fee_type") or "").strip().lower()
        if fee_type in ("tuition", "coaching"):
            key = "monthly"
        elif fee_type == "examination":
            key = "exam"
        elif fee_type == "hostel":
            key = "hostel"
        elif fee_type in ("registration", "transport", "uniform", "kit", "tournament"):
            key = fee_type
        else:
            continue
        catalog[key] = int(item.get("effective_amount") if item.get("effective_amount") is not None else item.get("amount") or 0)
    return catalog


def _alpha_amounts(
    person: dict,
    catalog_rates: Optional[dict] = None,
    resolved_items: Optional[list] = None,
) -> Dict[str, Any]:
    from alpha_centre_rules import apply_defense_colony_rates_for_person

    category = _canonical_player_type(person.get("player_type") or "Daily")
    sport = person.get("sport") or ""
    hardcoded = (ALPHA_FEE_CARDS.get(category) or {}).get(sport) or {}
    rates = apply_defense_colony_rates_for_person(person, {**hardcoded, **(catalog_rates or {})})
    catalog = _alpha_catalog_components(rates, resolved_items)
    if "registration" not in catalog:
        catalog["registration"] = int(rates.get("registration") or 0)
    if "monthly" not in catalog:
        catalog["monthly"] = int(rates.get("monthly") or 0)
    if "transport" not in catalog:
        catalog["transport"] = int(rates.get("transport") or 0)

    actual = dict(catalog)
    if int(person.get("monthly_fee_override") or 0):
        actual["monthly"] = int(person.get("monthly_fee_override") or 0)
    elif not int(person.get("monthly_fee_override") or 0) and category in ("Hostel", "Hostel Only"):
        if int(person.get("hostel_fee_override") or 0):
            actual["monthly"] = int(person.get("hostel_fee_override") or 0)
    if int(person.get("registration_fee_override") or 0):
        actual["registration"] = int(person.get("registration_fee_override") or 0)
    if person.get("transport_fee_monthly") not in (None, ""):
        actual["transport"] = int(person.get("transport_fee_monthly") or 0)
    if int(person.get("hostel_fee_override") or 0) and "hostel" in catalog:
        actual["hostel"] = int(person.get("hostel_fee_override") or 0)

    keys = [key for key, _ in ALPHA_CORE_COMPONENTS]
    for key, _label in ALPHA_OPTIONAL_COMPONENTS:
        if catalog.get(key) or actual.get(key):
            if key not in keys:
                keys.append(key)
            actual.setdefault(key, 0)
            catalog.setdefault(key, 0)
    for key in keys:
        actual.setdefault(key, 0)
        catalog.setdefault(key, 0)

    discounts, net = _discount_and_net(catalog, actual, keys)
    out = {
        **{k: int(actual.get(k) or 0) for k in keys},
        "discounts": discounts,
        "net_payable": net,
        "billing_frequency": "Monthly",
        "player_type": person.get("player_type") or category or "—",
        "sport_or_grade": sport or "—",
        "campus": person.get("centre") or "—",
        "skill_level": person.get("skill_level") or "—",
        "component_keys": keys,
    }
    return out


def build_fee_setup_row(
    person: dict,
    catalog_rates: Optional[dict] = None,
    resolved_items: Optional[list] = None,
) -> dict:
    if person.get("kind") == "student":
        amounts = _pws_amounts(person)
        entity = "PWS"
    else:
        amounts = _alpha_amounts(person, catalog_rates, resolved_items)
        entity = "ALPHA"
    row = {
        "name": person.get("name") or "—",
        "unique_id": _unique_id(person),
        "entity_label": entity,
        "campus": amounts["campus"],
        "sport_or_grade": amounts["sport_or_grade"],
        "player_type": amounts["player_type"],
        "skill_level": amounts["skill_level"],
        "discounts": amounts["discounts"],
        "net_payable": amounts["net_payable"],
        "billing_frequency": amounts["billing_frequency"],
        "component_keys": amounts["component_keys"],
    }
    for key in amounts["component_keys"]:
        row[key] = int(amounts.get(key) or 0)
    return row


def fee_setup_columns_and_keys(entity: str, component_keys: Sequence[str]) -> Tuple[List[str], List[str]]:
    labels = {key: label for key, label in ALPHA_CORE_COMPONENTS + ALPHA_OPTIONAL_COMPONENTS}
    for _category, key, label in PWS_COMPONENTS:
        labels[key] = label
    columns = list(IDENTITY_COLUMNS)
    keys = list(IDENTITY_KEYS)
    for key in component_keys:
        columns.append(labels.get(key, key.replace("_", " ").title()))
        keys.append(key)
    columns.extend(TAIL_COLUMNS)
    keys.extend(TAIL_KEYS)
    return columns, keys


def fee_setup_total_row(meta: dict) -> List[Any]:
    keys = meta.get("row_keys") or []
    sm = meta.get("summary") or {}
    money = set(sm.get("money_keys") or [])
    totals = sm.get("component_totals") or {}
    row: List[Any] = []
    for key in keys:
        if key == "name":
            row.append("TOTAL")
        elif key == "discounts":
            row.append(sm.get("total_discounts", 0))
        elif key == "net_payable":
            row.append(sm.get("total_net_payable", 0))
        elif key in totals:
            row.append(totals[key])
        elif key in money:
            row.append(sm.get(f"total_{key}", 0))
        else:
            row.append("")
    return row


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


def _ordered_component_keys(entity: str, seen: Sequence[str]) -> List[str]:
    if entity == "PWS":
        order = [key for _, key, _ in PWS_COMPONENTS]
    else:
        order = [key for key, _ in ALPHA_CORE_COMPONENTS + ALPHA_OPTIONAL_COMPONENTS]
    extra = [key for key in seen if key not in order]
    present = set(seen)
    return [key for key in order if key in present] + extra


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
    seen_components: List[str] = []
    if scope == "PWS":
        seen_components = [key for _, key, _ in PWS_COMPONENTS]
    else:
        seen_components = [key for key, _ in ALPHA_CORE_COMPONENTS]

    for person in people:
        catalog = None
        resolved_items = None
        if person.get("kind") == "player":
            try:
                from routers.fee_catalog import find_plan_for_person, resolve_rates_for_person

                catalog = await resolve_rates_for_person(person)
                plan = await find_plan_for_person(person)
                if plan:
                    resolved_items = plan.get("resolved_items") or []
            except Exception:
                catalog = None
                resolved_items = None
        row = build_fee_setup_row(person, catalog, resolved_items)
        for key in row.get("component_keys") or []:
            if key not in seen_components:
                seen_components.append(key)
        rows.append(row)

    component_keys = _ordered_component_keys(scope, seen_components)
    columns, keys = fee_setup_columns_and_keys(scope, component_keys)
    money_keys = [*component_keys, "discounts", "net_payable"]
    component_totals = {key: 0 for key in component_keys}
    total_disc = total_net = 0
    cleaned = []
    money = set(money_keys)
    for row in rows:
        for key in component_keys:
            component_totals[key] += int(row.get(key) or 0)
        total_disc += int(row.get("discounts") or 0)
        total_net += int(row.get("net_payable") or 0)
        cleaned.append({k: int(row.get(k) or 0) if k in money else (row.get(k) if row.get(k) not in (None, "") else "—") for k in keys})

    summary = {
        "total_rows": len(cleaned),
        "total_discounts": total_disc,
        "total_net_payable": total_net,
        "money_keys": money_keys,
        "component_totals": component_totals,
        "total_registration": component_totals.get("registration", 0),
        "total_base_fee": component_totals.get("tuition") or component_totals.get("monthly") or 0,
    }
    return build_meta(
        "fee-setup",
        "Fee Setup",
        user,
        scope,
        filters,
        columns,
        cleaned,
        summary,
        row_keys=keys,
    )
