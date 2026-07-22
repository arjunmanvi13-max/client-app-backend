"""Academy Structure — enrollment capacity baselines per entity."""
from typing import Dict, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from academy_structure import (
    ACADEMY_CATEGORIES,
    assert_super_admin,
    get_entity_baselines,
    save_entity_baselines,
)
from core import get_current_user

router = APIRouter(prefix="/academy-structure", tags=["academy-structure"])


class BaselineCategoriesIn(BaseModel):
    categories: Dict[str, int] = Field(default_factory=dict)


@router.get("")
async def list_academy_structure(user: dict = Depends(get_current_user)):
    assert_super_admin(user)
    pws = await get_entity_baselines("PWS")
    alpha = await get_entity_baselines("ALPHA")
    return {
        "categories": ACADEMY_CATEGORIES,
        "entities": {
            "PWS": {"categories": pws},
            "ALPHA": {"categories": alpha},
        },
    }


@router.put("/{entity}")
async def upsert_academy_structure(
    entity: Literal["PWS", "ALPHA", "pws", "alpha"],
    payload: BaselineCategoriesIn,
    user: dict = Depends(get_current_user),
):
    assert_super_admin(user)
    ent = entity.upper()
    if ent not in ("PWS", "ALPHA"):
        raise HTTPException(400, "entity must be PWS or ALPHA")
    doc = await save_entity_baselines(ent, payload.categories, user["id"])
    return doc
