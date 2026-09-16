"""ALPHA ground booking pricing helpers (no DB)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

HALF_DAY_RATE = 6000
FULL_DAY_RATE = 10000

SPORTS = ("Cricket", "Football")
TIME_SLOTS = ("half_day", "full_day", "custom")
EVENT_TYPES = ("Friendly Match", "Tournament", "Scouting", "Social Event")
STATUSES = ("Tentative", "Confirmed", "Cancelled")
BALL_TYPES = ("Tennis Ball", "Leather Ball")
BALL_COLORS = ("Red", "White")

SLOT_LABELS = {
    "half_day": "Half Day (4 hrs)",
    "full_day": "Full Day (6 hrs)",
    "custom": "Custom",
}
SLOT_LIST_RATES = {
    "half_day": HALF_DAY_RATE,
    "full_day": FULL_DAY_RATE,
}


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def inclusive_days(start_date: str, end_date: str) -> int:
    start = parse_iso_date(start_date)
    end = parse_iso_date(end_date)
    if end < start:
        raise ValueError("End date cannot be before start date")
    return (end - start).days + 1


def list_ground_rate(time_slot: str, days: int, custom_rate: Optional[float] = None) -> float:
    if time_slot == "custom":
        return float(custom_rate or 0)
    per_day = SLOT_LIST_RATES.get(time_slot)
    if per_day is None:
        raise ValueError("Invalid time slot")
    return float(per_day * days)


def _money(value: Any) -> float:
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return round(max(n, 0), 2)


def compute_pricing(
    *,
    time_slot: str,
    start_date: str,
    end_date: str,
    ground_rate: Optional[float],
    people: int,
    food_enabled: bool,
    food_rate_per_plate: float = 0,
    food_people: Optional[int] = None,
    transport_enabled: bool,
    transport_rate_per_person: float = 0,
    transport_people: Optional[int] = None,
    umpire_enabled: bool,
    umpire_rate_per_day: float = 0,
    umpire_people: Optional[int] = None,
    balls_enabled: bool,
    ball_qty: int = 0,
    ball_rate: float = 0,
    custom_list_rate: Optional[float] = None,
) -> dict:
    days = inclusive_days(start_date, end_date)
    people_n = max(int(people or 0), 0)
    list_rate = list_ground_rate(time_slot, days, custom_list_rate)
    quoted = _money(ground_rate if ground_rate is not None else list_rate)
    food_headcount = max(int(food_people if food_people is not None else people_n), 0)
    transport_headcount = max(int(transport_people if transport_people is not None else people_n), 0)
    umpire_headcount = max(int(umpire_people if umpire_people is not None else 1), 0)
    food_cost = _money(food_rate_per_plate) * food_headcount if food_enabled else 0.0
    transport_cost = _money(transport_rate_per_person) * transport_headcount if transport_enabled else 0.0
    umpire_cost = _money(umpire_rate_per_day) * days * umpire_headcount if umpire_enabled else 0.0
    ball_cost = _money(ball_qty) * _money(ball_rate) if balls_enabled else 0.0
    add_on_total = round(food_cost + transport_cost + umpire_cost + ball_cost, 2)
    discount_amount = round(max(list_rate - quoted, 0), 2)
    return {
        "days": days,
        "listGroundRate": round(list_rate, 2),
        "groundRate": quoted,
        "foodCost": round(food_cost, 2),
        "transportCost": round(transport_cost, 2),
        "umpireCost": round(umpire_cost, 2),
        "ballCost": round(ball_cost, 2),
        "addOnTotal": add_on_total,
        "totalRevenue": round(quoted + add_on_total, 2),
        "discountAmount": discount_amount,
        "discountRequested": discount_amount > 0.009 and time_slot != "custom",
    }
