"""Player assessment — sport-specific technical sub-scores, player-type setup, PDF export."""
import io
import uuid
import zipfile
from typing import Optional, List, Literal, Dict, Any, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from core import db, get_current_user, get_perm, is_admin, is_super_admin, now_utc, format_date_display, format_datetime_display
from routers.coach import _coach_visibility_filter

router = APIRouter(prefix="/coach-assessments", tags=["coach-assessments"])

ENTITY_ALPHA = "alpha"
SCHEMA_VERSION = 3

AssessmentStage = Literal["week_1_baseline", "week_4_progress", "week_8_12_final"]
Centre = Literal["Balua", "Harding Park"]
Sport = Literal["Cricket", "Football"]
Session = Literal["Morning", "Evening"]
PlayerType = Literal["Daily", "Day Boarding", "Hostel", "Boarding"]

ASSESSMENT_STAGES: Dict[str, str] = {
    "week_1_baseline": "Week 1 - Baseline",
    "week_4_progress": "Week 4 - Progress",
    "week_8_12_final": "Week 8-12 - Final",
}
STAGE_ORDER = ["week_1_baseline", "week_4_progress", "week_8_12_final"]
SETUP_PLAYER_TYPES = ["Daily", "Day Boarding", "Hostel", "Boarding"]
AGE_GROUPS = ["U-10", "U-12", "U-14", "U-16", "U-18", "Open"]

CORE_SCORE_KEYS = (
    "strength_conditioning",
    "game_awareness",
    "mental_attributes",
    "training_attitude",
)

CRICKET_TECH_KEYS = (
    "batting", "bowling", "fielding", "wicket_keeping",
    "running_between_wickets", "cricket_iq",
)
FOOTBALL_TECH_KEYS = (
    "dribbling", "passing", "shooting", "defending", "heading", "football_iq",
)

CRICKET_TECH: Dict[str, Dict[str, str]] = {
    "batting": {
        "label": "Batting",
        "coach": "Technique, footwork, shot selection, timing and scoring ability",
        "parent": "How well your child bats, including their technique, footwork, timing and ability to score runs.",
    },
    "bowling": {
        "label": "Bowling",
        "coach": "Action, line and length, variation, pace/spin control",
        "parent": "Your child's bowling skill, including their action, accuracy, pace or spin, and ability to take wickets.",
    },
    "fielding": {
        "label": "Fielding",
        "coach": "Catching, ground fielding, throwing accuracy and athleticism",
        "parent": "How well your child fields, including catching, ground fielding and throwing accuracy.",
    },
    "wicket_keeping": {
        "label": "Wicket-Keeping",
        "coach": "Glove work, positioning, stumpings and communication (if applicable)",
        "parent": "Your child's glove work, positioning and ability to take catches and effect stumpings (assessed where applicable).",
    },
    "running_between_wickets": {
        "label": "Running Between Wickets",
        "coach": "Decision-making, communication, backing up and conversion of singles",
        "parent": "Your child's ability to convert singles, communicate with their batting partner and make smart running decisions.",
    },
    "cricket_iq": {
        "label": "Cricket IQ",
        "coach": "Reading the game, tactical awareness and match situation understanding",
        "parent": "How well your child reads the game, understands match situations and makes tactical decisions.",
    },
}

FOOTBALL_TECH: Dict[str, Dict[str, str]] = {
    "dribbling": {
        "label": "Dribbling",
        "coach": "Ball control, close control, change of direction and beating opponents",
        "parent": "Your child's ability to control the ball, move past opponents and maintain possession under pressure.",
    },
    "passing": {
        "label": "Passing",
        "coach": "Accuracy, weight of pass, range, timing and decision to pass",
        "parent": "How accurately and intelligently your child passes the ball, including range and timing.",
    },
    "shooting": {
        "label": "Shooting",
        "coach": "Technique, accuracy, power, composure in front of goal",
        "parent": "Your child's ability to shoot with technique, accuracy and power, and to stay composed in front of goal.",
    },
    "defending": {
        "label": "Defending",
        "coach": "Positioning, tackling, marking, interceptions and defensive awareness",
        "parent": "Your child's positioning, tackling ability and effectiveness at winning the ball back for the team.",
    },
    "heading": {
        "label": "Heading",
        "coach": "Technique, timing, direction and aerial ability in attack and defence",
        "parent": "Your child's aerial ability, including technique and timing when heading the ball in attack and defence.",
    },
    "football_iq": {
        "label": "Football IQ",
        "coach": "Positioning off the ball, reading play, pressing and tactical discipline",
        "parent": "How well your child reads the game, positions themselves off the ball and understands team tactics.",
    },
}

