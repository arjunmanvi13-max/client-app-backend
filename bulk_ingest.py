"""Declarative column specs for bulk ingestion.

One spec per record kind drives three things: the
downloadable template, the upload validator, and the sample rows. Add a column
here and it appears in all three.

Covers every field the roster/directory forms expose, so a sheet can carry a
complete record rather than the 12-column subset the old player-only upload took.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from alpha_centre_rules import defense_colony_advanced_slot_error
from pws_fee_structure import (
    FEE_CATEGORIES,
    PWS_CLASSES,
    PWS_STUDENT_TYPES,
    TRANSPORT_DISTANCES,
)

SECTION_LETTERS = ("A", "B", "C", "D", "E", "F")
GENDERS = ("Male", "Female", "Other")
CENTRES = ("Balua", "Harding Park", "Defense Colony")
DAILY_ONLY_CENTRES = frozenset({"Harding Park", "Defense Colony"})
SPORTS = ("Cricket", "Football")
PLAYER_TYPES = ("Daily", "Day Boarding", "Hostel", "Boarding")
SLOTS = ("Morning", "Evening", "Both")
SKILL_LEVELS = ("Beginner", "Intermediate", "Advanced")
QUALIFICATIONS = ("B.Ed", "Bachelor's Degree", "Master's Degree", "Other")
TEACHER_DESIGNATIONS = ("CLASS_TEACHER", "TEACHER")
ORGANIZATIONS = ("PWS", "ALPHA", "BOTH")
STATUSES = ("active", "deactivated")

TRUE_WORDS = {"y", "yes", "true", "1", "t"}
FALSE_WORDS = {"n", "no", "false", "0", "f", ""}

MAX_MONEY = 10_000_000


class CellError(ValueError):
    """Raised by a coercer to reject one cell with a human-readable reason."""


MAX_TEXT_LEN = 255


def coerce_text(raw: str) -> Optional[str]:
    s = (raw or "").strip()
    if s[:1] in ("=", "+", "@") or (s[:1] == "-" and not s[1:2].isdigit()):
        s = "'" + s
    if len(s) > MAX_TEXT_LEN:
        raise CellError(f"must be at most {MAX_TEXT_LEN} characters")
    return s or None


def coerce_upper_text(raw: str) -> Optional[str]:
    s = coerce_text(raw)
    return s.upper() if s else None


def coerce_int(raw: str) -> Optional[int]:
    s = (raw or "").strip().replace(",", "")
    s = re.sub(r"^(?:₹|rs\.?|inr)\s*", "", s, flags=re.IGNORECASE).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except (ValueError, OverflowError):
        raise CellError("must be a whole number")


def coerce_money(raw: str) -> Optional[int]:
    value = coerce_int(raw)
    if value is None:
        return None
    if value < 0:
        raise CellError("must not be negative")
    if value > MAX_MONEY:
        raise CellError(f"must not exceed {MAX_MONEY:,}")
    return value


def coerce_bool(raw: str) -> Optional[bool]:
    s = (raw or "").strip().lower()
    if s in TRUE_WORDS:
        return True
    if s in FALSE_WORDS:
        return False
    raise CellError("must be Yes or No")


def coerce_date(raw: str) -> Optional[str]:
    """Accept YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, or an Excel datetime; store ISO."""
    s = (raw or "").strip()
    if not s:
        return None
    if "T" in s or " " in s:
        s = re.split(r"[ T]", s)[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if not (1900 <= parsed.year <= datetime.now().year + 1):
            raise CellError("year must be between 1900 and next year")
        return parsed.strftime("%Y-%m-%d")
    raise CellError("must be a date like 2026-04-15 or 15/04/2026")


def coerce_mobile(raw: str) -> Optional[str]:
    s = (raw or "").strip()
    if not s:
        return None
    s = re.sub(r"\.0+$", "", s)
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        raise CellError("must be a 10-digit Indian mobile number")
    return digits


def coerce_email(raw: str) -> Optional[str]:
    s = (raw or "").strip().lower()
    if not s:
        return None
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", s):
        raise CellError("must be a valid email address")
    return s


def coerce_aadhaar(raw: str) -> Optional[str]:
    s = "".join(ch for ch in (raw or "") if ch.isalnum()).upper()
    if not s:
        return None
    if len(s) != 12:
        raise CellError("must be exactly 12 alphanumeric characters")
    return s


def make_enum_coercer(allowed: Sequence[str], *, aliases: Optional[Dict[str, str]] = None) -> Callable[[str], Optional[str]]:
    lookup = {a.strip().lower(): a for a in allowed}
    for k, v in (aliases or {}).items():
        lookup[k.strip().lower()] = v

    def _coerce(raw: str) -> Optional[str]:
        s = (raw or "").strip()
        if not s:
            return None
        hit = lookup.get(s.lower())
        if hit is None:
            raise CellError("must be one of: " + ", ".join(allowed))
        return hit

    return _coerce


@dataclass(frozen=True)
class Column:
    label: str
    target: str
    coerce: Callable[[str], Any] = coerce_text
    required: bool = False
    allowed: Tuple[str, ...] = ()
    example: str = ""
    note: str = ""

    @property
    def help(self) -> str:
        if self.note:
            return self.note
        if self.allowed:
            return "One of: " + ", ".join(self.allowed)
        return ""


@dataclass(frozen=True)
class SheetSpec:
    kind: str
    sheet_name: str
    title: str
    columns: List[Column]
    samples: List[Dict[str, str]] = field(default_factory=list)

    @property
    def labels(self) -> List[str]:
        return [c.label for c in self.columns]

    def column_by_label(self, label: str) -> Optional[Column]:
        norm = _normalize_header(label)
        for c in self.columns:
            if _normalize_header(c.label) == norm:
                return c
        return None


def _normalize_header(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (label or "").lower())


def _enum(allowed, **kw) -> Column:
    kw.setdefault("coerce", make_enum_coercer(allowed))
    kw["allowed"] = tuple(allowed)
    return Column(**kw)


def _contact_columns(*, mobile_required: bool = False) -> List[Column]:
    return [
        Column("Primary Mobile", "mobile", coerce_mobile, required=mobile_required,
               example="9876543210", note="10-digit Indian mobile"),
        Column("Email", "email", coerce_email, example="aarav@example.com"),
        Column("Address", "address", example="12 Boring Road"),
        Column("Locality", "locality", example="Boring Road"),
        Column("City", "city", example="Patna"),
    ]


STUDENT_SPEC = SheetSpec(
    kind="student",
    sheet_name="Students",
    title="PWS Students",
    columns=[
        Column("Full Name", "name", required=True, example="Aarav Mishra"),
        Column("Admission Number", "admission_number", example="PWS2026001",
               note="Must be unique; leave blank to skip"),
        Column("Roll Number", "roll_number", example="12"),
        _enum(PWS_CLASSES, label="Class", target="pws_class", required=True, example="Class IX"),
        _enum(SECTION_LETTERS, label="Section", target="_section_letter", example="A"),
        _enum(GENDERS, label="Gender", target="gender", example="Male"),
        Column("Date of Birth", "dob", coerce_date, example="2012-06-14",
               note="YYYY-MM-DD or DD/MM/YYYY; age is derived from this"),
        Column("Date of Admission", "date_of_admission", coerce_date, required=True,
               example="2026-04-15"),
        _enum(PWS_STUDENT_TYPES, label="Student Type", target="pws_student_type",
              required=True, example="Day School"),
        Column("Father's Name", "father_name", example="Ramesh Mishra"),
        Column("Mother's Name", "mother_name", example="Sunita Mishra"),
        Column("Guardian Name", "guardian_name", example="Ramesh Mishra"),
        Column("Emergency Contact", "guardian_phone", coerce_mobile, example="9876543211"),
        *_contact_columns(),
        Column("Hostel Resident", "is_resident", coerce_bool, example="No",
               note="Yes / No"),
        Column("Transport Enabled", "transport_enabled", coerce_bool, example="No",
               note="Yes / No"),
        _enum(TRANSPORT_DISTANCES, label="Transport Distance", target="transport_distance",
              example="Up to 5 km", note="Required when Transport Enabled is Yes"),
        Column("Transport Fee Monthly", "transport_fee_monthly", coerce_money, example="0"),
        Column("Monthly Fee Override", "monthly_fee_override", coerce_money, example="",
               note="Blank = use the fee catalogue rate"),
        Column("Registration Fee Override", "registration_fee_override", coerce_money, example=""),
        Column("Hostel Fee Override", "hostel_fee_override", coerce_money, example=""),
        *[
            Column(f"Fee Override: {cat}", f"_pws_override::{cat}", coerce_money, example="",
                   note=f"Overrides the {cat} amount for this student only")
            for cat in FEE_CATEGORIES
        ],
        _enum(STATUSES, label="Status", target="status", example="active"),
    ],
    samples=[
        {
            "Full Name": "Aarav Mishra", "Admission Number": "PWS2026001", "Roll Number": "12",
            "Class": "Class IX", "Section": "A", "Gender": "Male",
            "Date of Birth": "2012-06-14", "Date of Admission": "2026-04-15",
            "Student Type": "Day School", "Father's Name": "Ramesh Mishra",
            "Mother's Name": "Sunita Mishra", "Guardian Name": "Ramesh Mishra",
            "Emergency Contact": "9876543211", "Primary Mobile": "9876543210",
            "Email": "aarav.mishra@example.com", "Address": "12 Boring Road",
            "Locality": "Boring Road", "City": "Patna", "Hostel Resident": "No",
            "Transport Enabled": "Yes", "Transport Distance": "Up to 5 km",
            "Transport Fee Monthly": "1200", "Status": "active",
        },
        {
            "Full Name": "Isha Sinha", "Admission Number": "PWS2026002", "Roll Number": "13",
            "Class": "Class X", "Section": "B", "Gender": "Female",
            "Date of Birth": "2011-11-02", "Date of Admission": "2026-04-15",
            "Student Type": "Boarding", "Father's Name": "Alok Sinha",
            "Mother's Name": "Rekha Sinha", "Guardian Name": "Alok Sinha",
            "Emergency Contact": "9876543212", "Primary Mobile": "9876543213",
            "Address": "Danapur", "Locality": "Danapur", "City": "Patna",
            "Hostel Resident": "Yes", "Transport Enabled": "No",
            "Fee Override: Tuition": "9500", "Status": "active",
        },
    ],
)


PLAYER_SPEC = SheetSpec(
    kind="player",
    sheet_name="Players",
    title="ALPHA Players",
    columns=[
        Column("Full Name", "name", required=True, example="Karan Raj"),
        Column("Father's Name", "father_name", example="Mahesh Raj"),
        Column("Guardian Name", "guardian_name", example="Mahesh Raj"),
        Column("Guardian Phone", "guardian_phone", coerce_mobile, example="9876543220"),
        Column("Age", "age", coerce_int, example="14",
               note="Ignored when Date of Birth is supplied"),
        Column("Date of Birth", "dob", coerce_date, example="2012-03-09"),
        Column("Mobile Number", "mobile", coerce_mobile, required=True, example="9876543221"),
        *[c for c in _contact_columns() if c.target in ("email", "address", "locality", "city")],
        _enum(CENTRES, label="Centre", target="centre", required=True, example="Balua"),
        _enum(SPORTS, label="Sport", target="sport", required=True, example="Cricket"),
        _enum(PLAYER_TYPES, label="Player Type", target="player_type", required=True,
              example="Daily", note="Harding Park and Defense Colony support Daily only"),
        _enum(SLOTS, label="Slot", target="slot", required=True, example="Morning",
              note="Defense Colony Advanced players must use Morning"),
        _enum(SKILL_LEVELS, label="Skill Level", target="skill_level", required=True,
              example="Beginner"),
        Column("Date of Admission", "date_of_admission", coerce_date, required=True,
               example="2026-04-15"),
        Column("Transport Fee Monthly", "transport_fee_monthly", coerce_money, example="0"),
        Column("Monthly Fee Override", "monthly_fee_override", coerce_money, example=""),
        Column("Registration Fee Override", "registration_fee_override", coerce_money, example=""),
        Column("Hostel Fee Override", "hostel_fee_override", coerce_money, example=""),
        _enum(PWS_CLASSES, label="Boarding Class", target="pws_class", example="",
              note="Only for Boarding players who also attend PWS"),
        _enum(SECTION_LETTERS, label="Boarding Section", target="_section_letter", example=""),
        _enum(STATUSES, label="Status", target="status", example="active"),
    ],
    samples=[
        {
            "Full Name": "Karan Raj", "Father's Name": "Mahesh Raj",
            "Guardian Name": "Mahesh Raj", "Guardian Phone": "9876543220",
            "Date of Birth": "2012-03-09", "Mobile Number": "9876543221",
            "Address": "Balua Road", "Locality": "Balua", "City": "Patna",
            "Centre": "Balua", "Sport": "Cricket", "Player Type": "Hostel",
            "Slot": "Morning", "Skill Level": "Intermediate",
            "Date of Admission": "2026-04-15", "Transport Fee Monthly": "0",
            "Status": "active",
        },
        {
            "Full Name": "Riya Singh", "Father's Name": "Vikas Singh",
            "Guardian Phone": "9876543222", "Age": "13",
            "Mobile Number": "9876543223", "Locality": "Harding Park", "City": "Patna",
            "Centre": "Harding Park", "Sport": "Football", "Player Type": "Daily",
            "Slot": "Evening", "Skill Level": "Beginner",
            "Date of Admission": "2026-05-02", "Status": "active",
        },
        {
            "Full Name": "Mohit Yadav", "Father's Name": "Rajesh Yadav",
            "Guardian Phone": "9876543224", "Age": "14",
            "Mobile Number": "9876543225", "Locality": "Defense Colony", "City": "Patna",
            "Centre": "Defense Colony", "Sport": "Cricket", "Player Type": "Daily",
            "Slot": "Morning", "Skill Level": "Beginner",
            "Date of Admission": "2026-05-05", "Status": "active",
        },
    ],
)


STAFF_SPEC = SheetSpec(
    kind="staff",
    sheet_name="Staff",
    title="Support Staff",
    columns=[
        Column("Full Name", "name", required=True, example="Sonu Kumar"),
        Column("Employee ID", "employee_id", coerce_upper_text, example="EMP2026001",
               note="Must be unique; leave blank to skip"),
        _enum(ORGANIZATIONS, label="Organization", target="organization", required=True,
              example="PWS"),
        Column("Department", "department", example="Maintenance"),
        Column("Designation", "group", example="Electrician",
               note="Stored as the staff grouping label"),
        _enum(GENDERS, label="Gender", target="gender", example="Male"),
        Column("Date of Birth", "dob", coerce_date, example="1990-02-20"),
        Column("Date of Joining", "date_of_admission", coerce_date, example="2026-04-01"),
        *_contact_columns(mobile_required=True),
        Column("Guardian Name", "guardian_name", example=""),
        Column("Guardian Phone", "guardian_phone", coerce_mobile, example=""),
        _enum(CENTRES, label="Centre", target="centre", example="",
              note="ALPHA staff only"),
        _enum(STATUSES, label="Status", target="status", example="active"),
    ],
    samples=[
        {
            "Full Name": "Sonu Kumar", "Employee ID": "EMP2026001", "Organization": "PWS",
            "Department": "Maintenance", "Designation": "Electrician", "Gender": "Male",
            "Date of Birth": "1990-02-20", "Date of Joining": "2026-04-01",
            "Primary Mobile": "9876543230", "Address": "Kankarbagh", "Locality": "Kankarbagh",
            "City": "Patna", "Status": "active",
        },
    ],
)


TEACHER_SPEC = SheetSpec(
    kind="teacher",
    sheet_name="Teachers",
    title="PWS Teachers (Directory)",
    columns=[
        Column("Full Name", "name", required=True, example="Priya Kumari"),
        Column("Date of Birth", "date_of_birth", coerce_date, required=True, example="1992-08-11"),
        Column("Address", "address", required=True, example="Rajendra Nagar, Patna"),
        Column("Mobile", "mobile", coerce_mobile, required=True, example="9876543240"),
        Column("Personal Email", "personal_email", coerce_email, required=True,
               example="priya.kumari@example.com", note="Personal address, not the login email"),
        Column("Aadhaar Number", "aadhaar_number", coerce_aadhaar, required=True,
               example="123456789012", note="Exactly 12 characters"),
        _enum(QUALIFICATIONS, label="Qualification", target="qualification", required=True,
              example="B.Ed"),
        Column("Qualification (Other)", "qualification_other", example="",
               note="Required when Qualification is Other"),
        Column("Last Job", "last_job", required=True, example="DAV Public School"),
        Column("Guardian Name", "guardian_name", required=True, example="Suresh Kumar"),
        Column("Guardian Mobile", "guardian_mobile", coerce_mobile, required=True,
               example="9876543241"),
        Column("Reference Name", "reference_name", required=True, example="Anil Verma"),
        Column("Reference Mobile", "reference_mobile", coerce_mobile, required=True,
               example="9876543242"),
        _enum(TEACHER_DESIGNATIONS, label="Teacher Designation", target="teacher_designation",
              example="TEACHER"),
        Column("Date of Joining", "date_of_joining", coerce_date, example="2026-04-01"),
    ],
    samples=[
        {
            "Full Name": "Priya Kumari", "Date of Birth": "1992-08-11",
            "Address": "Rajendra Nagar, Patna", "Mobile": "9876543240",
            "Personal Email": "priya.kumari@example.com", "Aadhaar Number": "123456789012",
            "Qualification": "B.Ed", "Last Job": "DAV Public School",
            "Guardian Name": "Suresh Kumar", "Guardian Mobile": "9876543241",
            "Reference Name": "Anil Verma", "Reference Mobile": "9876543242",
            "Teacher Designation": "CLASS_TEACHER", "Date of Joining": "2026-04-01",
        },
    ],
)


SPECS: Dict[str, SheetSpec] = {
    s.kind: s for s in (STUDENT_SPEC, PLAYER_SPEC, STAFF_SPEC, TEACHER_SPEC)
}
SPEC_BY_SHEET: Dict[str, SheetSpec] = {
    _normalize_header(s.sheet_name): s for s in SPECS.values()
}
KINDS: Tuple[str, ...] = tuple(SPECS.keys())


def parse_row(spec: SheetSpec, raw_row: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Coerce one sheet row into field values. Returns (values, errors)."""
    normalized = {
        _normalize_header(k): ("" if v is None else str(v))
        for k, v in (raw_row or {}).items()
    }
    values: Dict[str, Any] = {}
    overrides: Dict[str, int] = {}
    errors: List[str] = []

    for col in spec.columns:
        raw = normalized.get(_normalize_header(col.label), "")
        try:
            value = col.coerce(raw)
        except CellError as exc:
            errors.append(f"{col.label}: {exc}")
            continue
        if value is None or value == "":
            if col.required:
                errors.append(f"{col.label}: required")
            continue
        if col.target.startswith("_pws_override::"):
            overrides[col.target.split("::", 1)[1]] = value
        else:
            values[col.target] = value

    if overrides:
        values["pws_fee_overrides"] = overrides

    unknown = [
        k for k in (raw_row or {})
        if k and _normalize_header(k) and spec.column_by_label(k) is None
    ]
    if unknown:
        errors.append("Unrecognised column(s): " + ", ".join(sorted(set(unknown))))

    errors.extend(_cross_field_errors(spec, values))
    return values, errors


