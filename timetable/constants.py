"""PWS Time Table constants and seed templates."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Tuple

ENTITY_PWS = "pws"

DAYS_OF_WEEK = ("MON", "TUE", "WED", "THU", "FRI", "SAT")
DAY_LABELS = {
    "MON": "Monday",
    "TUE": "Tuesday",
    "WED": "Wednesday",
    "THU": "Thursday",
    "FRI": "Friday",
    "SAT": "Saturday",
}

SCHEDULE_GROUPS = ("PRE_PRIMARY", "PRIMARY_SECONDARY")
DAY_TYPES = ("WEEKDAY", "SATURDAY")
PERIOD_TYPES = ("TEACHING", "ASSEMBLY", "BREAK", "LUNCH", "HOME_ROOM", "CLUB")
SLOT_STATUSES = ("DRAFT", "PUBLISHED", "ARCHIVED")
SUBSTITUTION_REASONS = (
    "TEACHER_ABSENT", "ON_LEAVE", "OFFICIAL_DUTY", "MEDICAL", "EXAM_DUTY", "OTHER",
)
DUTY_TYPES = ("LUNCH_DUTY", "CLUB_INCHARGE", "ASSEMBLY_DUTY")

PRE_PRIMARY_GRADES = {"Nur", "Nursery", "LKG", "UKG"}
PRIMARY_GRADES = {str(i) for i in range(1, 11)} | {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}

# Display-order class labels → grade name in db.grades
TIMETABLE_CLASS_LABELS: List[Tuple[str, str, str | None]] = [
    ("Nursery", "Nur", None),
    ("LKG", "LKG", None),
    ("UKG", "UKG", None),
    ("Std I", "1", None),
    ("Std II", "2", None),
    ("Std III", "3", None),
    ("Std IV", "4", None),
    ("Std V", "5", None),
    ("Std VI", "6", None),
    ("Std VII", "7", None),
    ("Std VIII", "8", None),
    ("Std IX", "9", None),
    ("Std X A", "10", "A"),
    ("Std X B", "10", "B"),
]

TIMETABLE_SUBJECTS = [
    "English", "Hindi", "Sanskrit", "Mathematics", "EVS", "General Awareness",
    "Science", "Physics", "Chemistry", "Biology", "Social Science", "History",
    "Geography", "Political Science", "Economics", "Computer / IT", "Yoga", "Games",
    "Art & Craft", "Sports Club", "School Cinema", "English Story Telling", "Rhymes",
    "Discover & Explore", "NCC",
]

CLUB_INCHARGE_ROSTER = [
    ("Cricket", "Nandan"),
    ("Football", "Rituraj"),
    ("Karate", "Shatrunjay"),
    ("Kabaddi", "Preeti"),
    ("Kho-Kho", "Priya Kumari"),
    ("Chess", "Bineeta"),
    ("Table Tennis", "Aakash"),
]

TEACHER_SEED_NAMES = [
    "Shalini", "Shamshad", "Taniya", "Vandana", "Shwetambari", "Bineeta", "Priya Kumari",
    "Aakash", "Shambhavi", "Preeti", "Rituraj", "Rani", "Hemanti", "Nandan", "Madhuri",
    "Anjali", "Amita", "Ali Akbar", "Bandana Kumari", "Ritu", "Shatrunjay",
]

DEFAULT_MAX_WEEKLY_PERIODS = 35

# (order, label, start, end, period_type)
PRE_PRIMARY_WEEKDAY: List[Tuple[int, str, str, str, str]] = [
    (0, "Assembly & Home Rule", "07:30", "07:45", "ASSEMBLY"),
    (1, "1st", "07:45", "08:25", "TEACHING"),
    (2, "2nd", "08:25", "09:05", "TEACHING"),
    (3, "3rd", "09:05", "09:45", "TEACHING"),
    (4, "Lunch", "09:45", "10:05", "LUNCH"),
    (5, "4th", "10:05", "10:35", "TEACHING"),
    (6, "5th", "10:35", "11:10", "TEACHING"),
]

PRIMARY_WEEKDAY: List[Tuple[int, str, str, str, str]] = [
    (0, "Assembly & Home Rule", "07:30", "08:00", "ASSEMBLY"),
    (1, "1st", "08:00", "08:45", "TEACHING"),
    (2, "2nd", "08:45", "09:25", "TEACHING"),
    (3, "3rd", "09:25", "10:05", "TEACHING"),
    (4, "4th", "10:05", "10:45", "TEACHING"),
    (5, "Lunch", "10:45", "11:15", "LUNCH"),
    (6, "5th", "11:15", "12:00", "TEACHING"),
    (7, "6th", "12:00", "12:40", "TEACHING"),
    (8, "7th", "12:40", "13:20", "TEACHING"),
    (9, "Home Room", "13:20", "13:30", "HOME_ROOM"),
]

SATURDAY_PERIODS: List[Tuple[int, str, str, str, str]] = [
    (0, "Assembly & Home Rule", "09:00", "09:15", "ASSEMBLY"),
    (1, "1st", "09:15", "09:50", "TEACHING"),
    (2, "2nd", "09:50", "10:25", "TEACHING"),
    (3, "Lunch", "10:25", "11:00", "LUNCH"),
    (4, "3rd", "11:00", "11:30", "TEACHING"),
    (5, "4th", "11:30", "12:00", "TEACHING"),
]

PERIOD_SEED_TEMPLATES: Dict[str, List[Tuple[int, str, str, str, str]]] = {
    "PRE_PRIMARY_WEEKDAY": PRE_PRIMARY_WEEKDAY,
    "PRIMARY_SECONDARY_WEEKDAY": PRIMARY_WEEKDAY,
    "SATURDAY": SATURDAY_PERIODS,
}


def schedule_group_for_grade(grade_name: str) -> str:
    g = (grade_name or "").strip()
    if g in PRE_PRIMARY_GRADES or g.lower() in {"nur", "nursery"}:
        return "PRE_PRIMARY"
    return "PRIMARY_SECONDARY"


def is_teaching_period(period_type: str) -> bool:
    return period_type == "TEACHING"


def day_of_week_for_date(date_iso: str) -> str | None:
    """Return MON–SAT for a calendar date, or None on Sunday."""
    wd = datetime.fromisoformat(date_iso[:10]).weekday()
    if wd >= len(DAYS_OF_WEEK):
        return None
    return DAYS_OF_WEEK[wd]