PARAMETERS: Dict[str, Dict[str, str]] = {
    "technical_skill": {
        "label": "Technical Skill",
        "parent": "How well your child performs the core skills of their sport (batting, bowling, fielding for Cricket; passing, shooting, defending for Football).",
        "coach": "Average of all sport-specific technical sub-parameter scores.",
    },
    "strength_conditioning": {
        "label": "Strength & Conditioning",
        "parent": "Your child's physical fitness level, including speed, strength, stamina, agility and flexibility.",
        "coach": "Speed, strength, power, agility, endurance and mobility.",
    },
    "game_awareness": {
        "label": "Game Awareness",
        "parent": "How smartly your child reads the game, makes decisions and positions themselves during a match.",
        "coach": "Decision-making, tactical understanding and match awareness.",
    },
    "mental_attributes": {
        "label": "Mental Attributes",
        "parent": "Your child's ability to stay focused, confident and composed under pressure during training and matches.",
        "coach": "Confidence, focus, resilience and composure under pressure.",
    },
    "training_attitude": {
        "label": "Training Attitude",
        "parent": "How your child behaves during practice sessions — their discipline, effort, coachability and teamwork.",
        "coach": "Discipline, effort, coachability, teamwork and attendance.",
    },
}


def _tech_meta(sport: str) -> Dict[str, Dict[str, str]]:
    return CRICKET_TECH if sport == "Cricket" else FOOTBALL_TECH


def _tech_keys(sport: str) -> Tuple[str, ...]:
    return CRICKET_TECH_KEYS if sport == "Cricket" else FOOTBALL_TECH_KEYS


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


def _coach_scope_ok(user: dict, centre: Optional[str], sport: Optional[str], player_type: Optional[str] = None) -> None:
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
    if player_type and centres and centre and centre not in centres:
        raise HTTPException(403, "Player type not available for your centre allocation")


def score_label(value: int) -> str:
    if value <= 3:
        return "Beginner"
    if value <= 5:
        return "Developing"
    if value <= 7:
        return "Good"
    if value <= 9:
        return "Very Good"
    return "Elite"


def age_group_for_age(age: Optional[int]) -> str:
    if age is None:
        return "—"
    if age <= 10:
        return "U-10"
    if age <= 12:
        return "U-12"
    if age <= 14:
        return "U-14"
    if age <= 16:
        return "U-16"
    if age <= 18:
        return "U-18"
    return "Open"


def _player_role_label(player: dict) -> str:
    parts = []
    ag = age_group_for_age(player.get("age"))
    if ag != "—":
        parts.append(ag)
    if player.get("skill_level"):
        parts.append(player["skill_level"])
    return " · ".join(parts) if parts else "—"


def _player_type_query(player_type: str) -> Any:
    if player_type == "Hostel":
        return {"$in": ["Hostel", "Hostel Only"]}
    return player_type


def _calc_technical_avg(technical_sub: dict, sport: str) -> Optional[float]:
    keys = _tech_keys(sport)
    vals = [technical_sub.get(k) for k in keys]
    if any(v is None for v in vals):
        return None
    return round(sum(int(v) for v in vals) / len(keys), 1)


def _build_scores_payload(
    sport: str,
    technical_sub: Optional[dict],
    strength_conditioning: Optional[int],
    game_awareness: Optional[int],
    mental_attributes: Optional[int],
    training_attitude: Optional[int],
) -> dict:
    tech_sub = {k: None for k in _tech_keys(sport)}
    if technical_sub:
        for k in _tech_keys(sport):
            if technical_sub.get(k) is not None:
                tech_sub[k] = int(technical_sub[k])
    tech_avg = _calc_technical_avg(tech_sub, sport)
    core = {
        "strength_conditioning": strength_conditioning,
        "game_awareness": game_awareness,
        "mental_attributes": mental_attributes,
        "training_attitude": training_attitude,
    }
    overall = None
    if tech_avg is not None and all(core[k] is not None for k in CORE_SCORE_KEYS):
        overall = round(
            (tech_avg + core["strength_conditioning"] + core["game_awareness"]
             + core["mental_attributes"] + core["training_attitude"]) / 5,
            1,
        )
    return {
        "technical_sub": tech_sub,
        "technical_skill_avg": tech_avg,
        **core,
        "overall_score": overall,
    }


