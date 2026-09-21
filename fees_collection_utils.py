"""Pure helpers for Collect Fees summary — no DB imports."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional


def normalize_person_date(value: Optional[str], fallback: Optional[str] = None) -> str:
    """Normalize Directory dates (YYYY-MM-DD or DD/MM/YYYY) to ISO YYYY-MM-DD."""
    s = (value or "").strip()
    if not s:
        return fallback or ""
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    if "/" in s:
        parts = [p.strip() for p in s.split("/")]
        if len(parts) == 3 and len(parts[2]) == 4:
            day, month, year = parts[0].zfill(2), parts[1].zfill(2), parts[2]
            return f"{year}-{month}-{day}"
    return s[:10] if len(s) >= 10 else (fallback or s)


def days_overdue(due_date_str: str, today: str) -> int:
    try:
        due = datetime.fromisoformat((due_date_str or today)[:10]).date()
        t = datetime.fromisoformat(today[:10]).date()
        return max((t - due).days, 0)
    except Exception:
        return 0


def compute_player_fee_status(unpaid: List[dict], paid: List[dict], today: str, current_month: str) -> dict:
    amount_due = sum(int(f.get("amount_due") or 0) for f in unpaid)
    amount_due_today = sum(
        int(f.get("amount_due") or 0) for f in unpaid
        if (f.get("due_date") or "9999-99-99")[:10] <= today
        or (f.get("period_month") or "9999-99") <= current_month
    )
    overdue_fees = [
        f for f in unpaid
        if (f.get("period_month") or "9999-99") < current_month
        or (f.get("due_date") or "9999-99-99")[:10] < today
    ]
    overdue_days = 0
    if overdue_fees:
        overdue_days = max(
            days_overdue(f.get("due_date") or f"{f.get('period_month', current_month)}-05", today)
            for f in overdue_fees
        )
    has_current_month_due = any(f.get("period_month") == current_month for f in unpaid)
    paid_ahead = amount_due == 0 and any(
        (f.get("period_month") or "0000-00") > current_month for f in paid
    )

    if paid_ahead:
        fee_status = "paid_ahead"
        badge = "Paid Ahead"
    elif amount_due == 0:
        fee_status = "paid"
        badge = "Paid"
    elif overdue_fees:
        fee_status = "overdue"
        badge = f"Overdue {overdue_days}d" if overdue_days > 0 else "Overdue"
    else:
        fee_status = "due"
        badge = "Due"

    return {
        "amount_due": amount_due,
        "amount_due_today": amount_due_today,
        "overdue_days": overdue_days if overdue_fees else 0,
        "fee_status": fee_status,
        "badge": badge,
        "has_current_month_due": has_current_month_due,
    }


def expand_player_type_filter(raw: str | None) -> list[str] | None:
    """Parse comma-separated ALPHA player types; Hostel aliases match both labels."""
    if not raw or not str(raw).strip():
        return None
    types = [x.strip() for x in str(raw).split(",") if x.strip() and x.strip().lower() != "all"]
    if not types:
        return None
    out: list[str] = []
    for t in types:
        if t in ("Hostel", "Hostel Only"):
            out.extend(["Hostel", "Hostel Only"])
        else:
            out.append(t)
    seen: set[str] = set()
    unique: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique or None
