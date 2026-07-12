"""Player assessment v4 — deep technical sub-parameters, 4-term layout, 0–10 scale."""
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 4

AssessmentStage = str  # assessment_1 .. assessment_4

ASSESSMENT_STAGES: Dict[str, str] = {
    "assessment_1": "Assessment 1 — Term 1 (Jan–Mar)",
    "assessment_2": "Assessment 2 — Term 2 (Apr–Jun)",
    "assessment_3": "Assessment 3 — Term 3 (Jul–Sep)",
    "assessment_4": "Assessment 4 — Term 4 (Oct–Dec)",
}
STAGE_ORDER = ["assessment_1", "assessment_2", "assessment_3", "assessment_4"]

# Legacy v3 stage IDs accepted on input and mapped to v4 on save/query
LEGACY_STAGE_ALIASES: Dict[str, str] = {
    "week_1_baseline": "assessment_1",
    "week_4_progress": "assessment_2",
    "week_8_12_final": "assessment_3",
}
LEGACY_STAGE_LABELS: Dict[str, str] = {
    "week_1_baseline": "Week 1 - Baseline",
    "week_4_progress": "Week 4 - Progress",
    "week_8_12_final": "Week 8-12 - Final",
}

ALL_STAGE_IDS = tuple(STAGE_ORDER) + tuple(LEGACY_STAGE_ALIASES.keys())


def normalize_assessment_stage(stage: str) -> str:
    return LEGACY_STAGE_ALIASES.get(stage, stage)


def stage_label_for(stage: str) -> str:
    normalized = normalize_assessment_stage(stage)
    if normalized in ASSESSMENT_STAGES:
        return ASSESSMENT_STAGES[normalized]
    return LEGACY_STAGE_LABELS.get(stage, stage)


def stage_query_value(stage: str) -> Any:
    """Match v4 and legacy v3 records for the same assessment period."""
    normalized = normalize_assessment_stage(stage)
    keys = {normalized}
    for legacy, target in LEGACY_STAGE_ALIASES.items():
        if target == normalized:
            keys.add(legacy)
    if len(keys) == 1:
        return next(iter(keys))
    return {"$in": list(keys)}

CORE_SCORE_KEYS = (
    "strength_conditioning",
    "game_awareness",
    "mental_attributes",
    "training_attitude",
)

