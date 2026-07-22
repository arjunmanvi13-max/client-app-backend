"""Academy Structure — enrollment capacity baselines per entity."""
from typing import Any, Dict, Literal

from fastapi import APIRouter, Body, Depends, HTTPException

from academy_structure import (
    ALPHA_CATEGORY_KEYS,
    PWS_CLASS_KEYS,
    assert_super_admin,
    get_alpha_baselines,
    get_pws_baselines,
    save_alpha_baselines,
    save_entity_baselines,
    save_pws_baselines,
)
from core import get_current_user

router = APIRouter(prefix="/academy-structure", tags=["academy-structure"])


def _alpha_totals(matrix: Dict[str, Dict[str, int]]) -> dict:
    cricket = sum(int((matrix.get(c) or {}).get("cricket") or 0) for c in ALPHA_CATEGORY_KEYS)
    football = sum(int((matrix.get(c) or {}).get("football") or 0) for c in ALPHA_CATEGORY_KEYS)
    return {"cricket": cricket, "football": football, "overall": cricket + football}


@router.get("")
async def list_academy_structure(user: dict = Depends(get_current_user)):
    assert_super_admin(user)
    pws_classes = await get_pws_baselines()
    alpha_matrix = await get_alpha_baselines()
    return {
        "pws_class_keys": PWS_CLASS_KEYS,
        "alpha_category_keys": ALPHA_CATEGORY_KEYS,
        "entities": {
            "PWS": {
                "pws_classes": pws_classes,
                "total_capacity": sum(pws_classes.values()),
            },
            "ALPHA": {
                "alpha_matrix": alpha_matrix,
                "totals": _alpha_totals(alpha_matrix),
            },
        },
    }


@router.put("/{entity}")
async def upsert_academy_structure(
    entity: Literal["PWS", "ALPHA", "pws", "alpha"],
    payload: Dict[str, Any] = Body(...),
    user: dict = Depends(get_current_user),
):
    assert_super_admin(user)
    ent = entity.upper()
    if ent not in ("PWS", "ALPHA"):
        raise HTTPException(400, "entity must be PWS or ALPHA")

    if ent == "PWS":
        if payload.get("pws_classes") is not None:
            doc = await save_pws_baselines(payload["pws_classes"], user["id"])
            classes = doc.get("pws_classes") or {}
        elif payload.get("categories"):
            await save_entity_baselines(ent, payload["categories"], user["id"])
            classes = await get_pws_baselines()
        else:
            raise HTTPException(400, "pws_classes required")
        return {"entity": "PWS", "pws_classes": classes, "total_capacity": sum(classes.values())}

    if payload.get("alpha_matrix") is not None:
        doc = await save_alpha_baselines(payload["alpha_matrix"], user["id"])
        matrix = doc.get("alpha_matrix") or {}
    elif payload.get("categories"):
        await save_entity_baselines(ent, payload["categories"], user["id"])
        matrix = await get_alpha_baselines()
    else:
        raise HTTPException(400, "alpha_matrix required")
    return {"entity": "ALPHA", "alpha_matrix": matrix, "totals": _alpha_totals(matrix)}
