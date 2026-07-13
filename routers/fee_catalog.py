"""Fee catalogue and fee plans MVP — configuration layer before invoice engine.

Legacy `fees` collection and paid history are untouched. Catalogue/plans drive
new auto-generation; hardcoded RATE_CARDS remain as fallback.
"""
import uuid
from typing import Optional, Literal, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from core import db, get_current_user, get_perm, is_super_admin, now_utc, derive_person_entities

router = APIRouter(prefix="/fee-catalog", tags=["fee-catalog"])

ENTITY_IDS = ("alpha", "pws")
FEE_TYPES = (
    "tuition", "transport", "hostel", "examination", "coaching",
    "registration", "uniform", "kit", "tournament",
)
FREQUENCIES = ("monthly", "quarterly", "term_wise", "annual", "one_time")

# Maps catalogue fee_type → legacy db.fees fee_type string
LEGACY_FEE_TYPE = {
    "tuition": "Monthly",
    "coaching": "Monthly",
    "registration": "Registration",
    "transport": "Transport",
    "hostel": "Hostel",
    "examination": "Exam",
    "uniform": "Uniform",
    "kit": "Kit",
    "tournament": "Tournament",
}

# Maps catalogue fee_type → rate-card resolver keys
RATE_KEYS = {
    "tuition": "monthly",
    "coaching": "monthly",
    "registration": "registration",
    "transport": "transport",
    "hostel": "hostel_monthly",
    "examination": "exam",
}


from rbac.guards import can_manage_fee_heads, can_collect_pws_fees, can_collect_alpha_fees


def _can_manage(user: dict) -> bool:
    return can_manage_fee_heads(user)


def _can_view(user: dict) -> bool:
    return _can_manage(user) or can_collect_pws_fees(user) or can_collect_alpha_fees(user)


def _assert_manage(user: dict) -> None:
    if not _can_manage(user):
        raise HTTPException(403, "manage_fee_catalog permission required")


def _assert_view(user: dict) -> None:
    if not _can_view(user):
        raise HTTPException(403, "view_fees permission required")


class ApplicableIn(BaseModel):
    grade_ids: List[str] = Field(default_factory=list)
    section_ids: List[str] = Field(default_factory=list)
    sports: List[str] = Field(default_factory=list)
    centres: List[str] = Field(default_factory=list)
    categories: List[str] = Field(default_factory=list)  # player_type / PWS Day Scholar|Hostel


class CatalogueItemIn(BaseModel):
    entity_id: Literal["alpha", "pws"]
    code: str
    name: str
    fee_type: Literal[
        "tuition", "transport", "hostel", "examination", "coaching",
        "registration", "uniform", "kit", "tournament",
    ]
    amount: float
    billing_frequency: Literal["monthly", "quarterly", "term_wise", "annual", "one_time"]
    academic_year_id: Optional[str] = None
    applicable: ApplicableIn = Field(default_factory=ApplicableIn)
    active: bool = True
    description: Optional[str] = None


class CatalogueItemPatch(BaseModel):
    name: Optional[str] = None
    amount: Optional[float] = None
    billing_frequency: Optional[Literal["monthly", "quarterly", "term_wise", "annual", "one_time"]] = None
    academic_year_id: Optional[str] = None
    applicable: Optional[ApplicableIn] = None
    active: Optional[bool] = None
    description: Optional[str] = None


class PlanItemIn(BaseModel):
    catalogue_item_id: str
    amount: Optional[float] = None  # override catalogue default


class PlanMatchIn(BaseModel):
    kind: Optional[Literal["student", "player"]] = None
    is_resident: Optional[bool] = None
    sport: Optional[str] = None
    centre: Optional[str] = None
    player_type: Optional[str] = None
    grade_id: Optional[str] = None
    section_id: Optional[str] = None


class FeePlanIn(BaseModel):
    entity_id: Literal["alpha", "pws"]
    name: str
    academic_year_id: Optional[str] = None
    description: Optional[str] = None
    items: List[PlanItemIn]
    match: PlanMatchIn = Field(default_factory=PlanMatchIn)
    is_default: bool = False
    active: bool = True


class FeePlanPatch(BaseModel):
    name: Optional[str] = None
    academic_year_id: Optional[str] = None
    description: Optional[str] = None
    items: Optional[List[PlanItemIn]] = None
    match: Optional[PlanMatchIn] = None
    is_default: Optional[bool] = None
    active: Optional[bool] = None


def _catalogue_to_rates(items: list[dict]) -> dict:
    """Convert catalogue item list to legacy rate-card shape."""
    out: dict = {}
    for item in items:
        key = RATE_KEYS.get(item.get("fee_type"))
        if not key:
            continue
        amt = item.get("amount_override") if item.get("amount_override") is not None else item.get("amount")
        if amt is not None:
            out[key] = int(amt)
    return out