def _scores_complete(scores: Optional[dict], sport: str) -> bool:
    if not scores:
        return False
    tech_sub = scores.get("technical_sub") or {}
    if not all(tech_sub.get(k) is not None for k in _tech_keys(sport)):
        return False
    return all(scores.get(k) is not None for k in CORE_SCORE_KEYS)


def _completion_status(scores: Optional[dict], sport: str) -> str:
    if not scores:
        return "not_started"
    tech_sub = scores.get("technical_sub") or {}
    any_score = any(tech_sub.get(k) is not None for k in _tech_keys(sport))
    any_score = any_score or any(scores.get(k) is not None for k in CORE_SCORE_KEYS)
    if not any_score:
        return "not_started"
    if _scores_complete(scores, sport):
        return "completed"
    return "in_progress"


def _normalize_scores(raw: Optional[dict], sport: str) -> dict:
    if not raw:
        return _build_scores_payload(sport, None, None, None, None, None)
    if raw.get("technical_sub") is not None or raw.get("technical_skill_avg") is not None:
        return _build_scores_payload(
            sport,
            raw.get("technical_sub"),
            raw.get("strength_conditioning"),
            raw.get("game_awareness"),
            raw.get("mental_attributes"),
            raw.get("training_attitude"),
        )
    # Legacy v2 single technical_skill integer
    legacy_tech = raw.get("technical_skill")
    tech_sub = {k: legacy_tech for k in _tech_keys(sport)} if legacy_tech is not None else {k: None for k in _tech_keys(sport)}
    return _build_scores_payload(
        sport,
        tech_sub,
        raw.get("strength_conditioning"),
        raw.get("game_awareness"),
        raw.get("mental_attributes"),
        raw.get("training_attitude"),
    )


