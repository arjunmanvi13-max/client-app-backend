"""PWS fee structure — 2026-27 academic year (Prarambhika World School).

Central config for class-based fees. ALPHA fees remain in routers/fees.py RATE_CARDS.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

PWS_ACADEMIC_YEAR = "2026-27"
PWS_FY_START = "2026-04"
PWS_FY_END = "2027-03"

PWS_STUDENT_TYPES = ("Day School", "Boarding", "Day Boarding")
PWS_CLASSES = (
    "Nursery", "UKG",
    "Class I", "Class II", "Class III", "Class IV", "Class V", "Class VI",
    "Class VII", "Class VIII", "Class IX", "Class X",
)
TRANSPORT_DISTANCES = ("Up to 5 km", "Over 5 km")

FEE_CATEGORIES = (
    "Registration",
    "Admission Charges",
    "Security (Refundable)",
    "Annual Charges",
    "Tuition",
    "Physical Education",
    "Exam Fee",
    "Transport",
)

# Fixed one-time amounts (class-independent unless noted)
BASE_FEES = {
    "Registration": {"amount": 1000, "frequency": "one_time"},
    "Admission Charges": {"amount": 10000, "frequency": "one_time"},
}


def _class_idx(pws_class: str) -> int:
    try:
        return PWS_CLASSES.index(pws_class)
    except ValueError:
        return 0


def _nursery_to_iii(pws_class: str) -> bool:
    return _class_idx(pws_class) <= 4  # Nursery … Class III


def _nursery_to_iv(pws_class: str) -> bool:
    return _class_idx(pws_class) <= 5


def tuition_amount(pws_class: str) -> int:
    i = _class_idx(pws_class)
    if i <= 1:
        return 1300
    if i <= 4:
        return 1800
    if i <= 7:
        return 2000
    if i <= 9:
        return 2300
    return 3000


def pe_amount(pws_class: str) -> int:
    i = _class_idx(pws_class)
    if i <= 1:
        return 500
    if i <= 4:
        return 750
    return 1000


def exam_amount(pws_class: str) -> int:
    return 1000 if _nursery_to_iv(pws_class) else 1500


def security_amount(pws_class: str) -> int:
    return 2000 if _nursery_to_iii(pws_class) else 3000


def annual_amount(pws_class: str) -> int:
    return 5000 if _nursery_to_iv(pws_class) else 6000


def transport_amount(distance: Optional[str]) -> int:
    if distance == "Over 5 km":
        return 3000
    if distance == "Up to 5 km":
        return 2500
    return 0


def _amount_for_category(category: str, pws_class: str, transport_distance: Optional[str] = None) -> int:
    if category == "Registration":
        return BASE_FEES["Registration"]["amount"]
    if category == "Admission Charges":
        return BASE_FEES["Admission Charges"]["amount"]
    if category == "Security (Refundable)":
        return security_amount(pws_class)
    if category == "Annual Charges":
        return annual_amount(pws_class)
    if category == "Tuition":
        return tuition_amount(pws_class)
    if category == "Physical Education":
        return pe_amount(pws_class)
    if category == "Exam Fee":
        return exam_amount(pws_class)
    if category == "Transport":
        return transport_amount(transport_distance)
    return 0


def resolve_category_amounts(
    pws_class: str,
    transport_enabled: bool = False,
    transport_distance: Optional[str] = None,
    overrides: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    """Default fee amounts per category with optional per-category overrides."""
    overrides = overrides or {}
    out: Dict[str, int] = {}
    for cat in FEE_CATEGORIES:
        if cat == "Transport" and not transport_enabled:
            continue
        base = _amount_for_category(cat, pws_class, transport_distance)
        out[cat] = int(overrides.get(cat, base))
    return out


def _fy_months(start: str = PWS_FY_START, end: str = PWS_FY_END) -> List[str]:
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    y, m = sy, sm
    out: List[str] = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _month_num(period: str) -> int:
    return int(period.split("-")[1])


@dataclass
class ScheduledFee:
    fee_type: str
    period_month: str
    amount: int
    category: str

    def to_dict(self) -> dict:
        return {
            "fee_type": self.fee_type,
            "period_month": self.period_month,
            "amount": self.amount,
            "category": self.category,
        }


def _map_fee_type(category: str) -> str:
    """Map display category to db.fees fee_type."""
    mapping = {
        "Registration": "Registration",
        "Admission Charges": "Admission",
        "Security (Refundable)": "Security",
        "Annual Charges": "Annual",
        "Tuition": "Monthly",
        "Physical Education": "Physical Education",
        "Exam Fee": "Exam",
        "Transport": "Transport",
    }
    return mapping.get(category, category)


def build_pws_fee_schedule(
    pws_class: str,
    date_of_admission: str,
    transport_enabled: bool = False,
    transport_distance: Optional[str] = None,
    overrides: Optional[Dict[str, int]] = None,
    academic_year: str = PWS_ACADEMIC_YEAR,
) -> List[ScheduledFee]:
    """Full FY schedule for a student profile (before payment status)."""
    amounts = resolve_category_amounts(pws_class, transport_enabled, transport_distance, overrides)
    admission_month = (date_of_admission or "2026-04-01")[:7]
    schedule: List[ScheduledFee] = []

    one_time = ("Registration", "Admission Charges", "Security (Refundable)", "Annual Charges")
    for cat in one_time:
        if cat not in amounts:
            continue
        schedule.append(ScheduledFee(
            fee_type=_map_fee_type(cat),
            period_month=admission_month,
            amount=amounts[cat],
            category=cat,
        ))

    for period in _fy_months():
        if period < admission_month:
            continue
        if "Tuition" in amounts:
            schedule.append(ScheduledFee("Monthly", period, amounts["Tuition"], "Tuition"))
        if transport_enabled and "Transport" in amounts:
            schedule.append(ScheduledFee("Transport", period, amounts["Transport"], "Transport"))
        mn = _month_num(period)
        if mn in (4, 9) and "Physical Education" in amounts:
            schedule.append(ScheduledFee("Physical Education", period, amounts["Physical Education"], "Physical Education"))
        if mn in (9, 2) and "Exam Fee" in amounts:
            schedule.append(ScheduledFee("Exam", period, amounts["Exam Fee"], "Exam Fee"))

    return schedule


def structure_metadata() -> dict:
    return {
        "academic_year": PWS_ACADEMIC_YEAR,
        "fy_start": PWS_FY_START,
        "fy_end": PWS_FY_END,
        "student_types": list(PWS_STUDENT_TYPES),
        "classes": list(PWS_CLASSES),
        "transport_distances": list(TRANSPORT_DISTANCES),
        "categories": list(FEE_CATEGORIES),
        "base_fees": BASE_FEES,
    }


def student_type_to_legacy(pws_student_type: Optional[str], is_resident: bool = False) -> str:
    if pws_student_type == "Boarding":
        return "Hostel"
    if pws_student_type == "Day Boarding":
        return "Day Boarding"
    if pws_student_type == "Day School":
        return "Day Scholar"
    return "Hostel" if is_resident else "Day Scholar"


def pws_student_profile_from_person(person: dict) -> dict:
    """Normalize person document to PWS fee profile."""
    pws_class = person.get("pws_class") or person.get("group") or "Class I"
    if pws_class not in PWS_CLASSES:
        pws_class = "Class I"
    transport_enabled = bool(person.get("transport_enabled"))
    if not transport_enabled and int(person.get("transport_fee_monthly") or 0) > 0:
        transport_enabled = True
    return {
        "pws_class": pws_class,
        "pws_student_type": person.get("pws_student_type") or student_type_to_legacy(None, person.get("is_resident")),
        "transport_enabled": transport_enabled,
        "transport_distance": person.get("transport_distance") or ("Up to 5 km" if transport_enabled else None),
        "date_of_admission": person.get("date_of_admission") or "2026-04-01",
        "overrides": person.get("pws_fee_overrides") or {},
    }
