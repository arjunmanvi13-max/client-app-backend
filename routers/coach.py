"""Coach-specific endpoints — Centre → Sport → PlayerType → Skill grouping."""
from collections import defaultdict
from typing import Optional, List, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core import db, get_current_user, now_utc, coach_can, is_admin

router = APIRouter(prefix="/coach", tags=["coach"])

class CoachAttendanceIn(BaseModel):
    date: str
    slot: Literal["Morning", "Evening"]
    centre: Optional[Literal["Balua", "Harding Park"]] = None
    sport: Optional[Literal["Cricket", "Football"]] = None
    absent_player_ids: List[str] = []

from coach_scope import (
    coach_assignment_lists,
    coach_player_query_for_user,
    coach_scope_metadata,
    assert_coach_sport_assigned,
    validate_coach_sport_param,
    ERR_SPORT_ACCESS,
)


def _coach_assignment_lists(user: dict) -> tuple[list, list]:
    return coach_assignment_lists(user)


def _coach_visibility_filter(user: dict, include_deactivated: bool = False) -> dict:
    """Coach sees only players in assigned sport, optionally scoped by centre."""
    if is_admin(user):
        q: dict = {"kind": "player"}
        if not include_deactivated:
            q["status"] = {"$ne": "deactivated"}
        return q
    return coach_player_query_for_user(user, include_deactivated=include_deactivated)


def _apply_coach_sport_param(user: dict, sport: Optional[str]) -> Optional[str]:
    try:
        return validate_coach_sport_param(user, sport, is_admin_fn=is_admin)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(403, str(e)) from e


def _assert_coach_can_access_players(user: dict) -> None:
    if is_admin(user) or user.get("role") != "coach":
        return
    try:
        assert_coach_sport_assigned(user)
    except ValueError as e:
        msg = str(e)
        code = 409 if "does not yet have" in msg else 403
        raise HTTPException(code, msg)


async def assert_player_in_coach_roster(user: dict, player_id: str) -> None:
    """Raise 403/404 if player is outside the coach's assigned centre/sport scope."""
    if is_admin(user):
        person = await db.people.find_one({"id": player_id, "kind": "player"}, {"_id": 0, "id": 1})
        if not person:
            raise HTTPException(404, "Player not found")
        return
    _assert_coach_can_access_players(user)
    filt = {**_coach_visibility_filter(user), "id": player_id}
    person = await db.people.find_one(filt, {"_id": 0, "id": 1})
    if not person:
        raise HTTPException(404, "Person not found")

@router.get("/dashboard")
async def coach_dashboard(user: dict = Depends(get_current_user)):
    if user["role"] != "coach" and not is_admin(user):
        raise HTTPException(403, "Coach role required")
    _assert_coach_can_access_players(user)
    q = _coach_visibility_filter(user)
    players = await db.people.find(q, {"_id": 0}).to_list(2000)

    deact_q = _coach_visibility_filter(user, include_deactivated=True)
    deact_q["status"] = "deactivated"
    deactivated_players = await db.people.find(deact_q, {"_id": 0}).sort("name", 1).to_list(500)

    by_centre: dict = defaultdict(int)
    by_sport: dict = defaultdict(int)
    by_player_type: dict = defaultdict(int)
    by_slot: dict = defaultdict(int)
    by_skill: dict = defaultdict(int)
    for p in players:
        by_centre[p.get("centre") or "Unassigned"] += 1
        by_sport[p.get("sport") or "Unassigned"] += 1
        by_player_type[p.get("player_type") or "Unassigned"] += 1
        by_slot[p.get("slot") or "Unassigned"] += 1
        by_skill[p.get("skill_level") or "Unassigned"] += 1

    today = now_utc().strftime("%Y-%m-%d")
    today_records = await db.attendance.find(
        {"date": today, "kind": "player", "marked_by": user["id"]},
        {"_id": 0},
    ).to_list(2000)

    payload = {
        "total_players": len(players),
        "by_centre": dict(by_centre),
        "by_sport": dict(by_sport),
        "by_player_type": dict(by_player_type),
        "by_slot": dict(by_slot),
        "by_skill": dict(by_skill),
        "today": {
            "date": today,
            "marked": len(today_records),
            "present": sum(1 for r in today_records if r["status"] == "present"),
            "absent": sum(1 for r in today_records if r["status"] == "absent"),
        },
        "assigned_sport": user.get("assigned_sport"),
        "assigned_centres": user.get("assigned_centres", []),
        "assigned_sports": user.get("assigned_sports", []),
        "deactivated_players": deactivated_players,
        "deactivated_count": len(deactivated_players),
    }
    if user.get("role") == "coach":
        payload["scope"] = coach_scope_metadata(user)
    return payload

