"""PWS class-roster helpers — include ALPHA Boarding/Day Boarding players by section.

Sports fields (centre, sport, group/batch, player_type) stay on the player document.
Academic mapping uses pws_class + section_id only.
"""
from __future__ import annotations

from typing import Any, Optional

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


def academic_class_roster_query(
    section_id: Optional[str] = None,
    section_ids: Optional[list[str]] = None,
    section_labels: Optional[list[str]] = None,
) -> dict:
    """Active PWS students plus ALPHA Boarding/Day Boarding players in the class.

    Matches both `section_id` and legacy `group` labels so teachers can take
    attendance even when older student records were stored by class name only.
    """
    ids = [sid for sid in (section_ids or []) if sid]
    if section_id:
        ids = [section_id]
    labels = [str(label).strip() for label in (section_labels or []) if str(label).strip()]
    if not ids and not labels:
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
    sections = await db.sections.find({"id": {"$in": ids}}, {"_id": 0, "id": 1, "label": 1}).to_list(200)
    labels = [s.get("label") for s in sections if s.get("label")]
    return academic_class_roster_query(section_ids=ids, section_labels=labels)
