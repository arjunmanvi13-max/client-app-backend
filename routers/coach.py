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

def _coach_visibility_filter(user: dict, include_deactivated: bool = False) -> dict:
    """Coach sees only players in their assigned centres + sports. Admin sees all."""
    q: dict = {"kind": "player"}
    if not include_deactivated:
        q["status"] = {"$ne": "deactivated"}
    if is_admin(user):
        return q
    centres = user.get("assigned_centres") or []
    sports = user.get("assigned_sports") or []
    if centres:
        q["centre"] = {"$in": centres}
    if sports:
        q["sport"] = {"$in": sports}
    return q

@router.get("/dashboard")
async def coach_dashboard(user: dict = Depends(get_current_user)):
    if user["role"] != "coach" and not is_admin(user):
        raise HTTPException(403, "Coach role required")
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

    return {
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

@router.get("/players")
async def coach_players(
    centre: Optional[Literal["Balua", "Harding Park"]] = None,
    sport: Optional[Literal["Cricket", "Football"]] = None,
    slot: Optional[Literal["Morning", "Evening"]] = None,
    user: dict = Depends(get_current_user),
):
    if not coach_can(user, "view"):
        raise HTTPException(403, "view_players permission required")
    q = _coach_visibility_filter(user)
    if centre: q["centre"] = centre
    if sport: q["sport"] = sport
    if slot: q["slot"] = slot
    players = await db.people.find(q, {"_id": 0}).sort("name", 1).to_list(2000)

    # Centre → Sport → PlayerType → [players]
    grouped: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for p in players:
        c = p.get("centre") or "Unassigned"
        sp = p.get("sport") or "Unassigned"
        pt = p.get("player_type") or "Unassigned"
        grouped[c][sp][pt].append(p)
    out: dict = {}
    for c, by_sport_map in grouped.items():
        out[c] = {sp: dict(by_pt) for sp, by_pt in by_sport_map.items()}
    return {"total": len(players), "groups": out, "players": players}

@router.post("/attendance")
async def coach_mark_attendance(payload: CoachAttendanceIn, user: dict = Depends(get_current_user)):
    from routers.parents import push_parent_notification
    if user["role"] != "coach" and not is_admin(user):
        raise HTTPException(403, "Coach role required")
    q = _coach_visibility_filter(user)
    q["slot"] = payload.slot
    if payload.centre: q["centre"] = payload.centre
    if payload.sport: q["sport"] = payload.sport
    players = await db.people.find(q, {"_id": 0}).to_list(2000)
    if not players:
        raise HTTPException(400, "No players found for that filter scope")
    absent_set = set(payload.absent_player_ids or [])
    today_str = now_utc().strftime("%Y-%m-%d")
    for p in players:
        status = "absent" if p["id"] in absent_set else "present"
        rec = {
            "date": payload.date,
            "kind": "player",
            "slot": payload.slot,
            "centre": p.get("centre"),
            "sport": p.get("sport"),
            "player_type": p.get("player_type"),
            "session": payload.slot,
            "group": p.get("group"),
            "person_id": p["id"],
            "status": status,
            "marked_by": user["id"],
            "marked_by_name": user["name"],
            "created_at": now_utc().isoformat(),
        }
        await db.attendance.update_one(
            {"date": payload.date, "kind": "player", "slot": payload.slot, "person_id": p["id"]},
            {"$set": rec},
            upsert=True,
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
    q = {"kind": "player"}
    if user["role"] == "coach":
        q["marked_by"] = user["id"]
    if date: q["date"] = date
    if slot: q["slot"] = slot
    if centre: q["centre"] = centre
    if sport: q["sport"] = sport
    return await db.attendance.find(q, {"_id": 0}).sort("date", -1).to_list(2000)

@router.get("/centres")
async def list_centres(_user: dict = Depends(get_current_user)):
    """Static config — centre/sport/player-type rules."""
    return {
        "centres": [
            {"name": "Balua", "sports": ["Cricket", "Football"], "player_types": ["Daily", "Hostel"]},
            {"name": "Harding Park", "sports": ["Cricket", "Football"], "player_types": ["Daily"]},
        ],
    }