def _serialize_record(row: dict, sport: Optional[str] = None) -> dict:
    sport = sport or row.get("sport") or "Cricket"
    scores = _normalize_scores(row.get("scores"), sport)
    complete = _scores_complete(scores, sport)
    return {
        "id": row.get("id"),
        "player_id": row.get("player_id"),
        "player_name": row.get("player_name"),
        "centre": row.get("centre"),
        "sport": row.get("sport"),
        "player_type": row.get("player_type"),
        "session": row.get("session") or row.get("slot"),
        "assessment_stage": row.get("assessment_stage"),
        "assessment_stage_label": ASSESSMENT_STAGES.get(row.get("assessment_stage") or "", row.get("assessment_stage")),
        "date": row.get("date"),
        "scores": scores,
        "technical_sub": scores.get("technical_sub"),
        "technical_skill_avg": scores.get("technical_skill_avg"),
        "overall_score": scores.get("overall_score"),
        "technical_skill_avg": scores.get("technical_skill_avg"),
        "overall_score": scores.get("overall_score"),
        "completion_status": _completion_status(scores, sport),
        "coach_remark": row.get("coach_remark"),
        "status": row.get("status"),
        "complete": complete,
        "saved_by": row.get("saved_by") or row.get("entered_by"),
        "saved_by_name": row.get("saved_by_name") or row.get("entered_by_name"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "finalized_at": row.get("finalized_at"),
        "published_at": row.get("published_at"),
        "schema_version": row.get("schema_version", 1),
    }


async def _players_filtered(
    user: dict,
    centre: str,
    sport: str,
    player_type: str,
    *,
    session: Optional[str] = None,
    age_group: Optional[str] = None,
    player_search: Optional[str] = None,
) -> list[dict]:
    if player_type == "Daily" and not session:
        raise HTTPException(400, "Session type is required when player type is Daily")
    q = _coach_visibility_filter(user)
    q["centre"] = centre
    q["sport"] = sport
    q["status"] = {"$ne": "deactivated"}
    q["player_type"] = _player_type_query(player_type)
    if player_type == "Daily":
        q["slot"] = session
    if player_search and player_search.strip():
        q["name"] = {"$regex": player_search.strip(), "$options": "i"}
    players = await db.people.find(
        q,
        {"_id": 0, "id": 1, "name": 1, "age": 1, "skill_level": 1, "player_type": 1, "status": 1, "slot": 1},
    ).sort("name", 1).to_list(500)
    if age_group and age_group != "All":
        players = [p for p in players if age_group_for_age(p.get("age")) == age_group]
    return players


def _batch_filter(
    centre: str,
    sport: str,
    player_type: str,
    assessment_stage: str,
    date: str,
    session: Optional[str] = None,
) -> dict:
    filt: dict = {
        "schema_version": SCHEMA_VERSION,
        "centre": centre,
        "sport": sport,
        "player_type": player_type,
        "assessment_stage": assessment_stage,
        "date": date,
    }
    if player_type == "Daily" and session:
        filt["$or"] = [{"session": session}, {"slot": session}]
    return filt


async def _batch_status(
    centre: str,
    sport: str,
    player_type: str,
    assessment_stage: str,
    date: str,
    session: Optional[str] = None,
    player_count: int = 0,
) -> dict:
    rows = await db.player_assessments.find(
        _batch_filter(centre, sport, player_type, assessment_stage, date, session),
        {"_id": 0, "status": 1, "scores": 1},
    ).to_list(500)
    statuses = [r.get("status") for r in rows if r.get("status")]
    completed = sum(1 for r in rows if _scores_complete(r.get("scores"), sport))
    if not statuses:
        return {"batch_status": None, "saved_count": 0, "all_complete": False, "completed_count": 0}
    if all(s == "published" for s in statuses):
        batch_status = "published"
    elif all(s == "final" for s in statuses):
        batch_status = "final"
    elif any(s == "draft" for s in statuses):
        batch_status = "draft"
    else:
        batch_status = statuses[0]
    all_complete = player_count > 0 and completed >= player_count and all(
        _scores_complete(r.get("scores"), sport) for r in rows
    )
    return {
        "batch_status": batch_status,
        "saved_count": len(rows),
        "completed_count": completed,
        "all_complete": all_complete,
    }


# ------------------ Metadata ------------------
@router.get("/metadata")
async def assessment_metadata(user: dict = Depends(get_current_user)):
    _assert_enter(user)
    centres = list(user.get("assigned_centres") or []) if user.get("role") == "coach" else ["Balua", "Harding Park"]
    sports = list(user.get("assigned_sports") or []) if user.get("role") == "coach" else ["Cricket", "Football"]
    if not centres:
        centres = ["Balua", "Harding Park"]
    if not sports:
        sports = ["Cricket", "Football"]
    return {
        "stages": [{"id": k, "label": v} for k, v in ASSESSMENT_STAGES.items()],
        "player_types": SETUP_PLAYER_TYPES,
        "allowed_centres": centres,
        "allowed_sports": sports,
        "cricket_technical": [{"key": k, **CRICKET_TECH[k]} for k in CRICKET_TECH_KEYS],
        "football_technical": [{"key": k, **FOOTBALL_TECH[k]} for k in FOOTBALL_TECH_KEYS],
        "core_parameters": [
            {"key": k, "label": PARAMETERS[k]["label"], "description": PARAMETERS[k]["coach"]}
            for k in CORE_SCORE_KEYS
        ],
        "score_scale": [
            {"range": "1–3", "label": "Beginner"},
            {"range": "4–5", "label": "Developing"},
            {"range": "6–7", "label": "Good"},
            {"range": "8–9", "label": "Very Good"},
            {"range": "10", "label": "Elite"},
        ],
        "age_groups": ["All", *AGE_GROUPS],
    }


# ------------------ Coach entry grid ------------------
class AssessmentEntryIn(BaseModel):
    player_id: str
    technical_sub: Optional[Dict[str, int]] = None
    strength_conditioning: Optional[int] = Field(None, ge=1, le=10)
    game_awareness: Optional[int] = Field(None, ge=1, le=10)
    mental_attributes: Optional[int] = Field(None, ge=1, le=10)
    training_attitude: Optional[int] = Field(None, ge=1, le=10)
    coach_remark: Optional[str] = Field(None, max_length=300)


class AssessmentBatchIn(BaseModel):
    centre: Centre
    sport: Sport
    player_type: PlayerType
    session: Optional[Session] = None
    assessment_stage: AssessmentStage
    date: str
    entries: List[AssessmentEntryIn]
    status: Literal["draft", "final"] = "draft"

    @model_validator(mode="after")
    def _daily_session_required(self):
        if self.player_type == "Daily" and not self.session:
            raise ValueError("Session type is required when player type is Daily")
        return self


@router.get("/grid")
async def assessment_grid(
    centre: Centre,
    sport: Sport,
    player_type: PlayerType,
    assessment_stage: AssessmentStage,
    date: str,
    session: Optional[Session] = None,
    age_group: Optional[str] = None,
    player_search: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    _assert_enter(user)
    _coach_scope_ok(user, centre, sport, player_type)
    players = await _players_filtered(
        user, centre, sport, player_type,
        session=session,
        age_group=age_group,
        player_search=player_search,
    )
    existing = await db.player_assessments.find(
        _batch_filter(centre, sport, player_type, assessment_stage, date, session),
        {"_id": 0},
    ).to_list(500)
    by_player = {r["player_id"]: r for r in existing}
    rows = []
    for p in players:
        m = by_player.get(p["id"])
        scores = _normalize_scores(m.get("scores") if m else None, sport)
        complete = _scores_complete(scores, sport)
        rec_status = m.get("status") if m else None
        rows.append({
            "player_id": p["id"],
            "name": p["name"],
            "age_group": age_group_for_age(p.get("age")),
            "role": _player_role_label(p),
            "player_type": p.get("player_type"),
            "scores": scores,
            "technical_sub": scores.get("technical_sub"),
            "technical_skill_avg": scores.get("technical_skill_avg"),
            "overall_score": scores.get("overall_score"),
            "completion_status": _completion_status(scores, sport),
            "coach_remark": m.get("coach_remark") if m else None,
            "status": rec_status,
            "complete": complete,
            "record_id": m.get("id") if m else None,
            "saved_by_name": m.get("saved_by_name") if m else None,
            "updated_at": m.get("updated_at") if m else None,
            "read_only": rec_status in ("final", "published") and user.get("role") == "coach",
        })
    batch = await _batch_status(centre, sport, player_type, assessment_stage, date, session, len(rows))
    return {
        "schema_version": SCHEMA_VERSION,
        "date": date,
        "centre": centre,
        "sport": sport,
        "player_type": player_type,
        "session": session,
        "assessment_stage": assessment_stage,
        "assessment_stage_label": ASSESSMENT_STAGES[assessment_stage],
        "technical_parameters": [{"key": k, **_tech_meta(sport)[k]} for k in _tech_keys(sport)],
        "core_parameters": [{"key": k, **PARAMETERS[k]} for k in CORE_SCORE_KEYS],
        "players": rows,
        "player_count": len(rows),
        "completed_count": batch["completed_count"],
        "batch_status": batch["batch_status"],
        "all_complete": batch["all_complete"],
        "saved_count": batch["saved_count"],
    }


@router.post("/batch")
async def save_assessment_batch(payload: AssessmentBatchIn, user: dict = Depends(get_current_user)):
    _assert_enter(user)
    _coach_scope_ok(user, payload.centre, payload.sport, payload.player_type)
    players = await _players_filtered(
        user, payload.centre, payload.sport, payload.player_type, session=payload.session,
    )
    valid_ids = {p["id"]: p for p in players}
    ts = now_utc().isoformat()
    saved = 0

    if payload.status == "final":
        missing = []
        for entry in payload.entries:
            if entry.player_id not in valid_ids:
                continue
            scores = _build_scores_payload(
                payload.sport,
                entry.technical_sub,
                entry.strength_conditioning,
                entry.game_awareness,
                entry.mental_attributes,
                entry.training_attitude,
            )
            if not _scores_complete(scores, payload.sport):
                missing.append(valid_ids[entry.player_id]["name"])
        if missing:
            raise HTTPException(
                400,
                f"All scores are required before finalizing. Incomplete: {', '.join(missing[:5])}"
                + ("…" if len(missing) > 5 else ""),
            )

    for entry in payload.entries:
        if entry.player_id not in valid_ids:
            raise HTTPException(403, f"Player {entry.player_id} is not in your assigned roster")
        player = valid_ids[entry.player_id]
        existing = await db.player_assessments.find_one({
            "schema_version": SCHEMA_VERSION,
            "player_id": entry.player_id,
            "assessment_stage": payload.assessment_stage,
            "date": payload.date,
        })
        if existing and existing.get("status") == "published":
            raise HTTPException(403, "Published assessments cannot be edited")
        if existing and existing.get("status") == "final" and user.get("role") == "coach":
            raise HTTPException(403, "Finalized assessments are locked. Contact admin to reopen.")

        scores = _build_scores_payload(
            payload.sport,
            entry.technical_sub,
            entry.strength_conditioning,
            entry.game_awareness,
            entry.mental_attributes,
            entry.training_attitude,
        )
        has_data = _scores_complete(scores, payload.sport) or _completion_status(scores, payload.sport) != "not_started" or (entry.coach_remark or "").strip()
        if not has_data:
            if existing and existing.get("status") not in ("final", "published"):
                await db.player_assessments.delete_one({"id": existing["id"]})
            continue

        doc = {
            "player_id": entry.player_id,
            "player_name": player["name"],
            "centre": payload.centre,
            "sport": payload.sport,
            "player_type": payload.player_type,
            "session": payload.session,
            "slot": payload.session,
            "assessment_stage": payload.assessment_stage,
            "date": payload.date,
            "scores": scores,
            "coach_remark": (entry.coach_remark or "").strip()[:300] or None,
            "status": payload.status,
            "schema_version": SCHEMA_VERSION,
            "entity_id": ENTITY_ALPHA,
            "saved_by": user["id"],
            "saved_by_name": user.get("name"),
            "entered_by": user["id"],
            "entered_by_name": user.get("name"),
            "entered_at": ts,
            "updated_at": ts,
        }
        if payload.status == "final":
            doc["finalized_at"] = ts
            doc["finalized_by"] = user["id"]

        if existing:
            doc["created_at"] = existing.get("created_at", ts)
            await db.player_assessments.update_one({"id": existing["id"]}, {"$set": doc})
        else:
            doc["id"] = str(uuid.uuid4())
            doc["created_at"] = ts
            await db.player_assessments.insert_one(doc)
        saved += 1

    return {"ok": True, "saved": saved, "status": payload.status}


class PublishIn(BaseModel):
    centre: Centre
    sport: Sport
    player_type: PlayerType
    session: Optional[Session] = None
    assessment_stage: AssessmentStage
    date: str


@router.post("/publish")
async def publish_assessments(payload: PublishIn, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    ts = now_utc().isoformat()
    filt = {**_batch_filter(
        payload.centre, payload.sport, payload.player_type,
        payload.assessment_stage, payload.date, payload.session,
    ), "status": "final"}
    result = await db.player_assessments.update_many(
        filt,
        {"$set": {"status": "published", "published_at": ts, "published_by": user["id"]}},
    )
    return {"published": result.modified_count}


class ReopenIn(BaseModel):
    centre: Centre
    sport: Sport
    player_type: PlayerType
    session: Optional[Session] = None
    assessment_stage: AssessmentStage
    date: str
    reason: str = Field(..., min_length=3, max_length=500)


@router.post("/reopen")
async def reopen_assessments(payload: ReopenIn, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    ts = now_utc().isoformat()
    filt = {
        **_batch_filter(
            payload.centre, payload.sport, payload.player_type,
            payload.assessment_stage, payload.date, payload.session,
        ),
        "status": {"$in": ["final", "published"]},
    }
    rows = await db.player_assessments.find(filt, {"_id": 0}).to_list(500)
    if not rows:
        raise HTTPException(404, "No finalized assessments found for this batch")
    audit = {
        "id": str(uuid.uuid4()),
        "action": "reopen",
        "centre": payload.centre,
        "sport": payload.sport,
        "player_type": payload.player_type,
        "session": payload.session,
        "assessment_stage": payload.assessment_stage,
        "date": payload.date,
        "reason": payload.reason.strip(),
        "reopened_by": user["id"],
        "reopened_by_name": user.get("name"),
        "reopened_at": ts,
        "previous_records": [
            {"id": r["id"], "player_id": r.get("player_id"), "scores": r.get("scores"), "status": r.get("status")}
            for r in rows
        ],
    }
    await db.assessment_audit.insert_one(audit)
    result = await db.player_assessments.update_many(
        {"id": {"$in": [r["id"] for r in rows]}},
        {
            "$set": {"status": "draft", "updated_at": ts},
            "$unset": {"finalized_at": "", "finalized_by": "", "published_at": "", "published_by": ""},
            "$push": {"reopen_history": audit},
        },
    )
    return {"reopened": result.modified_count, "audit_id": audit["id"]}


async def _prior_stages_for_player(player_id: str, current_stage: str, sport: str) -> list[dict]:
    idx = STAGE_ORDER.index(current_stage) if current_stage in STAGE_ORDER else -1
    if idx <= 0:
        return []
    prior_keys = STAGE_ORDER[:idx]
    rows = await db.player_assessments.find(
        {
            "player_id": player_id,
            "assessment_stage": {"$in": prior_keys},
            "status": {"$in": ["final", "published"]},
            "schema_version": {"$gte": 2},
        },
        {"_id": 0},
    ).sort("date", -1).to_list(20)
    seen = set()
    out = []
    for r in rows:
        stage = r.get("assessment_stage")
        if stage in seen:
            continue
        seen.add(stage)
        out.append(_serialize_record(r, sport))
    return sorted(out, key=lambda x: STAGE_ORDER.index(x["assessment_stage"]))


def _wrap_text(text: str, max_chars: int) -> list[str]:
    words = (text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _render_assessment_pdf(record: dict, prior: list[dict]) -> bytes:
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    sport = record.get("sport") or "Cricket"
    scores = _normalize_scores(record.get("scores"), sport)
    tech_meta = _tech_meta(sport)
    tech_keys = _tech_keys(sport)

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    w, h = A4
    margin = 18 * mm
    y = h - margin
    navy = rl_colors.HexColor("#1E3A8A")
    muted = rl_colors.HexColor("#64748B")

    c.setFillColor(navy)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(margin, y, "ALPHA Sports Academy")
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    c.setFillColor(muted)
    c.drawString(margin, y, "Player Performance Assessment")
    y -= 14 * mm

    c.setFillColor(rl_colors.black)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, y, record.get("player_name") or "Player")
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    session_txt = record.get("session") or record.get("slot") or ""
    if record.get("player_type") != "Daily":
        session_txt = "—"
    meta = (
        f"{record.get('sport', '—')}  ·  {record.get('centre', '—')}  ·  "
        f"{record.get('player_type', '—')}"
        + (f"  ·  {session_txt}" if session_txt and session_txt != "—" else "")
        + f"  ·  {ASSESSMENT_STAGES.get(record.get('assessment_stage', ''), '—')}"
    )
    c.drawString(margin, y, meta)
    y -= 5 * mm
    c.drawString(margin, y, f"Assessment date: {format_date_display(record.get('date'))}")
    y -= 5 * mm
    c.drawString(margin, y, f"Coach: {record.get('saved_by_name') or '—'}")
    y -= 5 * mm
    c.drawString(margin, y, f"Saved: {format_datetime_display(record.get('updated_at') or record.get('created_at'))}")
    y -= 10 * mm
    c.setFont("Helvetica", 8)
    c.setFillColor(muted)
    c.drawString(margin, y, "Score guide: 1–3 Beginner | 4–5 Developing | 6–7 Good | 8–9 Very Good | 10 Elite")
    y -= 10 * mm
    c.setFillColor(rl_colors.black)

    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, y, "Technical Skill")
    y -= 6 * mm
    tech_avg = scores.get("technical_skill_avg")
    c.setFont("Helvetica", 10)
    c.drawString(margin, y, f"Average: {tech_avg}/10 — {score_label(int(round(tech_avg)))}" if tech_avg else "Average: —")
    y -= 6 * mm
    c.setFont("Helvetica", 8)
    c.setFillColor(muted)
    for line in _wrap_text(PARAMETERS["technical_skill"]["parent"], 95):
        c.drawString(margin, y, line)
        y -= 4 * mm
    c.setFillColor(rl_colors.black)
    y -= 4 * mm
    tech_sub = scores.get("technical_sub") or {}
    for key in tech_keys:
        if y < 35 * mm:
            c.showPage()
            y = h - margin
        val = tech_sub.get(key)
        c.setFont("Helvetica-Bold", 9)
        lbl = tech_meta[key]["label"]
        score_txt = f"{val}/10 — {score_label(val)}" if val is not None else "—"
        c.drawString(margin, y, f"{lbl}: {score_txt}")
        y -= 4 * mm
        c.setFont("Helvetica", 8)
        c.setFillColor(muted)
        for line in _wrap_text(tech_meta[key]["parent"], 95):
            c.drawString(margin + 3 * mm, y, line)
            y -= 3.5 * mm
        c.setFillColor(rl_colors.black)
        y -= 2 * mm

    for key in CORE_SCORE_KEYS:
        if y < 40 * mm:
            c.showPage()
            y = h - margin
        val = scores.get(key)
        c.setFont("Helvetica-Bold", 10)
        score_txt = f"{val}/10 — {score_label(val)}" if val is not None else "—"
        c.drawString(margin, y, f"{PARAMETERS[key]['label']}: {score_txt}")
        y -= 5 * mm
        c.setFont("Helvetica", 8)
        c.setFillColor(muted)
        for line in _wrap_text(PARAMETERS[key]["parent"], 95):
            c.drawString(margin + 3 * mm, y, line)
            y -= 3.5 * mm
        c.setFillColor(rl_colors.black)
        y -= 3 * mm

    overall = scores.get("overall_score")
    if overall is not None:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margin, y, f"Overall Score: {overall} / 10")
        y -= 10 * mm

    if record.get("coach_remark"):
        if y < 35 * mm:
            c.showPage()
            y = h - margin
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margin, y, "Coach remarks")
        y -= 7 * mm
        c.setFont("Helvetica", 9)
        for line in _wrap_text(record["coach_remark"], 95):
            c.drawString(margin, y, line)
            y -= 5 * mm

    if prior:
        if y < 50 * mm:
            c.showPage()
            y = h - margin
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margin, y, "Progress comparison (overall score)")
        y -= 8 * mm
        c.setFont("Helvetica", 8)
        for p in prior:
            c.drawString(margin, y, f"{ASSESSMENT_STAGES.get(p['assessment_stage'], p['assessment_stage'])}: {p.get('overall_score', '—')}")
            y -= 5 * mm
        c.drawString(margin, y, f"Current: {overall or '—'}")
        y -= 8 * mm

    c.setFont("Helvetica", 7)
    c.setFillColor(muted)
    c.drawString(margin, 12 * mm, "Confidential — for parent/guardian use only.")
    c.showPage()
    c.save()
    return buf.getvalue()


