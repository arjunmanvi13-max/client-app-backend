"""Pure helpers for Collect Fees summary — no DB imports."""
from __future__ import annotations

from datetime import datetime
from typing import List


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
