"""Pure helpers for APL - N player/student identifier formatting."""
import re
from datetime import datetime
from typing import Optional

APL_ID_START = 150
APL_ID_REGEX = re.compile(r"^APL\s*-\s*(\d+)$", re.I)


def parse_apl_number(player_id: str) -> Optional[int]:
    """Parse numeric portion from APL - N or legacy APL-N formats."""
    if not player_id:
        return None
    s = player_id.strip()
    m = APL_ID_REGEX.match(s)
    if m:
        return int(m.group(1))
    m2 = re.match(r"^APL-(\d+)$", s, re.I)
    if m2:
        return int(m2.group(1))
    return None


def format_apl_id(n: int) -> str:
    return f"APL - {n}"


def normalize_person_name(name: Optional[str]) -> str:
    return (name or "").strip()


def normalize_dob(dob: Optional[str]) -> Optional[str]:
    if not dob:
        return None
    s = dob.strip()
    if len(s) >= 10:
        try:
            return datetime.fromisoformat(s[:10]).date().isoformat()
        except ValueError:
            pass
    return s or None
