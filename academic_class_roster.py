"""PWS class-roster helpers — include ALPHA Boarding/Day Boarding players by section.

Sports fields (centre, sport, group/batch, player_type) stay on the player document.
Academic mapping uses pws_class + section_id, with aliases for older group labels.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from pws_class_catalog import (
    CLASS_TO_GRADE_KEY,
    class_aliases,
    format_class_display,
    normalize_class_value,
)
from student_academic import section_letter_from_label

PWS_LINKED_PLAYER_TYPES = ("Boarding", "Day Boarding")
PWS_LINKED_PLAYER_TYPE_ALIASES = {
    "Boarding": ("Boarding",),
    "Day Boarding": ("Day Boarding",),
}


def canonical_player_type(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if raw == "Hostel":
        return "Hostel Only"
    return raw


def is_pws_linked_player_type(player_type: Optional[str]) -> bool:
    return canonical_player_type(player_type) in PWS_LINKED_PLAYER_TYPES


def is_pws_linked_player(person: dict) -> bool:
    return person.get("kind") == "player" and is_pws_linked_player_type(person.get("player_type"))


def pws_classes_for_grade_name(grade_name: Optional[str]) -> list[str]:
    canon = normalize_class_value(grade_name)
    return [canon] if canon else []


def group_aliases_for_section(
    label: Optional[str] = None,
    grade_name: Optional[str] = None,
    letter: Optional[str] = None,
) -> list[str]:
    """Labels historically stored on people.group for the same class/section."""
    raw = (label or "").strip()
    letter = (letter or section_letter_from_label(raw) or "").strip().upper()
    prefixes: set[str] = set()
    if raw:
        prefixes.add(raw.rsplit("-", 1)[0].strip() if "-" in raw else raw)
    if grade_name and str(grade_name).strip():
        prefixes.add(str(grade_name).strip())
    pws_list: list[str] = []
    for prefix in prefixes:
        pws_list.extend(pws_classes_for_grade_name(prefix))
    pws_list = list(dict.fromkeys(pws_list))

    aliases: set[str] = set()
    if raw:
        aliases.add(raw)
        aliases.add(raw.replace(" ", ""))
        aliases.add(raw.replace("-", ""))
        aliases.add(raw.replace(" ", "").replace("-", ""))
    heads: list[str] = []
    for pws in pws_list:
        num = CLASS_TO_GRADE_KEY.get(pws) or ""
        display = format_class_display(pws)
        heads.extend([pws, num, display, f"Std {num}", f"Grade {num}", f"Class {num}"])
        heads.extend(class_aliases(pws))
    heads.extend(list(prefixes))
    for head in dict.fromkeys(h.strip() for h in heads if h and str(h).strip()):
        if letter:
            aliases.add(f"{head}-{letter}")
            aliases.add(f"{head} {letter}")
            aliases.add(f"{head}{letter}")
        else:
            aliases.add(head)
    return [a for a in aliases if a]


def _unique_pairs(pairs: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for pws, letter in pairs:
        key = ((pws or "").strip(), (letter or "").strip().upper())
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def academic_class_roster_query(
    section_id: Optional[str] = None,
    section_ids: Optional[list[str]] = None,
    section_labels: Optional[list[str]] = None,
    section_docs: Optional[list[dict]] = None,
) -> dict:
    """Active PWS students plus ALPHA Boarding/Day Boarding players in the class.

    Matches `section_id`, legacy `group` labels (3-A, Std 3-A, Class III-A, …),
    and pws_class + section letter so older Directory records still appear.
    """
    ids = [sid for sid in (section_ids or []) if sid]
    if section_id:
        ids = [section_id]
    labels = [str(label).strip() for label in (section_labels or []) if str(label).strip()]
    pws_pairs: list[tuple[str, str]] = []
    for doc in section_docs or []:
        lab = (doc.get("label") or "").strip()
        letter = (doc.get("name") or section_letter_from_label(lab) or "").strip().upper()
        gname = (doc.get("grade_name") or (lab.rsplit("-", 1)[0] if lab else "")).strip()
        if lab:
            labels.append(lab)
        labels.extend(group_aliases_for_section(lab, gname, letter))
        for pws in pws_classes_for_grade_name(gname):
            if letter:
                pws_pairs.append((pws, letter))
    for lab in list(labels):
        labels.extend(group_aliases_for_section(lab))
    labels = list(dict.fromkeys(a for a in labels if a))
    pws_pairs = _unique_pairs(pws_pairs)

    if not ids and not labels and not pws_pairs:
        return {"id": {"$in": []}}

    section_clause: Any = ids[0] if len(ids) == 1 else {"$in": ids}
    or_clauses: list[dict[str, Any]] = []
    if ids:
        or_clauses.append({"kind": "student", "section_id": section_clause})
        or_clauses.append({
            "kind": "player",
            "player_type": {"$in": list(PWS_LINKED_PLAYER_TYPES)},
            "section_id": section_clause,
        })
    if labels:
        label_clause: Any = labels[0] if len(labels) == 1 else {"$in": labels}
        or_clauses.append({"kind": "student", "group": label_clause})
        or_clauses.append({
            "kind": "player",
            "player_type": {"$in": list(PWS_LINKED_PLAYER_TYPES)},
            "academic_section_label": label_clause,
        })
    for pws, letter in pws_pairs:
        aliases = class_aliases(pws)
        pws_clause: Any = aliases[0] if len(aliases) == 1 else {"$in": aliases}
        or_clauses.append({"kind": "student", "pws_class": pws_clause, "section_name": letter})
        or_clauses.append({"kind": "student", "pws_class": pws_clause, "section": letter})
        or_clauses.append({
            "kind": "player",
            "player_type": {"$in": list(PWS_LINKED_PLAYER_TYPES)},
            "pws_class": pws_clause,
            "section_name": letter,
        })
    return {
        "status": {"$ne": "deactivated"},
        "$or": or_clauses,
    }


async def class_roster_query_for_section_ids(section_ids: list[str]) -> dict:
    """Resolve section labels and build a roster filter for those classes."""
    from core import db
    ids = [sid for sid in section_ids if sid]
    if not ids:
        return academic_class_roster_query()
    sections = await db.sections.find(
        {"id": {"$in": ids}},
        {"_id": 0, "id": 1, "label": 1, "grade_name": 1, "name": 1},
    ).to_list(200)
    labels = [s.get("label") for s in sections if s.get("label")]
    return academic_class_roster_query(
        section_ids=ids,
        section_labels=labels,
        section_docs=sections,
    )
