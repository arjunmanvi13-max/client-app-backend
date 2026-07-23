"""Academic calendar rules linked to attendance — Sundays are holidays for students/teachers."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def calendar_day_info(date_str: str) -> Dict[str, Any]:
    """Return calendar metadata for an attendance date."""
    d = parse_iso_date(date_str)
    is_sunday = d.weekday() == 6
    # Students and teachers: Sunday is an automatic holiday (no attendance required).
    holiday_for = {
        "student": is_sunday,
        "teacher": is_sunday,
        "player": False,
        "staff": False,
        "coach": False,
    }
    return {
        "date": date_str,
        "weekday": d.strftime("%A"),
        "is_sunday": is_sunday,
        "holiday_for": holiday_for,
        "is_holiday_for": holiday_for,
    }


def is_holiday_for_kind(date_str: str, kind: str) -> bool:
    info = calendar_day_info(date_str)
    return bool(info["holiday_for"].get(kind, False))
