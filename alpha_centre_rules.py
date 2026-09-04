"""ALPHA campus-specific fee and session rules that must not leak to other centres."""
from __future__ import annotations

from typing import Any, Optional

DEFENSE_COLONY_CENTRE = "Defense Colony"
DEFENSE_COLONY_REGISTRATION = 7500
DEFENSE_COLONY_MONTHLY_BY_SKILL = {
    "Beginner": 3500,
    "Intermediate": 3500,
    "Advanced": 8000,
}
DEFENSE_COLONY_ADVANCED_SLOT_ERROR = (
    "Defense Colony Advanced players can only be assigned to the Morning session"
)


def defense_colony_fee_rates(skill_level: Optional[str] = None) -> dict:
    monthly = DEFENSE_COLONY_MONTHLY_BY_SKILL.get(skill_level or "")
    if monthly is None:
        monthly = DEFENSE_COLONY_MONTHLY_BY_SKILL["Beginner"]
    return {"registration": DEFENSE_COLONY_REGISTRATION, "monthly": monthly}


def apply_defense_colony_rates(
    rates: Optional[dict],
    centre: Optional[str] = None,
    skill_level: Optional[str] = None,
) -> dict:
    """Overlay Defense Colony skill-based defaults onto a rate-card or catalogue result."""
    out = dict(rates or {})
    if (centre or "") != DEFENSE_COLONY_CENTRE:
        return out
    out.update(defense_colony_fee_rates(skill_level))
    return out


def defense_colony_advanced_slot_error(
    centre: Optional[str],
    skill_level: Optional[str],
    slot: Optional[str],
) -> Optional[str]:
    if (centre or "") != DEFENSE_COLONY_CENTRE:
        return None
    if (skill_level or "") != "Advanced":
        return None
    if slot and slot != "Morning":
        return DEFENSE_COLONY_ADVANCED_SLOT_ERROR
    return None


def apply_defense_colony_rates_for_person(person: dict[str, Any], rates: Optional[dict]) -> dict:
    return apply_defense_colony_rates(
        rates,
        person.get("centre"),
        person.get("skill_level"),
    )