def _cross_field_errors(spec: SheetSpec, v: Dict[str, Any]) -> List[str]:
    """Rules that need more than one cell to evaluate."""
    errs: List[str] = []
    if spec.kind == "player":
        centre = v.get("centre")
        if centre in DAILY_ONLY_CENTRES and v.get("player_type") not in (None, "Daily"):
            errs.append(f"Player Type: {centre} supports Daily only")
        slot_err = defense_colony_advanced_slot_error(centre, v.get("skill_level"), v.get("slot"))
        if slot_err:
            errs.append(f"Slot: {slot_err}")
    if spec.kind == "student":
        if v.get("transport_enabled") and not v.get("transport_distance"):
            errs.append("Transport Distance: required when Transport Enabled is Yes")
    if spec.kind == "teacher":
        if v.get("qualification") == "Other" and not v.get("qualification_other"):
            errs.append("Qualification (Other): required when Qualification is Other")
    return errs


def duplicate_key(spec: SheetSpec, values: Dict[str, Any]) -> Optional[Tuple]:
    """In-file duplicate key — catches the same person listed twice in one sheet."""
    name = (values.get("name") or "").strip().lower()
    if not name:
        return None
    dob = values.get("date_of_birth") if spec.kind == "teacher" else values.get("dob")
    if not dob:
        return None
    return (name, dob)