@router.get("/export/pdf")
async def export_assessment_pdf(
    centre: Centre,
    sport: Sport,
    player_type: PlayerType,
    assessment_stage: AssessmentStage,
    date: str,
    session: Optional[Session] = None,
    player_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    _assert_enter(user)
    _coach_scope_ok(user, centre, sport, player_type)
    if player_type == "Daily" and not session:
        raise HTTPException(400, "Session type required for Daily player type export")
    q = {
        **_batch_filter(centre, sport, player_type, assessment_stage, date, session),
        "status": {"$in": ["final", "published"]},
    }
    if player_id:
        from routers.coach import assert_player_in_coach_roster
        await assert_player_in_coach_roster(user, player_id)
        q["player_id"] = player_id
    rows = await db.player_assessments.find(q, {"_id": 0}).sort("player_name", 1).to_list(200)
    if not rows:
        raise HTTPException(404, "No finalized assessments found for export")
    incomplete = [r for r in rows if not _scores_complete(r.get("scores"), sport)]
    if incomplete:
        raise HTTPException(400, "All player scores must be completed before PDF export")

    if len(rows) == 1:
        prior = await _prior_stages_for_player(rows[0]["player_id"], assessment_stage, sport)
        pdf = _render_assessment_pdf(rows[0], prior)
        name = (rows[0].get("player_name") or "player").replace(" ", "_")
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="assessment-{name}-{date}.pdf"'},
        )

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for row in rows:
            prior = await _prior_stages_for_player(row["player_id"], assessment_stage, sport)
            pdf = _render_assessment_pdf(row, prior)
            name = (row.get("player_name") or row["player_id"]).replace(" ", "_")
            zf.writestr(f"assessment-{name}-{date}.pdf", pdf)
    zip_buf.seek(0)
    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="assessments-{date}.zip"'},
    )


