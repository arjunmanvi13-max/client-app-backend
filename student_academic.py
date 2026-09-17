"""Helpers to keep student class/section fields aligned with Academic Structure."""
from __future__ import annotations

import re
from typing import Dict, Iterable, Optional

from core import db

from pws_class_catalog import (
    CLASS_TO_GRADE_KEY as PWS_CLASS_TO_GRADE_PREFIX,
    format_class_display as class_display_name,
    grade_aliases_for_class as grade_aliases_for_pws_class,
    normalize_class_value,
    same_class,
)

# Back-compat: callers still import these names.
PWS_CLASS_DISPLAY = {c: c for c in PWS_CLASS_TO_GRADE_PREFIX}


def normalize_grade_key(name: str) -> str:
    folded = (name or "").strip().lower().replace("std", "").replace("grade", "").replace("class", "")
    return re.sub(r"[^a-z0-9]+", " ", folded).strip() or (name or "").strip().lower()


def section_letter_from_label(label: Optional[str]) -> str:
    m = re.search(r"-([A-F])$", (label or "").strip(), re.I)
    return m.group(1).upper() if m else ""


def grade_matches_pws_class(grade_name: Optional[str], pws_class: Optional[str]) -> bool:
    return same_class(grade_name, pws_class)


def _section_matches_class(section: dict, pws_class: str, letter: str) -> bool:
    if section_letter_from_label(section.get("label")) != letter.upper():
        return False
    grade_name = section.get("grade_name") or (section.get("label") or "").split("-")[0]
    return grade_matches_pws_class(grade_name, pws_class)


async def _open_academic_year_id() -> Optional[str]:
    year = await db.academic_years.find_one({"status": "open"}, {"_id": 0, "id": 1})
    return year["id"] if year else None


async def find_section_for_class_letter(
    pws_class: str,
    letter: str,
    *,
    academic_year_id: Optional[str] = None,
) -> Optional[dict]:
    letter = (letter or "").strip().upper()
    if not letter or not pws_class:
        return None
    year_id = academic_year_id or await _open_academic_year_id()
    if not year_id:
        return None
    sections = await db.sections.find({"academic_year_id": year_id}, {"_id": 0}).to_list(500)
    for sec in sections:
        if _section_matches_class(sec, pws_class, letter):
            return sec
    return None


async def sync_student_academic_fields(doc: dict, *, fix_mismatch: bool = True) -> dict:
    """Ensure section_id/group/class display fields are consistent with pws_class."""
    out = dict(doc)
    canon = normalize_class_value(out.get("pws_class"))
    if canon:
        out["pws_class"] = canon
    pws_class = (out.get("pws_class") or "").strip()
    out["class_name"] = class_display_name(pws_class)

    section_id = out.get("section_id")
    section = None
    if section_id:
        section = await db.sections.find_one({"id": section_id}, {"_id": 0})

    if section and pws_class and not grade_matches_pws_class(section.get("grade_name"), pws_class):
        if fix_mismatch:
            letter = section_letter_from_label(section.get("label"))
            replacement = await find_section_for_class_letter(pws_class, letter)
            if replacement:
                section = replacement
                out["section_id"] = replacement["id"]
            else:
                out.pop("section_id", None)
                section = None
        else:
            out.pop("section_id", None)
            section = None

    if section:
        out["group"] = section["label"]
        out["section_name"] = section_letter_from_label(section["label"])
    else:
        letter = section_letter_from_label(out.get("group"))
        out["section_name"] = letter
        if letter and pws_class and fix_mismatch:
            matched = await find_section_for_class_letter(pws_class, letter)
            if matched:
                out["section_id"] = matched["id"]
                out["group"] = matched["label"]
                out["section_name"] = section_letter_from_label(matched["label"])

    if not out.get("section_name"):
        out["section_name"] = ""

    return out


async def enrich_students_for_list(rows: Iterable[dict]) -> list[dict]:
    """Attach class_name/section_name and canonical group labels for roster display."""
    items = list(rows)
    if not items:
        return items

    section_ids = [r.get("section_id") for r in items if r.get("section_id")]
    section_cache: dict[str, dict] = {}
    if section_ids:
        found = await db.sections.find({"id": {"$in": section_ids}}, {"_id": 0}).to_list(len(section_ids))
        section_cache = {s["id"]: s for s in found}

    enriched: list[dict] = []
    for row in items:
        out = dict(row)
        sports_group = out.get("group") if out.get("kind") == "player" else None
        canon = normalize_class_value(out.get("pws_class"))
        if canon:
            out["pws_class"] = canon
        pws_class = out.get("pws_class") or ""
        out["class_name"] = class_display_name(pws_class)
        section = section_cache.get(out.get("section_id") or "")
        if section:
            if pws_class and not grade_matches_pws_class(section.get("grade_name"), pws_class):
                letter = section_letter_from_label(section.get("label"))
                replacement = await find_section_for_class_letter(pws_class, letter)
                if replacement:
                    section = replacement
                    out["section_id"] = replacement["id"]
            out["group"] = section.get("label") or out.get("group")
            out["section_name"] = section_letter_from_label(section.get("label"))
            if out.get("kind") == "player" and sports_group is not None:
                out["group"] = sports_group
                out["academic_section_label"] = section.get("label") or ""
        else:
            out["section_name"] = section_letter_from_label(out.get("group"))
        enriched.append(out)
    return enriched
