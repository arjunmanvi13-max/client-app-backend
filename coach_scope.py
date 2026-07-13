"""Coach roster scope helpers — sport/centre assignment (no DB imports)."""
from __future__ import annotations

from typing import List, Tuple


def coach_assignment_lists(user: dict) -> Tuple[List[str], List[str]]:
    centres = [c for c in (user.get("assigned_centres") or []) if c]
    sports = [s for s in (user.get("assigned_sports") or []) if s]
    if not sports and user.get("assigned_sport"):
        sports = [user["assigned_sport"]]
    centres = list(dict.fromkeys(centres))
    sports = list(dict.fromkeys(sports))
    return centres, sports


def normalize_coach_assignments(data: dict) -> dict:
    """Keep assigned_sport and assigned_sports aligned for coach accounts."""
    role = data.get("role")
    if role != "coach" and "assigned_sports" not in data and "assigned_sport" not in data:
        return data
    sports = [s for s in (data.get("assigned_sports") or []) if s]
    if not sports and data.get("assigned_sport"):
        sports = [data["assigned_sport"]]
    data["assigned_sports"] = sports
    if len(sports) == 1:
        data["assigned_sport"] = sports[0]
    elif sports and data.get("assigned_sport") not in sports:
        data["assigned_sport"] = sports[0]
    elif not sports:
        data["assigned_sport"] = None
    return data


def build_coach_player_query(
    centres: List[str],
    sports: List[str],
    *,
    include_deactivated: bool = False,
) -> dict:
    """Build Mongo filter for coach-visible players (sport scope is mandatory)."""
    q: dict = {"kind": "player"}
    if not include_deactivated:
        q["status"] = {"$ne": "deactivated"}
    if not sports:
        q["id"] = {"$in": []}
        return q
    q["sport"] = {"$in": sports}
    if centres:
        q["centre"] = {"$in": centres}
    return q