async def published_for_player(player_id: str) -> list[dict]:
    rows = await db.player_assessments.find(
        {"player_id": player_id, "status": "published"},
        {"_id": 0},
    ).sort("date", -1).to_list(200)
    out = []
    for r in rows:
        if r.get("schema_version", 1) >= 2:
            out.append(_serialize_record(r, r.get("sport")))
        else:
            out.append({
                "id": r.get("id"),
                "player_id": r.get("player_id"),
                "definition_name": r.get("definition_name"),
                "date": r.get("date"),
                "sport": r.get("sport"),
                "centre": r.get("centre"),
                "session": r.get("slot"),
                "coach_remark": r.get("coach_remark"),
                "status": r.get("status"),
                "schema_version": 1,
            })
    return out


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
        from routers.coach import assert_player_in_coach_roster
        await assert_player_in_coach_roster(user, player_id)
    elif not is_admin(user) and not get_perm(user, "view_coach_assessments"):
        raise HTTPException(403, "Not allowed")
    return {"player_id": player_id, "assessments": await published_for_player(player_id)}


# Legacy definitions (admin backward compat)
class DefinitionIn(BaseModel):
    name: str
    assessment_type: Literal["rating", "score", "test"]
    sport: Sport
    centre: Optional[Centre] = None
    slot: Optional[Session] = None
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
    rows = await db.coach_assessment_definitions.find({"entity_id": ENTITY_ALPHA}, {"_id": 0}).sort("name", 1).to_list(100)
    out = []
    for r in rows:
        if sport and r.get("sport") and r["sport"] != sport:
            continue
        if centre and r.get("centre") and r["centre"] != centre:
            continue
        if slot and r.get("slot") and r["slot"] != slot:
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
