"""Player assessment — deep technical sub-parameters, 4-term layout, PDF export."""
import io
import uuid
import zipfile
from typing import Optional, List, Literal, Dict, Any, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from assessment_schema import (
    SCHEMA_VERSION,
    ASSESSMENT_STAGES,
    STAGE_ORDER,
    CORE_SCORE_KEYS,
    PARAMETERS,
    build_scores,
    normalize_scores,
    scores_complete,
    completion_status,
    score_label,
    deep_meta,
    area_keys,
    metadata_export,
    assessment_year_from_date,
    avg_non_na,
    normalize_assessment_stage,
    stage_label_for,
    stage_query_value,
)
from core import db, get_current_user, get_perm, is_admin, is_super_admin, now_utc, format_date_display, format_datetime_display
from routers.coach import _coach_visibility_filter, _coach_assignment_lists

router = APIRouter(prefix="/coach-assessments", tags=["coach-assessments"])

ENTITY_ALPHA = "alpha"

AssessmentStage = Literal[
    "assessment_1", "assessment_2", "assessment_3", "assessment_4",
    "week_1_baseline", "week_4_progress", "week_8_12_final",
]
Centre = Literal["Balua", "Harding Park"]
Sport = Literal["Cricket", "Football"]
Session = Literal["Morning", "Evening"]
PlayerType = Literal["Daily", "Day Boarding", "Hostel", "Boarding"]

SETUP_PLAYER_TYPES = ["Daily", "Day Boarding", "Hostel", "Boarding"]
AGE_GROUPS = ["U-10", "U-12", "U-14", "U-16", "U-18", "Open"]


def _tech_meta(sport: str) -> Dict[str, Dict[str, Any]]:
    return deep_meta(sport)


def _tech_keys(sport: str) -> Tuple[str, ...]:
    return area_keys(sport)


def _can_enter(user: dict) -> bool:
    from rbac.guards import can_enter_coach_assessments, can_manage_coach_assessments_admin
    return can_manage_coach_assessments_admin(user) or can_enter_coach_assessments(user)


def _can_manage(user: dict) -> bool:
    from rbac.guards import can_manage_coach_assessments_admin
    return can_manage_coach_assessments_admin(user)


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
    from coach_scope import assert_coach_sport_assigned, validate_coach_sport_param, ERR_SPORT_ACCESS
    try:
        assigned = assert_coach_sport_assigned(user)
    except ValueError as e:
        raise HTTPException(403, str(e)) from e
    centres, _ = _coach_assignment_lists(user)
    if centre and centres and centre not in centres:
        raise HTTPException(403, ERR_SPORT_ACCESS)
    if sport:
        try:
            validate_coach_sport_param(user, sport, is_admin_fn=is_admin)
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e
    elif assigned:
        pass
    if player_type and centres and centre and centre not in centres:
        raise HTTPException(403, ERR_SPORT_ACCESS)


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


def _program_label(
    sport: str,
    centre: str,
    player_type: str,
    session: Optional[str] = None,
) -> str:
    parts = [sport, player_type]
    if player_type == "Daily" and session:
        parts.append(session)
    parts.append(centre)
    return " · ".join(parts)


def _player_type_query(player_type: str) -> Any:
    if player_type == "Hostel":
        return {"$in": ["Hostel", "Hostel Only"]}
    return player_type


def _entry_to_scores(sport: str, entry: "AssessmentEntryIn") -> dict:
    detail = entry.technical_detail
    if detail is None and entry.technical_sub is not None:
        from assessment_schema import empty_technical_detail
        detail = empty_technical_detail(sport)
        for area in _tech_keys(sport):
            flat = entry.technical_sub.get(area)
            if flat is not None:
                for k in detail[area]:
                    detail[area][k] = int(flat) if int(flat) > 0 else 0
    return build_scores(
        sport,
        detail,
        entry.strength_conditioning,
        entry.game_awareness,
        entry.mental_attributes,
        entry.training_attitude,
    )


