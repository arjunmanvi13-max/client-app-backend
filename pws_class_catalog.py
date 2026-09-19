"""Single source of truth for PWS class / standard values.

Canonical stored + display value:
    Nursery, LKG, UKG, Std 1 … Std 12

Internal class code (stable, not shown in UI):
    NURSERY, LKG, UKG, STD_01 … STD_12

Academic Structure may still store short grade keys (Nur, 1, 2) — those
normalize back to the canonical class via `normalize_class_value`.
Never display Nur, Std Nur, Class I, Class X, 10th, or Grade 10.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

CLASS_LIST: tuple[str, ...] = (
    "Nursery",
    "LKG",
    "UKG",
    "Std 1",
    "Std 2",
    "Std 3",
    "Std 4",
    "Std 5",
    "Std 6",
    "Std 7",
    "Std 8",
    "Std 9",
    "Std 10",
    "Std 11",
    "Std 12",
)

CLASS_SET = frozenset(CLASS_LIST)

# Short key stored on academic grades.name / used in section labels like "3-A".
CLASS_TO_GRADE_KEY: dict[str, str] = {
    "Nursery": "Nur",
    "LKG": "LKG",
    "UKG": "UKG",
    "Std 1": "1",
    "Std 2": "2",
    "Std 3": "3",
    "Std 4": "4",
    "Std 5": "5",
    "Std 6": "6",
    "Std 7": "7",
    "Std 8": "8",
    "Std 9": "9",
    "Std 10": "10",
    "Std 11": "11",
    "Std 12": "12",
}

CLASS_TO_CODE: dict[str, str] = {
    "Nursery": "NURSERY",
    "LKG": "LKG",
    "UKG": "UKG",
    "Std 1": "STD_01",
    "Std 2": "STD_02",
    "Std 3": "STD_03",
    "Std 4": "STD_04",
    "Std 5": "STD_05",
    "Std 6": "STD_06",
    "Std 7": "STD_07",
    "Std 8": "STD_08",
    "Std 9": "STD_09",
    "Std 10": "STD_10",
    "Std 11": "STD_11",
    "Std 12": "STD_12",
}

ROMAN_TO_ARABIC = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
    "xi": "11", "xii": "12",
}
ARABIC_TO_ROMAN = {v: k.upper() for k, v in ROMAN_TO_ARABIC.items()}

_TOKEN_STRIP = re.compile(r"[^a-z0-9]+")


def _fold(value: str) -> str:
    return _TOKEN_STRIP.sub(" ", (value or "").strip().lower()).strip()


def _alias_map() -> dict[str, str]:
    """Lowercased folded token -> canonical class."""
    mapping: dict[str, str] = {}

    def add(alias: str, canonical: str) -> None:
        key = _fold(alias)
        if key:
            mapping[key] = canonical
            mapping[key.replace(" ", "")] = canonical

    for canonical in CLASS_LIST:
        add(canonical, canonical)
        add(canonical.replace("Std ", "Class "), canonical)
        add(canonical.replace("Std ", "Standard "), canonical)
        add(canonical.replace("Std ", "Grade "), canonical)
        key = CLASS_TO_GRADE_KEY[canonical]
        add(key, canonical)
        add(f"std {key}", canonical)
        add(f"std{key}", canonical)
        add(f"standard {key}", canonical)
        add(f"grade {key}", canonical)
        add(f"class {key}", canonical)
        add(f"class-{key}", canonical)
        add(CLASS_TO_CODE[canonical], canonical)
        if key.isdigit():
            roman = ARABIC_TO_ROMAN[key]
            add(roman, canonical)
            add(f"class {roman}", canonical)
            add(f"std {roman}", canonical)
            add(f"class {key}", canonical)
            add(f"class{key}", canonical)
            add(f"c{key}", canonical)
            add(f"{key}th", canonical)
            if key == "1":
                add("1st", canonical)
    add("nur", "Nursery")
    add("std nur", "Nursery")
    add("nursary", "Nursery")
    add("pre nursery", "Nursery")
    add("pre-nursery", "Nursery")
    add("kg1", "LKG")
    add("kg 1", "LKG")
    add("l.k.g", "LKG")
    add("kg2", "UKG")
    add("kg 2", "UKG")
    add("u.k.g", "UKG")
    return mapping


_ALIAS_TO_CANONICAL = _alias_map()


def normalize_class_value(input_val: Optional[str]) -> Optional[str]:
    """Parse form/CSV/API/legacy values into the canonical stored class."""
    raw = (input_val or "").strip()
    if not raw:
        return None
    if raw in CLASS_SET:
        return raw
    folded = _fold(raw)
    if folded in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[folded]
    compact = folded.replace(" ", "")
    if compact in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[compact]
    return None


def format_class_display(class_val: Optional[str]) -> str:
    """UI label for any stored or legacy class/grade value."""
    if not (class_val or "").strip():
        return ""
    canon = normalize_class_value(class_val)
    return canon or (class_val or "").strip()


def grade_aliases_for_class(class_val: Optional[str]) -> set[str]:
    canon = normalize_class_value(class_val)
    if not canon:
        raw = _fold(class_val or "")
        return {raw} if raw else set()
    key = CLASS_TO_GRADE_KEY[canon]
    aliases = {
        _fold(canon),
        _fold(key),
        _fold(f"std {key}"),
        _fold(f"grade {key}"),
        _fold(f"class {key}"),
        _fold(CLASS_TO_CODE[canon]),
    }
    if key.isdigit():
        aliases.add(_fold(ARABIC_TO_ROMAN[key]))
        aliases.add(key)
    if canon == "Nursery":
        aliases.update({"nur", "nursery", "std nur"})
    return {a for a in aliases if a}


def class_aliases(class_val: Optional[str]) -> list[str]:
    """All strings that may appear in people.pws_class for this class.

    Derived from the same map normalize_class_value reads, so anything the write
    path accepts is findable by the read path.
    """
    canon = normalize_class_value(class_val)
    if not canon:
        raw = (class_val or "").strip()
        return [raw] if raw else []
    key = CLASS_TO_GRADE_KEY[canon]
    out = [
        canon,
        CLASS_TO_CODE[canon],
        key,
        f"Std {key}",
        f"STD {key}",
        f"Grade {key}",
        f"Class {key}",
        f"Class-{key}",
        f"class {key}",
    ]
    if key.isdigit():
        roman = ARABIC_TO_ROMAN[key]
        out.extend([f"Class {roman}", f"Std {roman}", roman, f"Class {roman.upper()}"])
        out.append(f"Std {int(key)}")
    if canon == "Nursery":
        out.extend(["Nur", "NUR", "nursery", "Std Nur"])
    out.extend(alias for alias, target in _ALIAS_TO_CANONICAL.items() if target == canon)
    seen: set[str] = set()
    unique: list[str] = []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def same_class(a: Optional[str], b: Optional[str]) -> bool:
    left, right = normalize_class_value(a), normalize_class_value(b)
    if left and right:
        return left == right
    return _fold(a or "") == _fold(b or "") and bool(_fold(a or ""))


def pws_class_mongo_values(class_val: Optional[str]) -> list[str]:
    return class_aliases(class_val)


def pws_class_mongo_filter(class_val: Optional[str], *, field: str = "pws_class") -> dict:
    values = pws_class_mongo_values(class_val)
    if not values:
        return {field: {"$in": []}}
    parts = [re.escape(v).replace("\\ ", r"[\s._\-]*") for v in values]
    pattern = "^(?:" + "|".join(parts) + ")$"
    return {field: {"$regex": pattern, "$options": "i"}}

