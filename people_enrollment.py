"""Sequential APL player/student IDs and duplicate enrollment guards."""
import re
from typing import Optional

from fastapi import HTTPException
from pymongo import ReturnDocument

from apl_id_format import (
    APL_ID_START,
    format_apl_id,
    normalize_dob,
    normalize_person_name,
    parse_apl_number,
)
from core import db

APL_ID_COUNTER_KEY = "apl_player_id"
DUPLICATE_NAME_DOB_MSG = "A player with this name and date of birth already exists."


async def _max_apl_number_in_people() -> int:
    max_n = APL_ID_START - 1
    cursor = db.people.find(
        {"kind": {"$in": ["player", "student"]}, "player_id": {"$exists": True, "$nin": [None, ""]}},
        {"player_id": 1},
    )
    async for doc in cursor:
        n = parse_apl_number(doc.get("player_id") or "")
        if n is not None:
            max_n = max(max_n, n)
    return max_n


async def ensure_apl_id_counter() -> None:
    """Initialize counter from existing player_id values (idempotent)."""
    existing = await db.counters.find_one({"_id": APL_ID_COUNTER_KEY})
    if existing is not None:
        return
    max_n = await _max_apl_number_in_people()
    await db.counters.update_one(
        {"_id": APL_ID_COUNTER_KEY},
        {"$setOnInsert": {"seq": max(max_n, APL_ID_START - 1)}},
        upsert=True,
    )


async def allocate_apl_player_id() -> str:
    """Atomically allocate the next APL - N identifier."""
    await ensure_apl_id_counter()
    doc = await db.counters.find_one_and_update(
        {"_id": APL_ID_COUNTER_KEY},
        {"$inc": {"seq": 1}},
        return_document=ReturnDocument.AFTER,
    )
    return format_apl_id(int(doc["seq"]))


async def assert_no_duplicate_name_dob(doc: dict, exclude_id: Optional[str] = None) -> None:
    """Block create when name + DOB match an existing player or student."""
    if doc.get("kind") not in ("player", "student"):
        return
    name = normalize_person_name(doc.get("name"))
    dob = normalize_dob(doc.get("dob"))
    if not name or not dob:
        return
    q: dict = {
        "kind": {"$in": ["player", "student"]},
        "name": {"$regex": f"^{re.escape(name)}$", "$options": "i"},
    }
    if exclude_id:
        q["id"] = {"$ne": exclude_id}
    async for existing in db.people.find(q, {"dob": 1}):
        if normalize_dob(existing.get("dob")) == dob:
            raise HTTPException(400, DUPLICATE_NAME_DOB_MSG)


async def assign_enrollment_ids(doc: dict) -> dict:
    """Normalize identity fields, check duplicates, and assign APL player_id on create."""
    if doc.get("kind") not in ("player", "student"):
        return doc
    doc["name"] = normalize_person_name(doc.get("name"))
    if doc.get("dob"):
        doc["dob"] = normalize_dob(doc["dob"]) or doc["dob"]
    await assert_no_duplicate_name_dob(doc)
    doc["player_id"] = await allocate_apl_player_id()
    return doc
