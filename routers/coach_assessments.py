"""Coach assessment MVP — ALPHA player ratings, scores, remarks."""
import uuid
from typing import Optional, List, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from core import db, get_current_user, get_perm, is_admin, is_super_admin, now_utc
from routers.coach import _coach_visibility_filter

router = APIRouter(prefix="/coach-assessments", tags=["coach-assessments"])

ENTITY_ALPHA = "alpha"
ASSESSMENT_TYPES = ("rating", "score", "test")


def _can_enter(user: dict) -> bool:
    return is_admin(user) or get_perm(user, "enter_coach_assessments")


def _can_manage(user: dict) -> bool:
    return is_super_admin(user) or get_perm(user, "manage_coach_assessments")


def _assert_enter(user: dict) -> None:
    if not _can_enter(user):
        raise HTTPException(403, "Coach assessment entry permission required")


def _assert_manage(user: dict) -> None:
    if not _can_manage(user):
        raise HTTPException(403, "Coach assessment management permission required")


async def _get_definition(defn_id: str) -> dict:
    d = await db.coach_assessment_definitions.find_one({"id": defn_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Assessment definition not found")
    return d


def _coach_scope_ok(user: dict, centre: Optional[str], sport: Optional[str]) -> None:
    if is_admin(user):
        return
    if user.get("role") != "coach":
        raise HTTPException(403, "Coach role required")
    centres = user.get("assigned_centres") or []
    sports = user.get("assigned_sports") or []
    if centre and centres and centre not in centres:
        raise HTTPException(403, "Centre not in your assigned centres")
    if sport and sports and sport not in sports:
        raise HTTPException(403, "Sport not in your assigned sports")


async def _players_in_scope(
    user: dict,
    centre: str,
    sport: str,
    slot: str,
) -> list[dict]:
    q = _coach_visibility_filter(user)
    q["centre"] = centre
    q["sport"] = sport
    q["slot"] = slot
    return await db.people.find(q, {"_id": 0, "id": 1, "name": 1}).sort("name", 1).to_list(500)


# ------------------ Definitions (admin) ------------------
class DefinitionIn(BaseModel):
    name: str
    assessment_type: Literal["rating", "score", "test"]
    sport: Literal["Cricket", "Football"]
    centre: Optional[Literal["Balua", "Harding Park"]] = None
    slot: Optional[Literal["Morning", "Evening"]] = None
    max_score: Optional[int] = Field(None, ge=1, le=500)
    rating_labels: List[str] = []


@router.get("/definitions")
async def list_definitions(
    sport: Optional[str] = None,
    centre: Optional[str] = None,
    slot: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    _assert_enter(user)
    rows = await db.coach_assessment_definitions.find(
        {"entity_id": ENTITY_ALPHA}, {"_id": 0},
    ).sort("name", 1).to_list(100)
    out = []
    for r in rows:
        if sport and r.get("sport") and r["sport"] != sport:
            continue
        if centre and r.get("centre") and r["centre"] != centre:
            continue
        if slot and r.get("slot") and r["slot"] != slot:
            continue
        if user.get("role") == "coach":
            centres = user.get("assigned_centres") or []
            sports = user.get("assigned_sports") or []
            if sports and r.get("sport") and r["sport"] not in sports:
                continue
            if centres and r.get("centre") and r["centre"] not in centres:
                continue
        out.append(r)
    return out


@router.post("/definitions")
async def create_definition(payload: DefinitionIn, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    if payload.assessment_type in ("score", "test") and not payload.max_score:
        raise HTTPException(400, "max_score required for score/test assessments")
    doc = {
        "id": str(uuid.uuid4()),
        "name": payload.name.strip(),
        "assessment_type": payload.assessment_type,
        "sport": payload.sport,
        "centre": payload.centre,
        "slot": payload.slot,
        "max_score": payload.max_score,
        "rating_labels": payload.rating_labels or ["1", "2", "3", "4", "5"],
        "entity_id": ENTITY_ALPHA,
        "created_at": now_utc().isoformat(),
        "created_by": user["id"],
    }
    await db.coach_assessment_definitions.insert_one(doc)
    doc.pop("_id", None)
    return doc


# ------------------ Coach entry grid ------------------
class AssessmentEntryIn(BaseModel):
    player_id: str
    score: Optional[float] = Field(None, ge=0)
    rating: Optional[str] = None
    coach_remark: Optional[str] = None


class AssessmentBatchIn(BaseModel):
    definition_id: str
    date: str
    centre: Literal["Balua", "Harding Park"]
    sport: Literal["Cricket", "Football"]
    slot: Literal["Morning", "Evening"]
    entries: List[AssessmentEntryIn]
    status: Literal["draft", "final"] = "draft"


@router.get("/grid")
async def assessment_grid(
    definition_id: str,
    date: str,
    centre: Literal["Balua", "Harding Park"],
    sport: Literal["Cricket", "Football"],
    slot: Literal["Morning", "Evening"],
    user: dict = Depends(get_current_user),
):
    _assert_enter(user)
    _coach_scope_ok(user, centre, sport)
    defn = await _get_definition(definition_id)
    players = await _players_in_scope(user, centre, sport, slot)
    existing = await db.player_assessments.find(
        {"definition_id": definition_id, "date": date, "centre": centre, "sport": sport, "slot": slot},
        {"_id": 0},
    ).to_list(500)
    by_player = {r["player_id"]: r for r in existing}
    rows = []
    for p in players:
        m = by_player.get(p["id"])
        rows.append({
            "player_id": p["id"],
            "name": p["name"],
            "score": m.get("score") if m else None,
            "max_score": m.get("max_score") if m else defn.get("max_score"),
            "rating": m.get("rating") if m else None,
            "coach_remark": m.get("coach_remark") if m else None,
            "status": m.get("status") if m else None,
            "record_id": m.get("id") if m else None,
            "entered_at": m.get("entered_at") if m else None,
        })
    return {
        "definition": defn,
        "date": date,
        "centre": centre,
        "sport": sport,
        "slot": slot,
        "players": rows,
    }


@router.post("/batch")
async def save_assessment_batch(payload: AssessmentBatchIn, user: dict = Depends(get_current_user)):
    _assert_enter(user)
    _coach_scope_ok(user, payload.centre, payload.sport)
    defn = await _get_definition(payload.definition_id)
    players = await _players_in_scope(user, payload.centre, payload.sport, payload.slot)
    valid_ids = {p["id"] for p in players}
    max_score = defn.get("max_score")
    ts = now_utc().isoformat()
    saved = 0

    for entry in payload.entries:
        if entry.player_id not in valid_ids:
            raise HTTPException(403, f"Player {entry.player_id} is not in your assigned roster")
        existing = await db.player_assessments.find_one({
            "player_id": entry.player_id,
            "definition_id": payload.definition_id,
            "date": payload.date,
        })
        if existing and existing.get("status") == "published":
            raise HTTPException(403, "Published assessments cannot be edited")
        if existing and existing.get("status") == "final" and user.get("role") == "coach":
            raise HTTPException(403, "Finalized assessments are locked. Contact admin.")

        if defn["assessment_type"] in ("score", "test"):
            if entry.score is None and payload.status == "final":
                continue
            if entry.score is not None and max_score and entry.score > max_score:
                raise HTTPException(400, f"Score {entry.score} exceeds maximum {max_score}")
        if defn["assessment_type"] == "rating" and not entry.rating and payload.status == "final":
            continue

        if entry.score is None and not entry.rating and not (entry.coach_remark or "").strip():
            if existing and existing.get("status") not in ("final", "published"):
                await db.player_assessments.delete_one({"id": existing["id"]})
            continue

        doc = {
            "player_id": entry.player_id,
            "definition_id": payload.definition_id,
            "date": payload.date,
            "sport": payload.sport,
            "centre": payload.centre,
            "slot": payload.slot,
            "assessment_type": defn["assessment_type"],
            "definition_name": defn["name"],
            "score": entry.score,
            "max_score": max_score,
            "rating": entry.rating,
            "coach_remark": (entry.coach_remark or "").strip() or None,
            "status": payload.status,
            "entity_id": ENTITY_ALPHA,
            "entered_by": user["id"],
            "entered_by_name": user.get("name"),
            "entered_at": ts,
            "updated_at": ts,
        }
        if payload.status == "final":
            doc["finalized_at"] = ts
            doc["finalized_by"] = user["id"]

        if existing:
            await db.player_assessments.update_one({"id": existing["id"]}, {"$set": doc})
        else:
            doc["id"] = str(uuid.uuid4())
            doc["created_at"] = ts
            await db.player_assessments.insert_one(doc)
        saved += 1

    return {"ok": True, "saved": saved, "status": payload.status}


class PublishIn(BaseModel):
    definition_id: str
    date: str


@router.post("/publish")
async def publish_assessments(payload: PublishIn, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    ts = now_utc().isoformat()
    result = await db.player_assessments.update_many(
        {
            "definition_id": payload.definition_id,
            "date": payload.date,
            "status": "final",
        },
        {"$set": {"status": "published", "published_at": ts, "published_by": user["id"]}},
    )
    return {"published": result.modified_count}


async def published_for_player(player_id: str) -> list[dict]:
    rows = await db.player_assessments.find(
        {"player_id": player_id, "status": "published"},
        {"_id": 0},
    ).sort("date", -1).to_list(200)
    return rows


@router.get("/published/{player_id}")
async def get_published(player_id: str, user: dict = Depends(get_current_user)):
    person = await db.people.find_one({"id": player_id, "kind": "player"}, {"_id": 0})
    if not person:
        raise HTTPException(404, "Player not found")
    if user.get("role") == "parent":
        if player_id not in (user.get("linked_person_ids") or []):
            raise HTTPException(404, "Ward not found")
    elif user.get("role") == "coach":
        if not _can_enter(user):
            raise HTTPException(403, "Not allowed")
    elif not is_admin(user) and not get_perm(user, "view_coach_assessments"):
        raise HTTPException(403, "Not allowed")
    return {"player_id": player_id, "assessments": await published_for_player(player_id)}
