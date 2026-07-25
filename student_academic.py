"""Helpers to keep student class/section fields aligned with Academic Structure."""
from __future__ import annotations

import re
from typing import Dict, Iterable, Optional

from core import db

PWS_CLASS_DISPLAY: Dict[str, str] = {
    "Nursery": "Nur",
    "LKG": "LKG",
    "UKG": "UKG",
    "Class I": "Std 1",
    "Class II": "Std 2",
    "Class III": "Std 3",
    "Class IV": "Std 4",
    "Class V": "Std 5",
    "Class VI": "Std 6",
    "Class VII": "Std 7",
    "Class VIII": "Std 8",
    "Class IX": "Std 9",
    "Class X": "Std 10",
}

PWS_CLASS_TO_GRADE_PREFIX: Dict[str, str] = {
    "Nursery": "Nur",
    "LKG": "LKG",
    "UKG": "UKG",
    "Class I": "1",
    "Class II": "2",
    "Class III": "3",
    "Class IV": "4",
    "Class V": "5",
    "Class VI": "6",
    "Class VII": "7",
    "Class VIII": "8",
    "Class IX": "9",
    "Class X": "10",
}


def normalize_grade_key(name: str) -> str:
    return (name or "").strip().lower().replace("std", "").replace("grade", "").strip()


def grade_aliases_for_pws_class(pws_class: str) -> set[str]:
    aliases: set[str] = set()
    prefix = PWS_CLASS_TO_GRADE_PREFIX.get(pws_class or "")
    if prefix:
        aliases.add(normalize_grade_key(prefix))
    aliases.add(normalize_grade_key(pws_class or ""))
    if prefix and prefix.isdigit():
        aliases.add(normalize_grade_key(f"std {prefix}"))
        aliases.add(normalize_grade_key(f"grade {prefix}"))
    if normalize_grade_key(pws_class or "") in {"nur", "nursery"} or prefix == "Nur":
        aliases.update({"nur", "nursery"})
    return {a for a in aliases if a}


def class_display_name(pws_class: Optional[str]) -> str:
    raw = (pws_class or "").strip()
    if not raw:
        return ""
    return PWS_CLASS_DISPLAY.get(raw, raw)


def section_letter_from_label(label: Optional[str]) -> str:
    m = re.search(r"-([A-F])$", (label or "").strip(), re.I)
    return m.group(1).upper() if m else ""


def grade_matches_pws_class(grade_name: Optional[str], pws_class: Optional[str]) -> bool:
    if not grade_name or not pws_class:
        return False
    aliases = grade_aliases_for_pws_class(pws_class)
    gn = normalize_grade_key(grade_name)
    return gn in aliases


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
        else:
            out["section_name"] = section_letter_from_label(out.get("group"))
        enriched.append(out)
    return enriched