def _serialize_record(row: dict, sport: Optional[str] = None) -> dict:
    sport = sport or row.get("sport") or "Cricket"
    scores = normalize_scores(row.get("scores"), sport)
    complete = scores_complete(scores, sport)
    return {
        "id": row.get("id"),
        "player_id": row.get("player_id"),
        "player_name": row.get("player_name"),
        "centre": row.get("centre"),
        "sport": row.get("sport"),
        "player_type": row.get("player_type"),
        "session": row.get("session") or row.get("slot"),
        "assessment_stage": row.get("assessment_stage"),
        "assessment_stage_label": stage_label_for(row.get("assessment_stage") or ""),
        "assessment_year": row.get("assessment_year"),
        "date": row.get("date"),
        "scores": scores,
        "technical_detail": scores.get("technical_detail"),
        "sub_parameter_averages": scores.get("sub_parameter_averages"),
        "technical_skill_master_average": scores.get("technical_skill_master_average"),
        "overall_score": scores.get("overall_score"),
        "completion_status": completion_status(scores, sport),
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
        {"_id": 0, "id": 1, "name": 1, "age": 1, "skill_level": 1, "player_type": 1, "status": 1, "slot": 1, "date_of_admission": 1},
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
        "assessment_stage": stage_query_value(assessment_stage),
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
    completed = sum(1 for r in rows if scores_complete(r.get("scores"), sport))
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
        scores_complete(r.get("scores"), sport) for r in rows
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
    centres, sports = _coach_assignment_lists(user) if user.get("role") == "coach" else (["Balua", "Harding Park"], ["Cricket", "Football"])
    if not centres:
        centres = ["Balua", "Harding Park"]
    if not sports:
        sports = ["Cricket", "Football"]
    return {
        "schema_version": SCHEMA_VERSION,
        "stages": [{"id": k, "label": v} for k, v in ASSESSMENT_STAGES.items()],
        "player_types": SETUP_PLAYER_TYPES,
        "allowed_centres": centres,
        "allowed_sports": sports,
        "cricket_technical": metadata_export("Cricket"),
        "football_technical": metadata_export("Football"),
        "core_parameters": [
            {"key": k, "label": PARAMETERS[k]["label"], "description": PARAMETERS[k]["coach"], "parent": PARAMETERS[k]["parent"]}
            for k in CORE_SCORE_KEYS
        ],
        "score_scale": [
            {"range": "0", "label": "N/A"},
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
    technical_detail: Optional[Dict[str, Dict[str, int]]] = None
    technical_sub: Optional[Dict[str, int]] = None  # legacy v3 flat scores
    strength_conditioning: Optional[int] = Field(None, ge=0, le=10)
    game_awareness: Optional[int] = Field(None, ge=0, le=10)
    mental_attributes: Optional[int] = Field(None, ge=0, le=10)
    training_attitude: Optional[int] = Field(None, ge=0, le=10)
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
        scores = normalize_scores(m.get("scores") if m else None, sport)
        complete = scores_complete(scores, sport)
        rec_status = m.get("status") if m else None
        rows.append({
            "player_id": p["id"],
            "name": p["name"],
            "age_group": age_group_for_age(p.get("age")),
            "role": _player_role_label(p),
            "player_type": p.get("player_type"),
            "date_of_admission": p.get("date_of_admission"),
            "program": _program_label(sport, centre, player_type, session),
            "scores": scores,
            "technical_detail": scores.get("technical_detail"),
            "sub_parameter_averages": scores.get("sub_parameter_averages"),
            "technical_skill_master_average": scores.get("technical_skill_master_average"),
            "overall_score": scores.get("overall_score"),
            "completion_status": completion_status(scores, sport),
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
        "assessment_stage": normalize_assessment_stage(assessment_stage),
        "assessment_stage_label": stage_label_for(assessment_stage),
        "technical_parameters": metadata_export(sport),
        "core_parameters": [{"key": k, **PARAMETERS[k]} for k in CORE_SCORE_KEYS],
        "players": rows,
        "player_count": len(rows),
        "completed_count": batch["completed_count"],
        "batch_status": batch["batch_status"],
        "all_complete": batch["all_complete"],
        "saved_count": batch["saved_count"],
    }


async def _upsert_assessment_entry(
    user: dict,
    payload: "AssessmentBatchIn",
    entry: AssessmentEntryIn,
    player: dict,
    *,
    status: str,
    ts: str,
) -> bool:
    """Save one player assessment. Returns True if persisted."""
    existing = await db.player_assessments.find_one({
        "schema_version": SCHEMA_VERSION,
        "player_id": entry.player_id,
        "assessment_stage": stage_query_value(payload.assessment_stage),
        "date": payload.date,
    })
    if existing and existing.get("status") == "published":
        raise HTTPException(403, "Published assessments cannot be edited")
    if existing and existing.get("status") == "final" and user.get("role") == "coach":
        raise HTTPException(403, "Finalized assessments are locked. Contact admin to reopen.")

    scores = _entry_to_scores(payload.sport, entry)
    has_data = (
        scores_complete(scores, payload.sport)
        or completion_status(scores, payload.sport) != "not_started"
        or (entry.coach_remark or "").strip()
    )
    if not has_data:
        if existing and existing.get("status") not in ("final", "published"):
            await db.player_assessments.delete_one({"id": existing["id"]})
        return False

    doc = {
        "player_id": entry.player_id,
        "player_name": player["name"],
        "centre": payload.centre,
        "sport": payload.sport,
        "player_type": payload.player_type,
        "session": payload.session,
        "slot": payload.session,
        "assessment_stage": normalize_assessment_stage(payload.assessment_stage),
        "assessment_year": assessment_year_from_date(payload.date),
        "date": payload.date,
        "scores": scores,
        "coach_remark": (entry.coach_remark or "").strip()[:300] or None,
        "status": status,
        "schema_version": SCHEMA_VERSION,
        "entity_id": ENTITY_ALPHA,
        "saved_by": user["id"],
        "saved_by_name": user.get("name"),
        "entered_by": user["id"],
        "entered_by_name": user.get("name"),
        "entered_at": ts,
        "updated_at": ts,
    }
    if status == "final":
        doc["finalized_at"] = ts
        doc["finalized_by"] = user["id"]

    if existing:
        doc["created_at"] = existing.get("created_at", ts)
        await db.player_assessments.update_one({"id": existing["id"]}, {"$set": doc})
    else:
        doc["id"] = str(uuid.uuid4())
        doc["created_at"] = ts
        await db.player_assessments.insert_one(doc)
    return True


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
            scores = _entry_to_scores(payload.sport, entry)
            if not scores_complete(scores, payload.sport):
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
        if await _upsert_assessment_entry(user, payload, entry, player, status=payload.status, ts=ts):
            saved += 1

    return {"ok": True, "saved": saved, "status": payload.status}


class SinglePlayerSaveIn(BaseModel):
    centre: Centre
    sport: Sport
    player_type: PlayerType
    session: Optional[Session] = None
    assessment_stage: AssessmentStage
    date: str
    entry: AssessmentEntryIn
    status: Literal["draft"] = "draft"

    @model_validator(mode="after")
    def _daily_session_required(self):
        if self.player_type == "Daily" and not self.session:
            raise ValueError("Session type is required when player type is Daily")
        return self


@router.post("/player")
async def save_single_player_assessment(payload: SinglePlayerSaveIn, user: dict = Depends(get_current_user)):
    """Auto-save one player without submitting the full batch."""
    _assert_enter(user)
    _coach_scope_ok(user, payload.centre, payload.sport, payload.player_type)
    players = await _players_filtered(
        user, payload.centre, payload.sport, payload.player_type, session=payload.session,
    )
    valid_ids = {p["id"]: p for p in players}
    if payload.entry.player_id not in valid_ids:
        raise HTTPException(403, "Player is not in your assigned roster")
    ts = now_utc().isoformat()
    batch = AssessmentBatchIn(
        centre=payload.centre,
        sport=payload.sport,
        player_type=payload.player_type,
        session=payload.session,
        assessment_stage=payload.assessment_stage,
        date=payload.date,
        entries=[payload.entry],
        status="draft",
    )
    saved = await _upsert_assessment_entry(
        user, batch, payload.entry, valid_ids[payload.entry.player_id], status="draft", ts=ts,
    )
    scores = _entry_to_scores(payload.sport, payload.entry)
    return {
        "ok": True,
        "saved": saved,
        "scores": scores,
        "completion_status": completion_status(scores, payload.sport),
        "complete": scores_complete(scores, payload.sport),
    }


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
        "assessment_stage": normalize_assessment_stage(payload.assessment_stage),
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


async def _year_assessments_for_player(player_id: str, year: int, sport: str) -> list[dict]:
    rows = await db.player_assessments.find(
        {
            "player_id": player_id,
            "assessment_year": year,
            "status": {"$in": ["final", "published"]},
            "schema_version": {"$gte": 3},
        },
        {"_id": 0},
    ).sort("date", 1).to_list(20)
    by_stage: Dict[str, dict] = {}
    for r in rows:
        stage = normalize_assessment_stage(r.get("assessment_stage") or "")
        if stage in STAGE_ORDER and stage not in by_stage:
            rec = _serialize_record(r, sport)
            rec["assessment_stage"] = stage
            rec["assessment_stage_label"] = stage_label_for(stage)
            by_stage[stage] = rec
    return [by_stage[s] for s in STAGE_ORDER if s in by_stage]


def _year_summary_rows(year_records: list[dict], sport: str) -> list[dict]:
    """Build comparison table rows for year summary (4 assessment columns)."""
    by_stage = {r.get("assessment_stage"): r for r in year_records}
    param_defs: List[Tuple[str, str, str]] = []
    for area in _tech_keys(sport):
        meta = _tech_meta(sport)[area]
        param_defs.append((area, f"{meta['label']} Average", "area"))
    param_defs.extend([
        ("technical_skill", "Technical Skill", "tech_master"),
        ("strength_conditioning", "S&C", "core"),
        ("game_awareness", "Game Awareness", "core"),
        ("mental_attributes", "Mental Attributes", "core"),
        ("training_attitude", "Training Attitude", "core"),
        ("overall_score", "Overall Score", "overall"),
    ])

    out = []
    for key, label, kind in param_defs:
        vals: List[Optional[float]] = []
        for stage in STAGE_ORDER:
            rec = by_stage.get(stage)
            scores = (rec or {}).get("scores") or {}
            if kind == "area":
                v = (scores.get("sub_parameter_averages") or {}).get(key)
            elif kind == "tech_master":
                v = scores.get("technical_skill_master_average")
            elif kind == "overall":
                v = scores.get("overall_score")
            else:
                raw = scores.get(key)
                v = float(raw) if raw is not None and int(raw) > 0 else None
            vals.append(v)
        scored = [v for v in vals if v is not None]
        change = None
        if len(scored) >= 2:
            first = next((v for v in vals if v is not None), None)
            last = next((v for v in reversed(vals) if v is not None), None)
            if first is not None and last is not None:
                change = round(last - first, 1)
        out.append({"key": key, "label": label, "values": vals, "change": change})
    return out


@router.get("/year-summary/{player_id}")
async def year_summary(
    player_id: str,
    year: Optional[int] = None,
    user: dict = Depends(get_current_user),
):
    _assert_enter(user)
    person = await db.people.find_one({"id": player_id, "kind": "player"}, {"_id": 0})
    if not person:
        raise HTTPException(404, "Player not found")
    if user.get("role") == "coach":
        from routers.coach import assert_player_in_coach_roster
        await assert_player_in_coach_roster(user, player_id)
    sport = person.get("sport") or "Cricket"
    yr = year or now_utc().year
    records = await _year_assessments_for_player(player_id, yr, sport)
    rows = _year_summary_rows(records, sport)
    return {
        "player_id": player_id,
        "player_name": person.get("name"),
        "sport": sport,
        "assessment_year": yr,
        "stages": [{"id": s, "label": ASSESSMENT_STAGES[s]} for s in STAGE_ORDER],
        "assessments": records,
        "comparison_rows": rows,
        "completed_count": len(records),
    }


async def _prior_stages_for_player(player_id: str, current_stage: str, sport: str, year: Optional[int] = None) -> list[dict]:
    yr = year or now_utc().year
    records = await _year_assessments_for_player(player_id, yr, sport)
    idx = STAGE_ORDER.index(current_stage) if current_stage in STAGE_ORDER else -1
    if idx <= 0:
        return []
    prior_keys = set(STAGE_ORDER[:idx])
    return [r for r in records if normalize_assessment_stage(r.get("assessment_stage") or "") in prior_keys]


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


def _draw_progress_chart(c, x: float, y: float, w: float, h: float, year_records: list[dict], sport: str) -> float:
    """Draw multi-line progress chart. Returns new y below chart."""
    from reportlab.lib import colors as rl_colors

    if len(year_records) < 2:
        return y

    rows = _year_summary_rows(year_records, sport)
    if not rows:
        return y

    chart_keys = [r["key"] for r in rows if r["key"] in ("overall_score", "technical_skill") or r["key"] in _tech_keys(sport)][:8]
    palette = [
        rl_colors.HexColor("#1E3A8A"),
        rl_colors.HexColor("#DC2626"),
        rl_colors.HexColor("#059669"),
        rl_colors.HexColor("#D97706"),
        rl_colors.HexColor("#7C3AED"),
        rl_colors.HexColor("#0891B2"),
        rl_colors.HexColor("#BE185D"),
        rl_colors.HexColor("#374151"),
    ]
    stage_labels = [stage_label_for(normalize_assessment_stage(r.get("assessment_stage", "")))[:12] for r in year_records]
    n_pts = len(year_records)
    margin_l, margin_b = 8, 14
    plot_w = w - margin_l - 4
    plot_h = h - margin_b - 8
    base_y = y - h

    c.setStrokeColor(rl_colors.HexColor("#E2E8F0"))
    c.setLineWidth(0.5)
    c.rect(x, base_y, w, h, stroke=1, fill=0)
    for tick in range(0, 11, 2):
        ty = base_y + margin_b + (tick / 10.0) * plot_h
        c.line(x + margin_l, ty, x + w - 4, ty)
        c.setFont("Helvetica", 6)
        c.setFillColor(rl_colors.HexColor("#94A3B8"))
        c.drawString(x, ty - 2, str(tick))

    row_by_key = {r["key"]: r for r in rows}
    legend_y = y + 4
    for i, key in enumerate(chart_keys):
        row = row_by_key.get(key)
        if not row:
            continue
        color = palette[i % len(palette)]
        vals = row["values"][:n_pts]
        pts = []
        for j, v in enumerate(vals):
            if v is None:
                continue
            px = x + margin_l + (j / max(n_pts - 1, 1)) * plot_w
            py = base_y + margin_b + (float(v) / 10.0) * plot_h
            pts.append((px, py))
        if len(pts) < 2:
            continue
        c.setStrokeColor(color)
        c.setLineWidth(2.5 if key == "overall_score" else 1.2)
        path = c.beginPath()
        path.moveTo(pts[0][0], pts[0][1])
        for px, py in pts[1:]:
            path.lineTo(px, py)
        c.drawPath(path, stroke=1, fill=0)
        for px, py in pts:
            c.setFillColor(color)
            c.circle(px, py, 2, fill=1, stroke=0)
        c.setFont("Helvetica", 6)
        c.setFillColor(color)
        lbl = row["label"][:18]
        c.drawString(x + w + 6, legend_y - i * 8, lbl)

    c.setFont("Helvetica", 6)
    c.setFillColor(rl_colors.HexColor("#64748B"))
    for j, lbl in enumerate(stage_labels):
        px = x + margin_l + (j / max(n_pts - 1, 1)) * plot_w
        c.drawCentredString(px, base_y + 2, lbl[:10])
    return base_y - 8


def _render_assessment_pdf(record: dict, year_records: list[dict]) -> bytes:
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    sport = record.get("sport") or "Cricket"
    scores = normalize_scores(record.get("scores"), sport)
    tech_meta = _tech_meta(sport)
    year_records = year_records or [record]
    if record.get("id") and not any(r.get("id") == record.get("id") for r in year_records):
        year_records = sorted(
            year_records + [_serialize_record(record, sport)],
            key=lambda x: STAGE_ORDER.index(normalize_assessment_stage(x.get("assessment_stage", "assessment_1")))
            if normalize_assessment_stage(x.get("assessment_stage", "")) in STAGE_ORDER else 99,
        )

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    w, h = A4
    margin = 18 * mm
    y = h - margin
    navy = rl_colors.HexColor("#1E3A8A")
    muted = rl_colors.HexColor("#64748B")

    def new_page():
        nonlocal y
        c.showPage()
        y = h - margin

    def ensure_space(need_mm: float):
        nonlocal y
        if y < need_mm * mm:
            new_page()

    # ---- Page 1: Profile & summary ----
    if record.get("status") == "draft":
        c.saveState()
        c.setFillColor(rl_colors.HexColor("#FEF3C7"))
        c.setFont("Helvetica-Bold", 36)
        c.translate(w / 2, h / 2)
        c.rotate(35)
        c.drawCentredString(0, 0, "DRAFT")
        c.restoreState()

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
        session_txt = ""
    meta = (
        f"{record.get('sport', '—')}  ·  {record.get('centre', '—')}  ·  "
        f"{record.get('player_type', '—')}"
        + (f"  ·  {session_txt}" if session_txt else "")
        + f"  ·  {stage_label_for(record.get('assessment_stage', ''))}"
    )
    program = record.get("program") or meta
    c.drawString(margin, y, f"Program: {program}")
    y -= 5 * mm
    if record.get("date_of_admission"):
        c.drawString(margin, y, f"Date of joining: {format_date_display(record.get('date_of_admission'))}")
        y -= 5 * mm
    c.drawString(margin, y, f"Assessment date: {format_date_display(record.get('date'))}")
    y -= 5 * mm
    c.drawString(margin, y, f"Assessment term: {stage_label_for(record.get('assessment_stage', ''))}")
    y -= 5 * mm
    c.drawString(margin, y, f"Assessed by: {record.get('saved_by_name') or '—'} on {format_datetime_display(record.get('updated_at') or record.get('created_at'))}")
    y -= 10 * mm

    overall = scores.get("overall_score")
    if overall is not None:
        c.setFillColor(navy)
        c.roundRect(margin, y - 10 * mm, 55 * mm, 12 * mm, 3 * mm, fill=1, stroke=0)
        c.setFillColor(rl_colors.white)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin + 4 * mm, y - 7 * mm, f"Overall: {overall} / 10")
        y -= 16 * mm
    c.setFillColor(muted)
    c.setFont("Helvetica", 8)
    c.drawString(margin, y, "0 = N/A | 1–3 Beginner | 4–5 Developing | 6–7 Good | 8–9 Very Good | 10 Elite")
    y -= 10 * mm
    c.setFillColor(rl_colors.black)

    tech_master = scores.get("technical_skill_master_average")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, y, f"Technical Skill: {tech_master}/10" if tech_master else "Technical Skill: —")
    y -= 6 * mm
    for key in CORE_SCORE_KEYS:
        val = scores.get(key)
        c.setFont("Helvetica", 10)
        lbl = PARAMETERS[key]["label"]
        if val is not None and int(val) > 0:
            c.drawString(margin, y, f"{lbl}: {val}/10 — {score_label(int(val))}")
        elif val == 0:
            c.drawString(margin, y, f"{lbl}: N/A")
        else:
            c.drawString(margin, y, f"{lbl}: —")
        y -= 5 * mm

    # ---- Page 2: Technical detail ----
    new_page()
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, y, "Technical Skill Detail")
    y -= 10 * mm
    detail = scores.get("technical_detail") or {}
    sub_avgs = scores.get("sub_parameter_averages") or {}
    for area in _tech_keys(sport):
        ensure_space(45)
        ameta = tech_meta[area]
        avg = sub_avgs.get(area)
        c.setFont("Helvetica-Bold", 11)
        avg_txt = f" — {avg}/10" if avg is not None else ""
        c.drawString(margin, y, f"{ameta['label']}{avg_txt}")
        y -= 5 * mm
        c.setFont("Helvetica", 8)
        c.setFillColor(muted)
        for line in _wrap_text(ameta.get("parent", ""), 95):
            c.drawString(margin, y, line)
            y -= 3.5 * mm
        c.setFillColor(rl_colors.black)
        subs = detail.get(area) or {}
        for sk, (slabel, scoach) in ameta["sub_params"].items():
            ensure_space(18)
            val = subs.get(sk)
            c.setFont("Helvetica-Bold", 9)
            if val is not None and int(val) > 0:
                score_txt = f"{val}/10 — {score_label(int(val))}"
            elif val == 0:
                score_txt = "N/A"
            else:
                score_txt = "—"
            c.drawString(margin + 2 * mm, y, f"{slabel}: {score_txt}")
            y -= 3.5 * mm
            c.setFont("Helvetica", 7)
            c.setFillColor(muted)
            for line in _wrap_text(scoach, 92):
                c.drawString(margin + 4 * mm, y, line)
                y -= 3 * mm
            c.setFillColor(rl_colors.black)
        y -= 3 * mm

    ensure_space(12)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, y, f"Technical Skill Master Average: {tech_master or '—'} / 10")
    y -= 8 * mm

    # ---- Page 3: Other parameters & remarks ----
    new_page()
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, y, "Other Parameters & Remarks")
    y -= 10 * mm
    for key in CORE_SCORE_KEYS:
        ensure_space(25)
        val = scores.get(key)
        c.setFont("Helvetica-Bold", 11)
        if val is not None and int(val) > 0:
            c.drawString(margin, y, f"{PARAMETERS[key]['label']}: {val}/10 — {score_label(int(val))}")
        elif val == 0:
            c.drawString(margin, y, f"{PARAMETERS[key]['label']}: N/A")
        else:
            c.drawString(margin, y, f"{PARAMETERS[key]['label']}: —")
        y -= 5 * mm
        c.setFont("Helvetica", 8)
        c.setFillColor(muted)
        for line in _wrap_text(PARAMETERS[key]["parent"], 95):
            c.drawString(margin, y, line)
            y -= 3.5 * mm
        c.setFillColor(rl_colors.black)
        y -= 4 * mm

    if record.get("coach_remark"):
        ensure_space(30)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margin, y, "Coach Remark")
        y -= 7 * mm
        c.setFont("Helvetica", 9)
        for line in _wrap_text(record["coach_remark"], 95):
            c.drawString(margin, y, line)
            y -= 5 * mm

    # ---- Page 4: Year progress (if 2+ assessments) ----
    if len(year_records) >= 2:
        new_page()
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margin, y, "Year Progress")
        y -= 8 * mm
        summary = _year_summary_rows(year_records, sport)
        c.setFont("Helvetica-Bold", 8)
        headers = ["Parameter"] + [f"A{i+1}" for i in range(4)] + ["Change"]
        col_w = [42 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 16 * mm]
        x0 = margin
        for i, hdr in enumerate(headers):
            c.drawString(x0 + sum(col_w[:i]), y, hdr)
        y -= 5 * mm
        c.setFont("Helvetica", 7)
        for row in summary:
            ensure_space(8)
            cells = [row["label"][:22]]
            vals = (row["values"] + [None, None, None, None])[:4]
            for v in vals:
                cells.append(f"{v:.1f}" if v is not None else "—")
            ch = row.get("change")
            if ch is not None:
                cells.append(f"{'+' if ch >= 0 else ''}{ch:.1f} {'↑' if ch > 0 else '↓' if ch < 0 else ''}".strip())
            else:
                cells.append("—")
            for i, cell in enumerate(cells):
                weight = "Helvetica-Bold" if row["key"] == "overall_score" else "Helvetica"
                c.setFont(weight, 7)
                c.drawString(x0 + sum(col_w[:i]), y, str(cell)[:14])
            y -= 4 * mm
        y -= 6 * mm
        ensure_space(55)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(margin, y, "Progress Chart")
        y -= 4 * mm
        y = _draw_progress_chart(c, margin, y, w - 2 * margin - 30 * mm, 45 * mm, year_records, sport)

    c.setFont("Helvetica", 7)
    c.setFillColor(muted)
    c.drawString(margin, 12 * mm, "Confidential — for parent/guardian use only.")
    c.showPage()
    c.save()
    return buf.getvalue()