@router.get("/players")
async def coach_players(
    centre: Optional[Literal["Balua", "Harding Park"]] = None,
    sport: Optional[Literal["Cricket", "Football"]] = None,
    slot: Optional[Literal["Morning", "Evening"]] = None,
    user: dict = Depends(get_current_user),
):
    if not coach_can(user, "view"):
        raise HTTPException(403, "view_players permission required")
    _assert_coach_can_access_players(user)
    effective_sport = _apply_coach_sport_param(user, sport)
    q = _coach_visibility_filter(user)
    if centre:
        if user.get("role") == "coach":
            assigned_centres, _ = _coach_assignment_lists(user)
            if assigned_centres and centre not in assigned_centres:
                raise HTTPException(403, ERR_SPORT_ACCESS)
        q["centre"] = centre
    if effective_sport:
        q["sport"] = effective_sport
    if slot:
        q["slot"] = slot
    players = await db.people.find(q, {"_id": 0}).sort("name", 1).to_list(2000)

    grouped: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for p in players:
        c = p.get("centre") or "Unassigned"
        sp = p.get("sport") or "Unassigned"
        pt = p.get("player_type") or "Unassigned"
        grouped[c][sp][pt].append(p)
    out: dict = {}
    for c, by_sport_map in grouped.items():
        out[c] = {sp: dict(by_pt) for sp, by_pt in by_sport_map.items()}
    payload = {"total": len(players), "groups": out, "players": players}
    if user.get("role") == "coach":
        payload["scope"] = coach_scope_metadata(user)
    return payload

@router.post("/attendance")
async def coach_mark_attendance(payload: CoachAttendanceIn, user: dict = Depends(get_current_user)):
    from routers.parents import push_parent_notification
    from routers.attendance import upsert_attendance, normalize_session
    if user["role"] != "coach" and not is_admin(user):
        raise HTTPException(403, "Coach role required")
    _assert_coach_can_access_players(user)
    effective_sport = _apply_coach_sport_param(user, payload.sport)
    q = _coach_visibility_filter(user)
    q["slot"] = payload.slot
    if payload.centre:
        q["centre"] = payload.centre
    if effective_sport:
        q["sport"] = effective_sport
    players = await db.people.find(q, {"_id": 0}).to_list(2000)
    if not players:
        raise HTTPException(400, "No players found for that filter scope")
    roster_ids = {p["id"] for p in players}
    for pid in payload.absent_player_ids or []:
        if pid not in roster_ids:
            raise HTTPException(403, "Player is not in your assigned roster")
    absent_set = set(payload.absent_player_ids or [])
    today_str = now_utc().strftime("%Y-%m-%d")
    sess = normalize_session(None, slot=payload.slot, kind="player")
    for p in players:
        status = "absent" if p["id"] in absent_set else "present"
        await upsert_attendance(
            user,
            kind="player",
            person_id=p["id"],
            date=payload.date,
            status=status,
            session=sess,
            slot=payload.slot,
            entity_id="alpha",
            group=p.get("group"),
            sport=p.get("sport"),
            centre=p.get("centre"),
            source="coach_ui",
            extra={"player_type": p.get("player_type")},
        )
        if status == "absent" and payload.date == today_str:
            try:
                await push_parent_notification(
                    p["id"],
                    title=f"Absent — {payload.slot} session",
                    body=f"Your ward missed today's {payload.slot} {p.get('sport','')} session at {p.get('centre','')}.",
                    ntype="absent_today",
                )
            except Exception:
                pass
    return {"count": len(players), "present": len(players) - len(absent_set), "absent": len(absent_set)}

@router.get("/attendance")
async def coach_attendance_history(
    date: Optional[str] = None,
    slot: Optional[str] = None,
    centre: Optional[str] = None,
    sport: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    if user["role"] != "coach" and not is_admin(user):
        raise HTTPException(403, "Coach role required")
    _assert_coach_can_access_players(user)
    effective_sport = _apply_coach_sport_param(user, sport)
    q = {"kind": "player"}
    if user["role"] == "coach":
        q["marked_by"] = user["id"]
        roster_q = _coach_visibility_filter(user)
        roster_ids = await db.people.distinct("id", roster_q)
        q["person_id"] = {"$in": roster_ids}
    if date:
        q["date"] = date
    if slot:
        q["slot"] = slot
    if centre:
        q["centre"] = centre
    if effective_sport:
        q["sport"] = effective_sport
    rows = await db.attendance.find(q, {"_id": 0}).sort("date", -1).to_list(2000)
    if user.get("role") == "coach":
        return {"data": rows, "scope": coach_scope_metadata(user)}
    return rows

@router.get("/scope")
async def coach_scope(user: dict = Depends(get_current_user)):
    """Read-only coach data scope for UI (authorization remains server-side)."""
    if user.get("role") != "coach" and not is_admin(user):
        raise HTTPException(403, "Coach role required")
    return coach_scope_metadata(user)

@router.get("/centres")
async def list_centres(_user: dict = Depends(get_current_user)):
    """Static config — centre/sport/player-type rules."""
    return {
        "centres": [
            {"name": "Balua", "sports": ["Cricket", "Football"], "player_types": ["Daily", "Hostel"]},
            {"name": "Harding Park", "sports": ["Cricket", "Football"], "player_types": ["Daily"]},
        ],
    }
