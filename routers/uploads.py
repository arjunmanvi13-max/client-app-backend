"""Bulk Upload — CSV / XLSX of players (auto-creates fees).

Required columns (case-insensitive):
Name, Father's Name, Age, Mobile Number, Locality, City, Centre, Sport,
Category, Slot, Skill Level, Date of Admission
"""
import csv
import io
import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import PlainTextResponse
from core import db, get_current_user, now_utc, notify_role
from rbac.guards import can_bulk_upload
from routers.fees import auto_create_fees_for_player

router = APIRouter(prefix="/bulk-upload", tags=["bulk-upload"])

REQUIRED_FIELDS = [
    "Name", "Father's Name", "Age", "Mobile Number", "Locality", "City",
    "Centre", "Sport", "Category", "Slot", "Skill Level", "Date of Admission",
]
ROW_LIMIT = 500
VALID_CENTRES = {"Balua", "Harding Park"}
VALID_SPORTS = {"Cricket", "Football"}
VALID_CATEGORIES = {"Daily", "Day Boarding", "Hostel"}
VALID_SLOTS = {"Morning", "Evening"}
VALID_SKILLS = {"Beginner", "Intermediate", "Advanced"}


def _row_to_player(row: dict) -> tuple[dict, list[str]]:
    """Map normalized row -> player_doc + list of validation errors."""
    errs = []
    g = lambda k: (row.get(k) or "").strip()
    name = g("Name")
    if not name:
        errs.append("Name required")
    centre = g("Centre")
    if centre not in VALID_CENTRES:
        errs.append(f"Centre must be one of {sorted(VALID_CENTRES)}")
    sport = g("Sport")
    if sport not in VALID_SPORTS:
        errs.append(f"Sport must be one of {sorted(VALID_SPORTS)}")
    category = g("Category")
    if category not in VALID_CATEGORIES:
        errs.append(f"Category must be one of {sorted(VALID_CATEGORIES)}")
    if centre == "Harding Park" and category != "Daily":
        errs.append("Harding Park allows Daily only")
    slot = g("Slot")
    if slot not in VALID_SLOTS:
        errs.append(f"Slot must be Morning or Evening")
    skill = g("Skill Level")
    if skill not in VALID_SKILLS:
        errs.append(f"Skill Level must be Beginner / Intermediate / Advanced")
    age_raw = g("Age")
    age = None
    if age_raw:
        try:
            age = int(float(age_raw))
        except Exception:
            errs.append("Age must be a number")
    doa = g("Date of Admission")
    if not doa:
        errs.append("Date of Admission required (YYYY-MM-DD)")
    elif len(doa) >= 10:
        # try to normalise
        doa = doa[:10]

    if errs:
        return {}, errs
    doc = {
        "id": str(uuid.uuid4()),
        "name": name,
        "kind": "player",
        "organization": "ALPHA",
        "father_name": g("Father's Name") or None,
        "age": age,
        "mobile": g("Mobile Number") or None,
        "locality": g("Locality") or None,
        "city": g("City") or None,
        "centre": centre,
        "sport": sport,
        "player_type": category,
        "slot": slot,
        "skill_level": skill,
        "date_of_admission": doa,
        "status": "active",
        "is_resident": category == "Hostel",
        "assigned_coach_id": None,
        "group": f"{slot} {sport}",
        "created_at": now_utc().isoformat(),
    }
    return doc, []


def _parse_csv(raw: bytes) -> List[dict]:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in reader]


def _parse_xlsx(raw: bytes) -> List[dict]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise HTTPException(500, "openpyxl not installed on server")
    wb = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    out: List[dict] = []
    for r in rows[1:]:
        if all(v is None or v == "" for v in r):
            continue
        out.append({headers[i]: ("" if v is None else str(v)) for i, v in enumerate(r) if i < len(headers)})
    return out


@router.get("/template", response_class=PlainTextResponse)
async def download_template(user: dict = Depends(get_current_user)):
    if not can_bulk_upload(user):
        raise HTTPException(403, "bulk_upload permission required")
    sample = (
        ",".join(REQUIRED_FIELDS) + "\n"
        + "Aarav Kumar,Ramesh Kumar,14,9876543210,Boring Road,Patna,Balua,Cricket,Daily,Morning,Beginner,2026-05-05\n"
    )
    return sample


@router.post("/players")
async def upload_players(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    if not can_bulk_upload(user):
        raise HTTPException(403, "bulk_upload permission required")
    raw = await file.read()
    name_lc = (file.filename or "").lower()
    try:
        if name_lc.endswith(".csv") or (file.content_type or "").startswith("text/"):
            rows = _parse_csv(raw)
        elif name_lc.endswith(".xlsx") or "spreadsheet" in (file.content_type or ""):
            rows = _parse_xlsx(raw)
        else:
            # try CSV first as fallback
            rows = _parse_csv(raw)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Could not parse file: {e}")

    if not rows:
        raise HTTPException(400, "File is empty")
    if len(rows) > ROW_LIMIT:
        raise HTTPException(400, f"Row limit is {ROW_LIMIT}")

    # validate all rows first
    errors = []
    docs = []
    for i, row in enumerate(rows, start=2):  # row 1 is header
        # normalise keys (strip)
        n = {(k or "").strip(): (v if v is not None else "") for k, v in row.items()}
        missing = [k for k in REQUIRED_FIELDS if k not in n]
        if missing:
            errors.append({"row": i, "errors": [f"Missing column: {m}" for m in missing], "name": n.get("Name") or ""})
            continue
        doc, errs = _row_to_player(n)
        if errs:
            errors.append({"row": i, "errors": errs, "name": n.get("Name") or ""})
        else:
            docs.append(doc)

    if errors:
        return {"status": "validation_failed", "valid_count": len(docs), "errors": errors}

    # All valid — insert players + auto-create fees
    if docs:
        await db.people.insert_many(docs)
        fees_created = 0
        for d in docs:
            created = await auto_create_fees_for_player(d)
            fees_created += len(created)
        # Notify super admin (single bulk)
        await notify_role(
            "super_admin",
            ntype="bulk_upload_completed",
            title="Bulk upload completed",
            message=f"{user['name']} uploaded {len(docs)} players · {fees_created} fees auto-generated",
        )
        return {"status": "ok", "players_created": len(docs), "fees_created": fees_created, "errors": []}
    return {"status": "ok", "players_created": 0, "fees_created": 0, "errors": []}