class PlayerReportPdfIn(BaseModel):
    centre: Centre
    sport: Sport
    player_type: PlayerType
    session: Optional[Session] = None
    assessment_stage: AssessmentStage
    date: str
    entry: AssessmentEntryIn

    @model_validator(mode="after")
    def _daily_session_required(self):
        if self.player_type == "Daily" and not self.session:
            raise ValueError("Session type is required when player type is Daily")
        return self


@router.post("/export/pdf/report")
async def export_player_report_pdf(
    payload: PlayerReportPdfIn,
    user: dict = Depends(get_current_user),
):
    """Generate a player assessment summary PDF from current entry data (draft or saved)."""
    _assert_enter(user)
    _coach_scope_ok(user, payload.centre, payload.sport, payload.player_type)
    from routers.coach import assert_player_in_coach_roster

    person = await db.people.find_one(
        {"id": payload.entry.player_id, "kind": "player"},
        {"_id": 0, "id": 1, "name": 1, "date_of_admission": 1},
    )
    if not person:
        raise HTTPException(404, "Player not found")
    await assert_player_in_coach_roster(user, payload.entry.player_id)

    scores = _entry_to_scores(payload.sport, payload.entry)
    ts = now_utc().isoformat()
    record = {
        "player_id": payload.entry.player_id,
        "player_name": person["name"],
        "centre": payload.centre,
        "sport": payload.sport,
        "player_type": payload.player_type,
        "session": payload.session,
        "assessment_stage": normalize_assessment_stage(payload.assessment_stage),
        "date": payload.date,
        "scores": scores,
        "coach_remark": (payload.entry.coach_remark or "").strip() or None,
        "status": "draft",
        "saved_by_name": user.get("name"),
        "updated_at": ts,
        "created_at": ts,
        "date_of_admission": person.get("date_of_admission"),
        "program": _program_label(payload.sport, payload.centre, payload.player_type, payload.session),
    }
    year = assessment_year_from_date(payload.date)
    year_records = await _year_assessments_for_player(payload.entry.player_id, year, payload.sport)
    pdf = _render_assessment_pdf(record, year_records)
    name = (person.get("name") or "player").replace(" ", "_")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="assessment-{name}-{payload.date}.pdf"'},
    )