PARAMETERS: Dict[str, Dict[str, str]] = {
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

# Cricket deep technical structure
CRICKET_DEEP: Dict[str, Dict[str, Any]] = {
    "batting": {
        "label": "Batting",
        "parent": "How well your child bats, including technique, footwork, timing and ability to score runs.",
        "sub_params": {
            "technique": ("Technique", "Grip, stance, backlift and overall batting mechanics"),
            "footwork": ("Footwork", "Movement to the pitch of the ball, both forward and back"),
            "shot_selection": ("Shot Selection", "Choosing the right shot for the ball and match situation"),
            "timing": ("Timing", "Clean contact and timing of the ball off the bat"),
            "scoring_ability": ("Scoring Ability", "Ability to rotate strike, find boundaries and build innings"),
            "temperament": ("Temperament", "Patience, composure and decision-making under pressure"),
        },
    },
    "bowling": {
        "label": "Bowling",
        "parent": "Your child's bowling skill, including action, accuracy, pace or spin, and ability to take wickets.",
        "sub_params": {
            "action_run_up": ("Action & Run-Up", "Smoothness, repeatability and legality of action"),
            "line_length": ("Line & Length", "Consistency in hitting the right areas"),
            "pace_spin_control": ("Pace / Spin Control", "Ability to vary pace or generate/control spin"),
            "swing_seam_turn": ("Swing / Seam / Turn", "Movement off the pitch or through the air"),
            "variation": ("Variation", "Ability to bowl different deliveries"),
            "wicket_taking": ("Wicket-Taking Ability", "Ability to create and convert chances"),
        },
    },
    "fielding": {
        "label": "Fielding",
        "parent": "How well your child fields, including catching, ground fielding and throwing accuracy.",
        "sub_params": {
            "catching": ("Catching", "Reliability in the air, both close-in and outfield"),
            "ground_fielding": ("Ground Fielding", "Stopping the ball, picking up cleanly, diving"),
            "throwing_accuracy": ("Throwing Accuracy", "Hitting the stumps and returning to the keeper/bowler"),
            "athleticism": ("Athleticism & Agility", "Speed, agility and range in the field"),
            "positioning": ("Positioning", "Reading the game and setting up in the right position"),
            "communication": ("Communication", "Calling, backing up and team awareness in the field"),
        },
    },
    "wicket_keeping": {
        "label": "Wicket-Keeping",
        "parent": "Your child's glove work, positioning and ability to take catches and effect stumpings (if applicable).",
        "sub_params": {
            "glove_work": ("Glove Work", "Clean takes, catches and dismissals"),
            "footwork_positioning": ("Footwork & Positioning", "Moving efficiently to take the ball"),
            "stumpings": ("Stumpings", "Speed and accuracy in effecting stumpings"),
            "catching_down_leg": ("Catching Down Leg", "Ability to take edge and leg-side deliveries"),
            "communication": ("Communication", "Leading the field, calling and directing"),
            "standing_up_back": ("Standing Up / Back", "Ability to keep both up to the stumps and back"),
        },
    },
    "running_between_wickets": {
        "label": "Running Between Wickets",
        "parent": "Your child's ability to convert singles, communicate with their batting partner and make smart running decisions.",
        "sub_params": {
            "calling": ("Calling", "Clear, decisive calling (Yes / No / Wait)"),
            "running_speed": ("Running Speed", "Ground speed when running between wickets"),
            "backing_up": ("Backing Up", "Starting position and backing up at the non-striker's end"),
            "conversion_singles": ("Conversion of Singles", "Ability to turn ones into twos"),
            "partner_communication": ("Communication with Partner", "Understanding and trust with batting partner"),
            "decision_making": ("Decision-Making", "Judging runs correctly under pressure"),
        },
    },
    "cricket_iq": {
        "label": "Cricket IQ",
        "parent": "How well your child reads the game, understands match situations and makes tactical decisions.",
        "sub_params": {
            "match_situation": ("Match Situation Awareness", "Understanding game state (overs, run rate, wickets)"),
            "tactical_decisions": ("Tactical Decision-Making", "Choosing the right option in the moment"),
            "pressure_management": ("Pressure Management", "Performing in high-pressure situations"),
            "role_clarity": ("Role Clarity", "Understanding their specific role in the team"),
            "reading_opposition": ("Reading the Opposition", "Identifying weaknesses and adapting"),
            "learning_adaptability": ("Learning & Adaptability", "Applying feedback and improving between sessions"),
        },
    },
}

FOOTBALL_DEEP: Dict[str, Dict[str, Any]] = {
    "dribbling": {
        "label": "Dribbling",
        "parent": "Your child's ability to control the ball, move past opponents and maintain possession under pressure.",
        "sub_params": {
            "close_control": ("Close Control", "Keeping the ball close to feet under pressure"),
            "change_direction": ("Change of Direction", "Speed and sharpness of turns and changes"),
            "ball_manipulation": ("Ball Manipulation", "Using different surfaces of the foot creatively"),
            "beating_opponents": ("Beating Opponents", "Success rate when attempting to go past a defender"),
            "speed_with_ball": ("Speed with Ball", "Maintaining control at pace"),
            "decision_to_dribble": ("Decision to Dribble", "Knowing when to dribble vs pass"),
        },
    },
    "passing": {
        "label": "Passing",
        "parent": "How accurately and intelligently your child passes the ball, including range and timing.",
        "sub_params": {
            "short_pass_accuracy": ("Short Pass Accuracy", "Reliability of short-range passing"),
            "long_pass_range": ("Long Pass Range", "Ability to switch play and play long passes accurately"),
            "weight_of_pass": ("Weight of Pass", "Delivering the ball at the right pace for the receiver"),
            "vision": ("Vision", "Seeing options before receiving the ball"),
            "timing_of_pass": ("Timing of Pass", "Playing the ball at the right moment"),
            "decision_making": ("Decision-Making", "Choosing the right pass option in each situation"),
        },
    },
    "shooting": {
        "label": "Shooting",
        "parent": "Your child's ability to shoot with technique, accuracy and power, and to stay composed in front of goal.",
        "sub_params": {
            "technique": ("Technique", "Correct striking technique (laces, instep, volley)"),
            "accuracy": ("Accuracy", "Placing the shot where intended"),
            "power": ("Power", "Generating pace on the shot"),
            "composure": ("Composure", "Staying calm and decisive in front of goal"),
            "movement_before_shot": ("Movement Before Shot", "Creating space and positioning to shoot"),
            "first_time_finishing": ("First-Time Finishing", "Ability to shoot without controlling first"),
        },
    },
    "defending": {
        "label": "Defending",
        "parent": "Your child's positioning, tackling ability and effectiveness at winning the ball back for the team.",
        "sub_params": {
            "positioning": ("Positioning", "Maintaining correct defensive shape and position"),
            "tackling": ("Tackling", "Winning the ball cleanly (slide, block and standing tackle)"),
            "marking": ("Marking", "Tracking runners and staying tight to opponents"),
            "interceptions": ("Interceptions", "Reading passes and cutting them out"),
            "aerial_duels": ("Aerial Duels", "Winning headers in defensive situations"),
            "concentration": ("Concentration", "Staying alert and focused throughout the match"),
        },
    },
    "heading": {
        "label": "Heading",
        "parent": "Your child's aerial ability, including technique and timing when heading the ball in attack and defence.",
        "sub_params": {
            "technique": ("Technique", "Heading through the ball correctly"),
            "timing": ("Timing", "Meeting the ball at the right moment"),
            "direction": ("Direction", "Directing headers accurately"),
            "aerial_presence": ("Aerial Presence", "Winning headers against opponents"),
            "attacking_headers": ("Attacking Headers", "Heading towards goal with power and accuracy"),
            "defensive_headers": ("Defensive Headers", "Clearing the ball with distance and safety"),
        },
    },
    "football_iq": {
        "label": "Football IQ",
        "parent": "How well your child reads the game, positions themselves off the ball and understands team tactics.",
        "sub_params": {
            "off_ball_movement": ("Off-the-Ball Movement", "Creating space and making runs without the ball"),
            "pressing": ("Pressing", "Pressing intelligently to win the ball back high up the pitch"),
            "positional_discipline": ("Positional Discipline", "Holding shape and position in the team structure"),
            "transition_awareness": ("Transition Awareness", "Reacting quickly when possession changes"),
            "reading_game": ("Reading the Game", "Anticipating play before it happens"),
            "tactical_understanding": ("Tactical Understanding", "Understanding the team's system and their role"),
        },
    },
}


def deep_meta(sport: str) -> Dict[str, Dict[str, Any]]:
    return CRICKET_DEEP if sport == "Cricket" else FOOTBALL_DEEP


def area_keys(sport: str) -> Tuple[str, ...]:
    return tuple(deep_meta(sport).keys())


def score_label(value: int) -> str:
    if value == 0:
        return "N/A"
    if value <= 3:
        return "Beginner"
    if value <= 5:
        return "Developing"
    if value <= 7:
        return "Good"
    if value <= 9:
        return "Very Good"
    return "Elite"


def avg_non_na(values: List[Optional[int]]) -> Optional[float]:
    scored = [int(v) for v in values if v is not None and int(v) > 0]
    if not scored:
        return None
    return round(sum(scored) / len(scored), 1)


def empty_technical_detail(sport: str) -> Dict[str, Dict[str, Optional[int]]]:
    out: Dict[str, Dict[str, Optional[int]]] = {}
    for area, meta in deep_meta(sport).items():
        out[area] = {k: None for k in meta["sub_params"]}
    return out


def build_scores(
    sport: str,
    technical_detail: Optional[Dict[str, Dict[str, Any]]],
    strength_conditioning: Optional[int] = None,
    game_awareness: Optional[int] = None,
    mental_attributes: Optional[int] = None,
    training_attitude: Optional[int] = None,
) -> dict:
    detail = empty_technical_detail(sport)
    if technical_detail:
        for area in area_keys(sport):
            if area in technical_detail:
                for k in detail[area]:
                    v = technical_detail[area].get(k)
                    detail[area][k] = int(v) if v is not None else None

    sub_avgs: Dict[str, Optional[float]] = {}
    for area, subs in detail.items():
        sub_avgs[area] = avg_non_na(list(subs.values()))

    area_avg_values = [v for v in sub_avgs.values() if v is not None]
    tech_master = round(sum(area_avg_values) / len(area_avg_values), 1) if area_avg_values else None

    core = {
        "strength_conditioning": strength_conditioning,
        "game_awareness": game_awareness,
        "mental_attributes": mental_attributes,
        "training_attitude": training_attitude,
    }

    overall_parts: List[float] = []
    if tech_master is not None:
        overall_parts.append(tech_master)
    for k in CORE_SCORE_KEYS:
        v = core[k]
        if v is not None and int(v) > 0:
            overall_parts.append(float(v))
    overall = round(sum(overall_parts) / len(overall_parts), 1) if len(overall_parts) == 5 else None

    return {
        "technical_detail": detail,
        "sub_parameter_averages": sub_avgs,
        "technical_skill_master_average": tech_master,
        **core,
        "overall_score": overall,
    }


def _area_complete(subs: Dict[str, Optional[int]]) -> bool:
    return all(v is not None for v in subs.values())


def scores_complete(scores: Optional[dict], sport: str) -> bool:
    if not scores:
        return False
    detail = scores.get("technical_detail") or {}
    for area in area_keys(sport):
        if area not in detail or not _area_complete(detail[area]):
            return False
    return all(scores.get(k) is not None for k in CORE_SCORE_KEYS)


def completion_status(scores: Optional[dict], sport: str) -> str:
    if not scores:
        return "not_started"
    detail = scores.get("technical_detail") or {}
    any_score = any(
        subs.get(sk) is not None
        for area in area_keys(sport)
        for sk in (detail.get(area) or {})
    )
    any_score = any_score or any(scores.get(k) is not None for k in CORE_SCORE_KEYS)
    if not any_score:
        return "not_started"
    if scores_complete(scores, sport):
        return "completed"
    return "in_progress"


def normalize_scores(raw: Optional[dict], sport: str) -> dict:
    if not raw:
        return build_scores(sport, None)
    if raw.get("technical_detail"):
        return build_scores(
            sport,
            raw.get("technical_detail"),
            raw.get("strength_conditioning"),
            raw.get("game_awareness"),
            raw.get("mental_attributes"),
            raw.get("training_attitude"),
        )
    # v3 flat technical_sub migration
    if raw.get("technical_sub"):
        detail = empty_technical_detail(sport)
        for area in area_keys(sport):
            flat = raw["technical_sub"].get(area)
            if flat is not None:
                for k in detail[area]:
                    detail[area][k] = int(flat) if int(flat) > 0 else 0
        return build_scores(
            sport, detail,
            raw.get("strength_conditioning"),
            raw.get("game_awareness"),
            raw.get("mental_attributes"),
            raw.get("training_attitude"),
        )
    return build_scores(sport, None)


def metadata_export(sport: str) -> List[dict]:
    out = []
    for area, meta in deep_meta(sport).items():
        subs = []
        for key, (label, coach) in meta["sub_params"].items():
            subs.append({"key": key, "label": label, "coach": coach})
        out.append({
            "key": area,
            "label": meta["label"],
            "parent": meta["parent"],
            "sub_params": subs,
        })
    return out


def assessment_year_from_date(date_str: str) -> int:
    return int(date_str[:4]) if date_str and len(date_str) >= 4 else 0