async def _hydrate_plan_items(plan: dict) -> dict:
    ids = [i["catalogue_item_id"] for i in plan.get("items") or []]
    if not ids:
        plan["resolved_items"] = []
        plan["rates"] = {}
        return plan
    cats = await db.fee_catalogue.find({"id": {"$in": ids}}, {"_id": 0}).to_list(100)
    by_id = {c["id"]: c for c in cats}
    resolved = []
    for pi in plan.get("items") or []:
        cat = by_id.get(pi["catalogue_item_id"])
        if not cat:
            continue
        resolved.append({
            **cat,
            "amount_override": pi.get("amount"),
            "effective_amount": pi.get("amount") if pi.get("amount") is not None else cat.get("amount"),
        })
    plan["resolved_items"] = resolved
    plan["rates"] = _catalogue_to_rates(resolved)
    return plan


def _person_entity(person: dict) -> str:
    ents = derive_person_entities(person)
    if person.get("kind") == "student" or ("PWS" in ents and "ALPHA" not in ents):
        return "pws"
    if "PWS" in ents and person.get("kind") == "student":
        return "pws"
    return "alpha"


def _pws_category(person: dict) -> str:
    return "Hostel" if person.get("is_resident") else "Day Scholar"


def _match_plan(plan: dict, person: dict, academic_year_id: Optional[str] = None) -> bool:
    m = plan.get("match") or {}
    if m.get("kind") and person.get("kind") != m["kind"]:
        return False
    if plan.get("academic_year_id") and academic_year_id and plan["academic_year_id"] != academic_year_id:
        return False
    if m.get("is_resident") is not None and bool(person.get("is_resident")) != m["is_resident"]:
        return False
    if m.get("sport") and (person.get("sport") or "") != m["sport"]:
        return False
    if m.get("centre") and (person.get("centre") or "") != m["centre"]:
        return False
    if m.get("player_type"):
        pt = person.get("player_type") or _pws_category(person)
        want = m["player_type"]
        hostel_aliases = {"Hostel", "Hostel Only"}
        if pt in hostel_aliases and want in hostel_aliases:
            pass
        elif pt != want:
            return False
    if m.get("grade_id") and person.get("grade_id") != m["grade_id"]:
        return False
    if m.get("section_id") and person.get("section_id") != m["section_id"]:
        return False
    return True


async def find_plan_for_person(person: dict, academic_year_id: Optional[str] = None) -> Optional[dict]:
    if person.get("fee_plan_id"):
        plan = await db.fee_plans.find_one({"id": person["fee_plan_id"], "active": True}, {"_id": 0})
        if plan:
            return await _hydrate_plan_items(plan)
    entity = _person_entity(person)
    plans = await db.fee_plans.find({"entity_id": entity, "active": True}, {"_id": 0}).to_list(200)
    defaults = [p for p in plans if p.get("is_default") and _match_plan(p, person, academic_year_id)]
    if defaults:
        return await _hydrate_plan_items(defaults[0])
    matched = [p for p in plans if _match_plan(p, person, academic_year_id)]
    if matched:
        return await _hydrate_plan_items(matched[0])
    return None


async def resolve_rates_for_person(person: dict, academic_year_id: Optional[str] = None) -> dict:
    """Return legacy-shaped rate dict from assigned/default fee plan."""
    plan = await find_plan_for_person(person, academic_year_id)
    if plan and plan.get("rates"):
        return plan["rates"]
    return {}


@router.get("/meta")
async def catalogue_meta(user: dict = Depends(get_current_user)):
    _assert_view(user)
    return {
        "entities": ENTITY_IDS,
        "fee_types": FEE_TYPES,
        "frequencies": FREQUENCIES,
        "legacy_fee_type_map": LEGACY_FEE_TYPE,
    }