async def _enrich_record_for_pdf(record: dict, sport: str) -> dict:
    person = await db.people.find_one(
        {"id": record.get("player_id"), "kind": "player"},
        {"_id": 0, "date_of_admission": 1},
    )
    out = dict(record)
    if person:
        out["date_of_admission"] = person.get("date_of_admission")
    out["program"] = _program_label(
        out.get("sport") or sport,
        out.get("centre") or "—",
        out.get("player_type") or "—",
        out.get("session") or out.get("slot"),
    )
    return out


@router.get("/export/pdf")
async def export_assessment_pdf(
    centre: Centre,
    sport: Sport,
    player_type: PlayerType,
    assessment_stage: AssessmentStage,
    date: str,
    session: Optional[Session] = None,
    player_id: Optional[str] = None,
    completed_only: bool = False,
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
    if completed_only:
        rows = [r for r in rows if scores_complete(r.get("scores"), sport)]
        if not rows:
            raise HTTPException(404, "No completed player assessments found for export")
    incomplete = [r for r in rows if not scores_complete(r.get("scores"), sport)]
    if incomplete and not completed_only:
        raise HTTPException(400, "All player scores must be completed before PDF export")

    year = assessment_year_from_date(date)

    if len(rows) == 1:
        row = await _enrich_record_for_pdf(rows[0], sport)
        year_records = await _year_assessments_for_player(row["player_id"], year, sport)
        pdf = _render_assessment_pdf(row, year_records)
        name = (row.get("player_name") or "player").replace(" ", "_")
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="assessment-{name}-{date}.pdf"'},
        )

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for row in rows:
            enriched = await _enrich_record_for_pdf(row, sport)
            year_records = await _year_assessments_for_player(enriched["player_id"], year, sport)
            pdf = _render_assessment_pdf(enriched, year_records)
            name = (enriched.get("player_name") or enriched["player_id"]).replace(" ", "_")
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
