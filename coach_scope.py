"""Coach roster scope helpers — strict single-sport assignment (no DB imports)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Configured sport master (code → record). IDs are stable codes, not hard-coded numeric IDs.
COACH_SPORT_MASTER: Dict[str, Dict[str, str]] = {
    "cricket": {"id": "cricket", "code": "cricket", "name": "Cricket"},
    "football": {"id": "football", "code": "football", "name": "Football"},
}

SPORT_NAME_TO_CODE = {v["name"]: k for k, v in COACH_SPORT_MASTER.items()}
SPORT_CODE_TO_NAME = {k: v["name"] for k, v in COACH_SPORT_MASTER.items()}
ALLOWED_COACH_SPORT_NAMES = tuple(v["name"] for v in COACH_SPORT_MASTER.values())

ERR_SPORT_ACCESS = "You do not have access to players from this sport."
ERR_SPORT_REQUIRED = (
    "Your coach account requires one assigned sport before this action is available."
)
ERR_SPORT_ASSIGNMENT = (
    "Your coach account does not yet have a sport assigned. Please contact the Sports Admin."
)
ERR_MULTI_SPORT = "A coach can be assigned to exactly one sport (Cricket or Football)."


def is_coach_user(user: dict) -> bool:
    """True for legacy coach role, alpha_coach role, or canonical alpha_coach user type."""
    role = (user.get("role") or "").lower()
    if role in ("coach", "alpha_coach"):
        return True
    return user.get("user_type") == "alpha_coach"


def sport_record(name_or_code: Optional[str]) -> Optional[Dict[str, str]]:
    if not name_or_code:
        return None
    raw = str(name_or_code).strip()
    code = raw.lower()
    if code in COACH_SPORT_MASTER:
        return COACH_SPORT_MASTER[code]
    code_from_name = SPORT_NAME_TO_CODE.get(raw)
    if code_from_name:
        return COACH_SPORT_MASTER[code_from_name]
    return None


def coach_assignment_lists(user: dict) -> Tuple[List[str], List[str]]:
    centres = [c for c in (user.get("assigned_centres") or []) if c]
    sports = [s for s in (user.get("assigned_sports") or []) if s]
    if not sports and user.get("assigned_sport"):
        sports = [user["assigned_sport"]]
    centres = list(dict.fromkeys(centres))
    sports = list(dict.fromkeys(sports))
    return centres, sports


def assigned_sport_name(user: dict) -> Optional[str]:
    """Return the coach's single assigned sport name, or None if unset/ambiguous."""
    if not is_coach_user(user):
        return None
    status = user.get("sport_assignment_status")
    if status == "ambiguous":
        return None
    _, sports = coach_assignment_lists(user)
    if len(sports) == 1:
        return sports[0]
    if len(sports) > 1:
        return None
    return user.get("assigned_sport") or None


def normalize_coach_assignments(data: dict) -> dict:
    """Keep assigned_sport and assigned_sports aligned; enforce single sport for coaches."""
    role = data.get("role")
    if role != "coach" and "assigned_sports" not in data and "assigned_sport" not in data:
        return data
    sports = [s for s in (data.get("assigned_sports") or []) if s]
    if not sports and data.get("assigned_sport"):
        sports = [data["assigned_sport"]]
    sports = list(dict.fromkeys(sports))
    if role == "coach":
        if len(sports) > 1:
            raise ValueError(ERR_MULTI_SPORT)
        if len(sports) == 1:
            data["assigned_sport"] = sports[0]
            data["assigned_sports"] = [sports[0]]
            data["sport_assignment_status"] = "ok"
        else:
            data["assigned_sport"] = None
            data["assigned_sports"] = []
            if data.get("sport_assignment_status") != "ambiguous":
                data["sport_assignment_status"] = "required"
    else:
        data["assigned_sports"] = sports
        if len(sports) == 1:
            data["assigned_sport"] = sports[0]
        elif sports and data.get("assigned_sport") not in sports:
            data["assigned_sport"] = sports[0]
        elif not sports:
            data["assigned_sport"] = None
    return data


def resolve_coach_data_scope(user: dict) -> Dict[str, Any]:
    """Server-side coach data scope from authenticated user record."""
    is_coach = is_coach_user(user)
    if not is_coach:
        return {
            "is_coach": False,
            "entity_id": None,
            "assigned_sport": None,
            "assigned_sport_id": None,
            "assigned_centres": [],
            "sport_locked": False,
            "sport_assignment_status": None,
            "requires_sport_assignment": False,
        }
    centres, sports = coach_assignment_lists(user)
    status = user.get("sport_assignment_status") or ("ok" if len(sports) == 1 else "required")
    if len(sports) > 1:
        status = "ambiguous"
    sport_name = sports[0] if len(sports) == 1 else None
    rec = sport_record(sport_name) if sport_name else None
    return {
        "is_coach": True,
        "entity_id": "alpha",
        "assigned_sport": {"id": rec["id"], "name": rec["name"], "code": rec["code"]} if rec else None,
        "assigned_sport_id": rec["id"] if rec else None,
        "assigned_centres": centres,
        "sport_locked": bool(rec),
        "sport_assignment_status": status,
        "requires_sport_assignment": status in ("required", "ambiguous"),
    }


def coach_scope_metadata(user: dict) -> Dict[str, Any]:
    """Display-only scope block for coach-facing API responses."""
    scope = resolve_coach_data_scope(user)
    if not scope["is_coach"]:
        return {}
    return {
        "entity": "ALPHA",
        "assigned_sport": scope["assigned_sport"],
        "assigned_centres": scope["assigned_centres"],
        "sport_locked": scope["sport_locked"],
        "sport_assignment_status": scope["sport_assignment_status"],
    }


def assert_coach_sport_assigned(user: dict) -> str:
    """Raise ValueError with user-facing message if coach lacks exactly one sport."""
    if not is_coach_user(user):
        return ""
    scope = resolve_coach_data_scope(user)
    if scope["requires_sport_assignment"]:
        if scope["sport_assignment_status"] == "ambiguous":
            raise ValueError(ERR_SPORT_ASSIGNMENT)
        raise ValueError(ERR_SPORT_REQUIRED)
    sport = scope["assigned_sport"]
    if not sport:
        raise ValueError(ERR_SPORT_ASSIGNMENT)
    return sport["name"]


def validate_coach_sport_param(user: dict, sport: Optional[str], *, is_admin_fn) -> Optional[str]:
    """Validate client-supplied sport for coaches. Returns effective sport or None."""
    if is_admin_fn(user) or not is_coach_user(user):
        return sport
    assigned = assert_coach_sport_assigned(user)
    if not sport:
        return assigned
    if sport != assigned:
        raise PermissionError(ERR_SPORT_ACCESS)
    return sport


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
    if len(sports) == 1:
        q["sport"] = sports[0]
    else:
        q["sport"] = {"$in": sports}
    if centres:
        q["centre"] = {"$in": centres}
    return q


def coach_player_query_for_user(user: dict, *, include_deactivated: bool = False) -> dict:
    """Build roster filter for a coach user (single sport enforced when status is ok)."""
    centres, sports = coach_assignment_lists(user)
    if user.get("sport_assignment_status") == "ambiguous" or len(sports) > 1:
        return {"kind": "player", "id": {"$in": []}}
    if len(sports) == 1:
        return build_coach_player_query(centres, sports, include_deactivated=include_deactivated)
    if user.get("assigned_sport"):
        return build_coach_player_query(centres, [user["assigned_sport"]], include_deactivated=include_deactivated)
    return build_coach_player_query(centres, [], include_deactivated=include_deactivated)