@router.get("/items")
async def list_catalogue_items(
    entity_id: Optional[str] = None,
    fee_type: Optional[str] = None,
    active: Optional[bool] = None,
    academic_year_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    _assert_view(user)
    q: dict = {}
    if entity_id:
        q["entity_id"] = entity_id
    if fee_type:
        q["fee_type"] = fee_type
    if active is not None:
        q["active"] = active
    if academic_year_id:
        q["$or"] = [{"academic_year_id": academic_year_id}, {"academic_year_id": None}]
    return await db.fee_catalogue.find(q, {"_id": 0}).sort("entity_id", 1).to_list(500)


@router.post("/items")
async def create_catalogue_item(payload: CatalogueItemIn, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    existing = await db.fee_catalogue.find_one({"entity_id": payload.entity_id, "code": payload.code})
    if existing:
        raise HTTPException(409, f"Catalogue code already exists: {payload.code}")
    ts = now_utc().isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        **payload.model_dump(),
        "legacy_fee_type": LEGACY_FEE_TYPE.get(payload.fee_type),
        "created_at": ts,
        "updated_at": ts,
        "created_by": user["id"],
    }
    await db.fee_catalogue.insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.get("/items/{item_id}")
async def get_catalogue_item(item_id: str, user: dict = Depends(get_current_user)):
    _assert_view(user)
    doc = await db.fee_catalogue.find_one({"id": item_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Catalogue item not found")
    return doc


@router.patch("/items/{item_id}")
async def patch_catalogue_item(item_id: str, payload: CatalogueItemPatch, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    doc = await db.fee_catalogue.find_one({"id": item_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Catalogue item not found")
    patch = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if "fee_type" in patch:
        patch["legacy_fee_type"] = LEGACY_FEE_TYPE.get(patch["fee_type"])
    patch["updated_at"] = now_utc().isoformat()
    patch["updated_by"] = user["id"]
    await db.fee_catalogue.update_one({"id": item_id}, {"$set": patch})
    return await db.fee_catalogue.find_one({"id": item_id}, {"_id": 0})


@router.get("/plans")
async def list_fee_plans(
    entity_id: Optional[str] = None,
    active: Optional[bool] = None,
    academic_year_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    _assert_view(user)
    q: dict = {}
    if entity_id:
        q["entity_id"] = entity_id
    if active is not None:
        q["active"] = active
    if academic_year_id:
        q["$or"] = [{"academic_year_id": academic_year_id}, {"academic_year_id": None}]
    plans = await db.fee_plans.find(q, {"_id": 0}).sort("name", 1).to_list(200)
    out = []
    for p in plans:
        out.append(await _hydrate_plan_items(p))
    return out


@router.post("/plans")
async def create_fee_plan(payload: FeePlanIn, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    if not payload.items:
        raise HTTPException(400, "Fee plan must include at least one catalogue item")
    for pi in payload.items:
        cat = await db.fee_catalogue.find_one({"id": pi.catalogue_item_id, "entity_id": payload.entity_id})
        if not cat:
            raise HTTPException(400, f"Catalogue item not found for entity: {pi.catalogue_item_id}")
    ts = now_utc().isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        **payload.model_dump(),
        "created_at": ts,
        "updated_at": ts,
        "created_by": user["id"],
    }
    if payload.is_default:
        await db.fee_plans.update_many(
            {"entity_id": payload.entity_id, "is_default": True},
            {"$set": {"is_default": False, "updated_at": ts}},
        )
    await db.fee_plans.insert_one(doc)
    doc.pop("_id", None)
    return await _hydrate_plan_items(doc)


@router.get("/plans/{plan_id}")
async def get_fee_plan(plan_id: str, user: dict = Depends(get_current_user)):
    _assert_view(user)
    doc = await db.fee_plans.find_one({"id": plan_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Fee plan not found")
    return await _hydrate_plan_items(doc)


@router.patch("/plans/{plan_id}")
async def patch_fee_plan(plan_id: str, payload: FeePlanPatch, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    doc = await db.fee_plans.find_one({"id": plan_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Fee plan not found")
    patch = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    ts = now_utc().isoformat()
    patch["updated_at"] = ts
    patch["updated_by"] = user["id"]
    if patch.get("is_default"):
        await db.fee_plans.update_many(
            {"entity_id": doc["entity_id"], "is_default": True, "id": {"$ne": plan_id}},
            {"$set": {"is_default": False, "updated_at": ts}},
        )
    await db.fee_plans.update_one({"id": plan_id}, {"$set": patch})
    updated = await db.fee_plans.find_one({"id": plan_id}, {"_id": 0})
    return await _hydrate_plan_items(updated)


@router.get("/plans/resolve")
async def resolve_fee_plan(
    entity_id: Optional[Literal["alpha", "pws"]] = None,
    kind: Optional[Literal["student", "player"]] = None,
    sport: Optional[str] = None,
    centre: Optional[str] = None,
    player_type: Optional[str] = None,
    is_resident: Optional[bool] = None,
    grade_id: Optional[str] = None,
    section_id: Optional[str] = None,
    person_id: Optional[str] = None,
    academic_year_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """Preview fee plan and amounts for enrollment or a specific person."""
    _assert_view(user)
    if person_id:
        person = await db.people.find_one({"id": person_id}, {"_id": 0})
        if not person:
            raise HTTPException(404, "Person not found")
        plan = await find_plan_for_person(person, academic_year_id)
        return {
            "person_id": person_id,
            "entity_id": _person_entity(person),
            "fee_plan_id": person.get("fee_plan_id") or (plan or {}).get("id"),
            "plan": plan,
            "rates": (plan or {}).get("rates") or {},
        }
    profile = {
        "kind": kind or "player",
        "sport": sport,
        "centre": centre,
        "player_type": player_type,
        "is_resident": is_resident,
        "grade_id": grade_id,
        "section_id": section_id,
        "organization": "PWS" if entity_id == "pws" else "ALPHA",
    }
    ent = entity_id or ("pws" if kind == "student" else "alpha")
    plans = await db.fee_plans.find({"entity_id": ent, "active": True}, {"_id": 0}).to_list(200)
    matched = [p for p in plans if _match_plan(p, profile, academic_year_id)]
    plan = await _hydrate_plan_items(matched[0]) if matched else None
    return {
        "entity_id": ent,
        "profile": profile,
        "plan": plan,
        "rates": (plan or {}).get("rates") or {},
    }
