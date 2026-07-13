"""Fees module — ALPHA Sports Academy + PWS school fees.

Auto-create Registration + first Monthly fee on player/student creation.
Rate cards:
- Daily players (Balua / Harding Park):
    Cricket Reg ₹3000 (one-time), Monthly ₹2500
    Football Reg ₹3000 (one-time), Monthly ₹2000
- Balua Hostel:
    Cricket Reg ₹20000, Monthly ₹12000
    Football Reg ₹20000, Monthly ₹15000
- Balua Day Boarding:
    Cricket Reg ₹20000, Monthly ₹7500
    Football Reg ₹20000, Monthly ₹7500

Monthly first-month rule: admission day <= 15 -> full fee, day >= 16 -> 50%.
Subsequent months always full.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, Literal, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from collections import defaultdict
from core import (
    db, get_current_user, is_admin, is_super_admin, assert_perm, now_utc, get_perm, notify_role,
    resolve_user_institution, fee_entity_filter, derive_person_entities, person_entity_filter,
    is_sports_admin, format_date_display, format_datetime_display, format_month_display,
)

from fees_collection_utils import compute_player_fee_status

router = APIRouter(prefix="/fees", tags=["fees"])

# ------------------ Rate Cards (per spec — ALPHA Sports Academy) ------------------
# NOTE: "Hostel" and "Hostel Only" are the SAME category. Old records may still use "Hostel".
RATE_CARDS = {
    # category: { sport: {"registration": amount, "monthly": amount} }
    "Daily": {
        "Cricket": {"registration": 3000, "monthly": 2500},
        "Football": {"registration": 3000, "monthly": 2000},
    },
    "Hostel Only": {
        "Cricket": {"registration": 3000, "monthly": 12000},
        "Football": {"registration": 3000, "monthly": 15000},
    },
    # Backward-compat alias — same amounts as Hostel Only
    "Hostel": {
        "Cricket": {"registration": 3000, "monthly": 12000},
        "Football": {"registration": 3000, "monthly": 15000},
    },
    "Day Boarding": {
        "Cricket": {"registration": 3000, "monthly": 7500},
        "Football": {"registration": 3000, "monthly": 7500},
    },
    # Boarding = full residential (hostel + morning & evening coaching, all-inclusive)
    "Boarding": {
        "Cricket": {"registration": 20000, "monthly": 15000},
        "Football": {"registration": 20000, "monthly": 15000},
    },
}

# ------------------ Rate Cards (PWS — Prarambhika World School) ------------------
PWS_RATE_CARDS = {
    "Day Scholar": {
        "registration": 5000,
        "monthly": 8000,   # tuition
        "exam": 2500,
    },
    "Hostel": {
        "registration": 5000,
        "monthly": 8000,   # tuition
        "hostel_monthly": 12000,
        "exam": 2500,
    },
}


def _pws_category(student: dict) -> str:
    return "Hostel" if student.get("is_resident") else "Day Scholar"


def _fee_entity(person: dict) -> str:
    ents = derive_person_entities(person)
    if "PWS" in ents and person.get("kind") == "student":
        return "pws"
    if "PWS" in ents and "ALPHA" not in ents:
        return "pws"
    return "alpha"


def get_pws_fee_rates(category: str) -> dict:
    return PWS_RATE_CARDS.get(category, {})


async def _rates_for_person(person: dict) -> dict:
    """Resolve rates from fee catalogue/plan, falling back to hardcoded cards."""
    try:
        from routers.fee_catalog import resolve_rates_for_person
        catalog = await resolve_rates_for_person(person)
        if catalog:
            return catalog
    except Exception:
        pass
    if person.get("kind") == "student" and "PWS" in derive_person_entities(person):
        return get_pws_fee_rates(_pws_category(person))
    category = _canonical_category(person.get("player_type") or "Daily")
    sport = person.get("sport") or ""
    return get_fee_rates(category, sport)


async def _recurring_amounts_async(person: dict) -> dict:
    """Recurring fee amounts from catalogue/plan with hardcoded fallback."""
    rates = await _rates_for_person(person)
    if person.get("kind") == "student" and "PWS" in derive_person_entities(person):
        tuition = int(person.get("monthly_fee_override") or 0) or rates.get("monthly", 0)
        transport = int(person.get("transport_fee_monthly") or 0)
        hostel = 0
        if _pws_category(person) == "Hostel":
            hostel = int(person.get("hostel_fee_override") or 0) or rates.get("hostel_monthly", 0)
        return {"monthly": tuition, "transport": transport, "hostel": hostel}
    monthly = int(person.get("monthly_fee_override") or 0) or rates.get("monthly", 0)
    if not monthly:
        category = _canonical_category(person.get("player_type") or "Daily")
        if category in ("Hostel", "Hostel Only"):
            monthly = int(person.get("hostel_fee_override") or 0) or rates.get("monthly", 0)
    transport = int(person.get("transport_fee_monthly") or 0)
    return {"monthly": monthly, "transport": transport, "hostel": 0}


def _canonical_category(category: str) -> str:
    """Map deprecated "Hostel" to "Hostel Only" for display consistency."""
    if category == "Hostel":
        return "Hostel Only"
    return category


def get_fee_rates(category: str, sport: str) -> dict:
    cat = RATE_CARDS.get(category)
    if not cat:
        return {}
    return cat.get(sport, {})


def first_month_amount(monthly: int, admission_iso: str) -> int:
    try:
        d = datetime.fromisoformat(admission_iso).day
    except Exception:
        d = 1
    return monthly if d <= 15 else int(monthly / 2)


def _month_key(date_iso: str) -> str:
    return date_iso[:7]  # "YYYY-MM"


def _fy_end(month_key: str) -> str:
    """Financial-year end month (Indian FY Apr–Mar) for a given YYYY-MM."""
    y, m = int(month_key[:4]), int(month_key[5:7])
    return f"{y + 1 if m >= 4 else y}-03"


def _next_month(month_key: str) -> str:
    y, m = int(month_key[:4]), int(month_key[5:7])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def _alpha_monthly_amounts(player: dict) -> tuple:
    """(monthly_amount, transport_amount) honouring per-player overrides."""
    sport = player.get("sport") or ""
    category = _canonical_category(player.get("player_type") or "Daily")
    rates = get_fee_rates(category, sport)
    if not rates:
        return 0, 0
    override = int(player.get("monthly_fee_override") or 0) or 0
    if not override and category in ("Hostel", "Hostel Only"):
        override = int(player.get("hostel_fee_override") or 0) or 0
    return (override or rates["monthly"], int(player.get("transport_fee_monthly") or 0))


def _recurring_amounts(person: dict) -> dict:
    """Sync fallback — hardcoded rate cards only (used where async unavailable)."""
    if person.get("kind") == "student" and person.get("organization") == "PWS":
        cat = _pws_category(person)
        rates = get_pws_fee_rates(cat)
        tuition = int(person.get("monthly_fee_override") or 0) or rates.get("monthly", 0)
        transport = int(person.get("transport_fee_monthly") or 0)
        hostel = 0
        if cat == "Hostel":
            hostel = int(person.get("hostel_fee_override") or 0) or rates.get("hostel_monthly", 0)
        return {"monthly": tuition, "transport": transport, "hostel": hostel}
    monthly, transport = _alpha_monthly_amounts(person)
    return {"monthly": monthly, "transport": transport, "hostel": 0}


def _monthly_amounts(player: dict) -> tuple:
    rec = _recurring_amounts(player)
    return rec["monthly"], rec["transport"]


async def auto_create_fees_for_player(player: dict) -> List[dict]:
    """Create Registration + first Monthly fee + (optional) first Transport fee.
    Idempotent on (player_id, fee_type, period_month).

    Rate-card driven based on player_type. Super Admin can override defaults via
    `monthly_fee_override` / `registration_fee_override` / `hostel_fee_override` on the Person record."""
    if player.get("kind") != "player" or player.get("organization") != "ALPHA":
        return []
    sport = player.get("sport") or ""
    category = _canonical_category(player.get("player_type") or "Daily")
    rates = await _rates_for_person(player)
    if not rates:
        return []
    admission = player.get("date_of_admission") or now_utc().strftime("%Y-%m-%d")
    period = _month_key(admission)
    created: List[dict] = []
    # Registration (one-time) — with optional Super Admin override
    reg_amt = int(player.get("registration_fee_override") or 0) or rates["registration"]
    existing_reg = await db.fees.find_one({"player_id": player["id"], "fee_type": "Registration"})
    if not existing_reg:
        reg = _build_fee(player, "Registration", reg_amt, reg_amt, period, admission)
        await db.fees.insert_one(reg)
        created.append(reg)
    # First monthly — priority: monthly_fee_override > hostel_fee_override (legacy for Hostel/Hostel Only) > rate-card
    override = int(player.get("monthly_fee_override") or 0) or 0
    if not override and category in ("Hostel", "Hostel Only"):
        override = int(player.get("hostel_fee_override") or 0) or 0
    monthly_amt = override or rates["monthly"]
    first_amt = first_month_amount(monthly_amt, admission)
    existing_m = await db.fees.find_one({"player_id": player["id"], "fee_type": "Monthly", "period_month": period})
    if not existing_m:
        mfee = _build_fee(player, "Monthly", monthly_amt, first_amt, period, admission, extra={
            "is_first_month": True,
            "first_month_discounted": first_amt < monthly_amt,
        })
        await db.fees.insert_one(mfee)
        created.append(mfee)
    # Transport (optional, recurring)
    tport = int(player.get("transport_fee_monthly") or 0)
    if tport > 0:
        existing_t = await db.fees.find_one({"player_id": player["id"], "fee_type": "Transport", "period_month": period})
        if not existing_t:
            tfee = _build_fee(player, "Transport", tport, first_month_amount(tport, admission), period, admission, extra={
                "is_first_month": True,
            })
            await db.fees.insert_one(tfee)
            created.append(tfee)
    # Notify super admin
    if created:
        await notify_role(
            "super_admin",
            ntype="fees_created",
            title="New player fees created",
            message=f"{player['name']} ({player.get('centre')}/{sport}/{category}) — {len(created)} fee(s) auto-generated",
            entity_id="alpha",
        )
    return created


async def auto_create_fees_for_student(student: dict) -> List[dict]:
    """Create PWS school fees on student enrollment."""
    if student.get("kind") != "student" or "PWS" not in derive_person_entities(student):
        return []
    if student.get("pws_class"):
        try:
            from routers.pws_fees import sync_pws_fees_for_student
            return await sync_pws_fees_for_student(student)
        except Exception:
            pass
    category = _pws_category(student)
    rates = await _rates_for_person(student)
    if not rates:
        return []
    admission = student.get("date_of_admission") or now_utc().strftime("%Y-%m-%d")
    period = _month_key(admission)
    created: List[dict] = []
    reg_amt = int(student.get("registration_fee_override") or 0) or rates["registration"]
    if not await db.fees.find_one({"player_id": student["id"], "fee_type": "Registration"}):
        reg = _build_fee(student, "Registration", reg_amt, reg_amt, period, admission)
        await db.fees.insert_one(reg)
        created.append(reg)
    exam_amt = rates.get("exam", 0)
    if exam_amt and not await db.fees.find_one({"player_id": student["id"], "fee_type": "Exam"}):
        exam = _build_fee(student, "Exam", exam_amt, exam_amt, period, admission)
        await db.fees.insert_one(exam)
        created.append(exam)
    tuition = int(student.get("monthly_fee_override") or 0) or rates["monthly"]
    first_tuition = first_month_amount(tuition, admission)
    if tuition and not await db.fees.find_one({"player_id": student["id"], "fee_type": "Monthly", "period_month": period}):
        tfee = _build_fee(student, "Monthly", tuition, first_tuition, period, admission, extra={
            "is_first_month": True,
            "first_month_discounted": first_tuition < tuition,
        })
        await db.fees.insert_one(tfee)
        created.append(tfee)
    if category == "Hostel":
        hostel_amt = int(student.get("hostel_fee_override") or 0) or rates.get("hostel_monthly", 0)
        if hostel_amt and not await db.fees.find_one({"player_id": student["id"], "fee_type": "Hostel", "period_month": period}):
            hfee = _build_fee(student, "Hostel", hostel_amt, first_month_amount(hostel_amt, admission), period, admission, extra={
                "is_first_month": True,
            })
            await db.fees.insert_one(hfee)
            created.append(hfee)
    tport = int(student.get("transport_fee_monthly") or 0)
    if tport > 0 and not await db.fees.find_one({"player_id": student["id"], "fee_type": "Transport", "period_month": period}):
        tr = _build_fee(student, "Transport", tport, first_month_amount(tport, admission), period, admission, extra={
            "is_first_month": True,
        })
        await db.fees.insert_one(tr)
        created.append(tr)
    if created:
        await notify_role(
            "super_admin",
            ntype="fees_created",
            title="New student fees created",
            message=f"{student['name']} ({category}/{student.get('group') or 'PWS'}) — {len(created)} fee(s) auto-generated",
            entity_id="pws",
        )
    return created


def _build_fee(player: dict, fee_type: str, amount: int, amount_due: int, period: str, admission: str, extra: dict | None = None) -> dict:
    entity = _fee_entity(player)
    if player.get("kind") == "student":
        category = _pws_category(player)
        centre = player.get("group")
        sport = None
    else:
        category = player.get("player_type") or "Daily"
        centre = player.get("centre")
        sport = player.get("sport")
    rec = {
        "id": str(uuid.uuid4()),
        "player_id": player["id"],
        "student_id": player["id"] if player.get("kind") == "student" else None,
        "entity_id": entity,
        "player_name": player["name"],
        "centre": centre,
        "sport": sport,
        "category": category,
        "fee_type": fee_type,
        "amount": amount,
        "amount_due": amount_due,
        "period_month": period,
        "due_date": admission if fee_type in ("Registration", "Exam") else f"{period}-05",
        "status": "due",
        "payment_mode": None,
        "reference_id": None,
        "paid_at": None,
        "created_at": now_utc().isoformat(),
    }
    if extra:
        rec.update(extra)
    return rec


def _iter_months(start: str, end: str):
    """Yield 'YYYY-MM' strings inclusive from start to end (e.g., '2026-01' to '2026-05')."""
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m > 12:
            m = 1; y += 1


async def ensure_monthly_fees_up_to_current(player_id: str) -> List[dict]:
    """Lazily back-fill monthly recurring fees up to the current month."""
    player = await db.people.find_one({"id": player_id})
    if not player:
        return []
    if player.get("kind") == "student" and player.get("organization") == "PWS":
        return await _ensure_pws_recurring_fees(player)
    if player.get("organization") != "ALPHA":
        return []
    sport = player.get("sport") or ""
    category = _canonical_category(player.get("player_type") or "Daily")
    rates = await _rates_for_person(player)
    if not rates:
        return []
    admission = player.get("date_of_admission") or now_utc().strftime("%Y-%m-%d")
    start_month = _month_key(admission)
    current_month = now_utc().strftime("%Y-%m")
    created: List[dict] = []
    amounts = await _recurring_amounts_async(player)
    monthly_amt = amounts["monthly"]
    tport = amounts["transport"]
    for period in _iter_months(start_month, current_month):
        if period == start_month:
            continue
        existing = await db.fees.find_one({"player_id": player_id, "fee_type": "Monthly", "period_month": period})
        if not existing and monthly_amt > 0:
            doc = _build_fee(player, "Monthly", monthly_amt, monthly_amt, period, f"{period}-05")
            await db.fees.insert_one(doc)
            created.append(doc)
        if tport > 0:
            existing_t = await db.fees.find_one({"player_id": player_id, "fee_type": "Transport", "period_month": period})
            if not existing_t:
                tdoc = _build_fee(player, "Transport", tport, tport, period, f"{period}-05")
                await db.fees.insert_one(tdoc)
                created.append(tdoc)
    return created


async def _ensure_pws_recurring_fees(student: dict) -> List[dict]:
    if student.get("pws_class"):
        try:
            from routers.pws_fees import sync_pws_fees_for_student
            return await sync_pws_fees_for_student(student)
        except Exception:
            pass
    admission = student.get("date_of_admission") or now_utc().strftime("%Y-%m-%d")
    start_month = _month_key(admission)
    current_month = now_utc().strftime("%Y-%m")
    created: List[dict] = []
    amounts = await _recurring_amounts_async(student)
    for period in _iter_months(start_month, current_month):
        if period == start_month:
            continue
        if amounts["monthly"] > 0 and not await db.fees.find_one({"player_id": student["id"], "fee_type": "Monthly", "period_month": period}):
            doc = _build_fee(student, "Monthly", amounts["monthly"], amounts["monthly"], period, f"{period}-05")
            await db.fees.insert_one(doc)
            created.append(doc)
        if amounts["hostel"] > 0 and not await db.fees.find_one({"player_id": student["id"], "fee_type": "Hostel", "period_month": period}):
            hdoc = _build_fee(student, "Hostel", amounts["hostel"], amounts["hostel"], period, f"{period}-05")
            await db.fees.insert_one(hdoc)
            created.append(hdoc)
        if amounts["transport"] > 0 and not await db.fees.find_one({"player_id": student["id"], "fee_type": "Transport", "period_month": period}):
            tdoc = _build_fee(student, "Transport", amounts["transport"], amounts["transport"], period, f"{period}-05")
            await db.fees.insert_one(tdoc)
            created.append(tdoc)
    return created


_bulk_ensure_state = {"ts": 0.0}

async def ensure_all_players_monthly_fees(force: bool = False) -> int:
    """Materialize recurring monthly fees for ALL active fee-bearing people up to current month."""
    import time
    now = time.time()
    if not force and now - _bulk_ensure_state["ts"] < 900:
        return 0
    _bulk_ensure_state["ts"] = now
    count = 0
    async for p in db.people.find({"kind": "player", "organization": "ALPHA", "status": {"$ne": "deactivated"}}, {"_id": 0, "id": 1}):
        created = await ensure_monthly_fees_up_to_current(p["id"])
        count += len(created)
    async for s in db.people.find({"kind": "student", "organization": "PWS", "status": {"$ne": "deactivated"}}, {"_id": 0, "id": 1}):
        created = await ensure_monthly_fees_up_to_current(s["id"])
        count += len(created)
    return count


# ------------------ Endpoints ------------------
def _require_view_fees(user: dict):
    if not get_perm(user, "view_fees"):
        raise HTTPException(403, "view_fees permission required")



async def _collection_people_query(
    user: dict,
    institution: Optional[str],
    centre: Optional[str],
    sport: Optional[str],
    group: Optional[str],
    search: Optional[str],
) -> dict:
    inst = (institution or resolve_user_institution(user, None) or "ALPHA").upper()
    if inst not in ("PWS", "ALPHA"):
        inst = "ALPHA"
    query: dict = {"status": {"$ne": "deactivated"}}
    if inst == "PWS":
        query["kind"] = "student"
        query.update(person_entity_filter("PWS"))
        section = group or centre
        if section:
            query["group"] = section
    else:
        query["kind"] = "player"
        query.update(person_entity_filter("ALPHA"))
        if centre:
            query["centre"] = centre
        if sport:
            query["sport"] = sport
    if user.get("role") == "coach":
        from routers.people import _coach_visibility_filter
        coach_q = _coach_visibility_filter(user)
        query = {"$and": [query, coach_q]}
    if is_sports_admin(user) and query.get("kind") == "student":
        return {"kind": "__none__"}
    if search and search.strip():
        from routers.people import _search_filter
        query.update(_search_filter(search.strip()))
    return query


def _fee_match_for_institution(inst: str, player_ids: List[str]) -> dict:
    if not player_ids:
        return {"player_id": "__none__"}
    base = {"player_id": {"$in": player_ids}}
    if inst == "PWS":
        return {**base, "entity_id": "pws"}
    return {**base, "$or": [{"entity_id": "alpha"}, {"entity_id": {"$exists": False}}]}


def _sort_collection_players(players: List[dict], sort: str) -> List[dict]:
    if sort == "name":
        return sorted(players, key=lambda p: (p.get("name") or "").lower())
    if sort == "overdue_days":
        return sorted(players, key=lambda p: p.get("overdue_days") or 0, reverse=True)
    return sorted(players, key=lambda p: p.get("amount_due") or 0, reverse=True)


@router.get("/summary")
async def fees_collection_summary(
    institution: Optional[Literal["PWS", "ALPHA"]] = None,
    centre: Optional[str] = None,
    sport: Optional[str] = None,
    group: Optional[str] = None,
    status: Optional[Literal["all", "overdue", "due_this_month", "paid_ahead"]] = "all",
    sort: Optional[Literal["amount_due", "name", "overdue_days"]] = "amount_due",
    search: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """KPI strip + enriched player rows for the Collect Fees screen."""
    _require_view_fees(user)
    await ensure_all_players_monthly_fees()
    today = now_utc().strftime("%Y-%m-%d")
    current_month = today[:7]
    inst = (institution or resolve_user_institution(user, None) or "ALPHA").upper()
    if inst not in ("PWS", "ALPHA"):
        inst = "ALPHA"

    pq = await _collection_people_query(user, institution, centre, sport, group, search)
    if pq.get("kind") == "__none__":
        empty = {
            "total_players": 0,
            "amount_due_today": 0,
            "overdue_count": 0,
            "collected_this_month": 0,
        }
        return {
            "institution": inst,
            "kpis": empty,
            "players": [],
            "filtered_count": 0,
            "total_due": 0,
            "current_month": current_month,
        }

    people = await db.people.find(pq, {"_id": 0}).sort("name", 1).to_list(2000)
    player_ids = [p["id"] for p in people]
    fee_match = _fee_match_for_institution(inst, player_ids)
    all_fees = await db.fees.find(fee_match, {"_id": 0}).to_list(20000)
    by_player: dict = defaultdict(lambda: {"unpaid": [], "paid": []})
    for f in all_fees:
        bucket = "paid" if f.get("status") == "paid" else "unpaid"
        by_player[f["player_id"]][bucket].append(f)

    collected_agg = await db.fees.aggregate([
        {"$match": {**fee_match, "status": "paid", "paid_at": {"$regex": f"^{current_month}"}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount_due"}}},
    ]).to_list(1)
    collected_this_month = int(collected_agg[0]["total"]) if collected_agg else 0

    enriched: List[dict] = []
    for person in people:
        pid = person["id"]
        buckets = by_player.get(pid, {"unpaid": [], "paid": []})
        snap = compute_player_fee_status(buckets["unpaid"], buckets["paid"], today, current_month)
        enriched.append({
            "id": pid,
            "name": person.get("name"),
            "mobile": person.get("mobile"),
            "centre": person.get("centre") or person.get("group"),
            "sport": person.get("sport"),
            "player_type": person.get("player_type") or person.get("pws_student_type"),
            "group": person.get("group"),
            "pws_class": person.get("pws_class"),
            "is_resident": person.get("is_resident"),
            "organization": person.get("organization"),
            **snap,
        })

    kpis = {
        "total_players": len(enriched),
        "amount_due_today": sum(p["amount_due_today"] for p in enriched),
        "overdue_count": sum(1 for p in enriched if p["fee_status"] == "overdue"),
        "collected_this_month": collected_this_month,
    }

    filtered = enriched
    if status and status != "all":
        if status == "overdue":
            filtered = [p for p in enriched if p["fee_status"] == "overdue"]
        elif status == "due_this_month":
            filtered = [p for p in enriched if p["has_current_month_due"]]
        elif status == "paid_ahead":
            filtered = [p for p in enriched if p["fee_status"] == "paid_ahead"]

    total_due = sum(p["amount_due"] for p in filtered)
    sorted_players = _sort_collection_players(filtered, sort or "amount_due")

    return {
        "institution": inst,
        "kpis": kpis,
        "players": sorted_players,
        "filtered_count": len(sorted_players),
        "total_due": total_due,
        "current_month": current_month,
    }


class RemindIn(BaseModel):
    player_ids: List[str]
    channel: Literal["whatsapp", "sms"] = "whatsapp"


@router.post("/remind")
async def send_fee_reminders(payload: RemindIn, user: dict = Depends(get_current_user)):
    """Prepare fee reminders for overdue players (WhatsApp deep links / SMS text)."""
    if not get_perm(user, "collect_fees") and not get_perm(user, "view_fees"):
        raise HTTPException(403, "collect_fees or view_fees permission required")
    if not payload.player_ids:
        raise HTTPException(400, "Select at least one player")
    today = now_utc().strftime("%Y-%m-%d")
    current_month = today[:7]
    reminders = []
    for pid in payload.player_ids:
        person = await db.people.find_one({"id": pid}, {"_id": 0})
        if not person:
            continue
        fees = await db.fees.find({"player_id": pid, "status": {"$ne": "paid"}}, {"_id": 0}).to_list(200)
        paid = await db.fees.find({"player_id": pid, "status": "paid"}, {"_id": 0}).to_list(200)
        snap = compute_player_fee_status(fees, paid, today, current_month)
        if snap["fee_status"] != "overdue":
            continue
        mobile = (person.get("mobile") or person.get("guardian_phone") or "").strip()
        digits = "".join(c for c in mobile if c.isdigit())
        if digits.startswith("91") and len(digits) > 10:
            wa_phone = digits
        elif len(digits) == 10:
            wa_phone = f"91{digits}"
        else:
            wa_phone = digits or None
        msg = (
            f"Dear Parent/Guardian, fee reminder for {person.get('name')}: "
            f"₹{snap['amount_due']:,} outstanding ({snap['badge']}). "
            f"Please contact the office to settle dues. — PWS & ALPHA"
        )
        import urllib.parse
        link = f"https://wa.me/{wa_phone}?text={urllib.parse.quote(msg)}" if wa_phone else None
        reminders.append({
            "player_id": pid,
            "name": person.get("name"),
            "mobile": mobile or None,
            "amount_due": snap["amount_due"],
            "overdue_days": snap["overdue_days"],
            "message": msg,
            "whatsapp_url": link,
        })
    if not reminders:
        raise HTTPException(400, "None of the selected players are overdue")
    return {"count": len(reminders), "reminders": reminders}


@router.get("/rate-card")
async def get_rate_card(entity: Optional[Literal["alpha", "pws"]] = None, user: dict = Depends(get_current_user)):
    _require_view_fees(user)
    plan_count = await db.fee_plans.count_documents({"active": True})
    payload = {"catalogue_enabled": plan_count > 0}
    if entity == "pws":
        return {**payload, "rates": PWS_RATE_CARDS}
    if entity == "alpha":
        return {**payload, "rates": RATE_CARDS}
    return {**payload, "alpha": RATE_CARDS, "pws": PWS_RATE_CARDS}


@router.get("")
async def list_fees(
    player_id: Optional[str] = None,
    entity_id: Optional[Literal["alpha", "pws"]] = None,
    centre: Optional[str] = None,
    sport: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    period_month: Optional[str] = None,
    fee_type: Optional[str] = None,
    institution: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    _require_view_fees(user)
    q: dict = {}
    if player_id: q["player_id"] = player_id
    if entity_id:
        q["entity_id"] = entity_id
    else:
        inst = resolve_user_institution(user, institution)
        q.update(fee_entity_filter(inst))
    if centre: q["centre"] = centre
    if sport: q["sport"] = sport
    if category: q["category"] = category
    if status: q["status"] = status
    if period_month: q["period_month"] = period_month
    if fee_type: q["fee_type"] = fee_type
    return await db.fees.find(q, {"_id": 0}).sort("due_date", -1).to_list(2000)


@router.get("/dashboard")
async def fees_dashboard(
    centre: Optional[str] = None,
    entity_id: Optional[Literal["alpha", "pws"]] = None,
    institution: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    _require_view_fees(user)
    await ensure_all_players_monthly_fees()
    today = now_utc().strftime("%Y-%m-%d")
    this_month = today[:7]
    out = {"date": today, "by_centre": {}, "by_entity": {}}
    inst = resolve_user_institution(user, institution)
    show_alpha = entity_id != "pws" and inst in ("ALPHA", "BOTH")
    show_pws = entity_id != "alpha" and inst in ("PWS", "BOTH")
    if show_alpha:
        centres = [centre] if centre else ["Balua", "Harding Park"]
        for c in centres:
            base = {"centre": c, "$or": [{"entity_id": "alpha"}, {"entity_id": {"$exists": False}}]}
            out["by_centre"][c] = await _aggregate_fee_bucket(base, today, this_month)
    if show_pws:
        pws_base = {"entity_id": "pws"}
        if centre:
            pws_base["centre"] = centre
        out["by_entity"]["pws"] = await _aggregate_fee_bucket(pws_base, today, this_month)
    return out


async def _aggregate_fee_bucket(base: dict, today: str, this_month: str) -> dict:
    collected_today = await db.fees.aggregate([
        {"$match": {**base, "status": "paid", "paid_at": {"$regex": f"^{today}"}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
    ]).to_list(1)
    due_current = await db.fees.aggregate([
        {"$match": {**base, "status": "due", "period_month": this_month}},
        {"$group": {"_id": None, "total": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
    ]).to_list(1)
    due_past = await db.fees.aggregate([
        {"$match": {**base, "status": "due", "period_month": {"$lt": this_month}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount_due"}, "count": {"$sum": 1}}},
    ]).to_list(1)
    with_dues = await db.fees.distinct("player_id", {**base, "status": "due"})
    received_total = await db.fees.aggregate([
        {"$match": {**base, "status": "paid"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount_due"}}},
    ]).to_list(1)
    return {
        "collected_today": (collected_today[0]["total"] if collected_today else 0),
        "collected_today_count": (collected_today[0]["count"] if collected_today else 0),
        "due_current_month": (due_current[0]["total"] if due_current else 0),
        "due_current_count": (due_current[0]["count"] if due_current else 0),
        "due_past": (due_past[0]["total"] if due_past else 0),
        "due_past_count": (due_past[0]["count"] if due_past else 0),
        "people_with_dues": len(with_dues),
        "received_total": (received_total[0]["total"] if received_total else 0),
    }


PaymentMode = Literal["Cash", "Online", "UPI"]


class CollectIn(BaseModel):
    payment_mode: PaymentMode
    reference_id: Optional[str] = None


@router.post("/{fee_id}/collect")
async def collect_fee(fee_id: str, payload: CollectIn, user: dict = Depends(get_current_user)):
    if not get_perm(user, "collect_fees"):
        raise HTTPException(403, "collect_fees permission required")
    fee = await db.fees.find_one({"id": fee_id})
    if not fee:
        raise HTTPException(404, "Fee not found")
    if fee.get("status") == "paid":
        raise HTTPException(400, "Fee already paid")
    if payload.payment_mode in ("Online", "UPI") and not (payload.reference_id or "").strip():
        raise HTTPException(400, "Reference ID required for Online/UPI payments")
    update = {
        "status": "paid",
        "payment_mode": payload.payment_mode,
        "reference_id": payload.reference_id or None,
        "paid_at": now_utc().isoformat(),
        "collected_by_id": user["id"],
        "collected_by_name": user["name"],
    }
    await db.fees.update_one({"id": fee_id}, {"$set": update})
    return await db.fees.find_one({"id": fee_id}, {"_id": 0})


class FeePatch(BaseModel):
    amount_due: Optional[int] = None
    discount: Optional[int] = None  # positive number to subtract
    notes: Optional[str] = None


@router.get("/player-dues/{player_id}")
async def get_player_dues(player_id: str, user: dict = Depends(get_current_user)):
    """Returns a complete dues breakdown for a player or PWS student."""
    _require_view_fees(user)
    player = await db.people.find_one({"id": player_id}, {"_id": 0})
    if not player:
        raise HTTPException(404, "Person not found")
    if player.get("kind") == "player" and player.get("organization") != "ALPHA":
        raise HTTPException(400, "Fee dues are only available for ALPHA players or PWS students")
    if player.get("kind") == "student" and player.get("organization") != "PWS":
        raise HTTPException(400, "Fee dues are only available for PWS students")
    # Back-fill any missing recurring fees
    await ensure_monthly_fees_up_to_current(player_id)
    fees = await db.fees.find({"player_id": player_id}, {"_id": 0}).sort("due_date", 1).to_list(500)
    current_month = now_utc().strftime("%Y-%m")
    fy_start = f"{now_utc().year - (0 if now_utc().month >= 4 else 1)}-04"

    unpaid = [f for f in fees if f.get("status") != "paid"]
    paid = [f for f in fees if f.get("status") == "paid"]

    # Advance (future) months payable up to end of the same financial year
    fy_end = _fy_end(current_month)
    amounts = await _recurring_amounts_async(player)
    monthly_amt, tport = amounts["monthly"], amounts["transport"]
    hostel_amt = amounts["hostel"]
    existing_periods = {(f.get("fee_type"), f.get("period_month")) for f in fees}
    advance: List[dict] = []
    m = _next_month(current_month)
    while m <= fy_end:
        if monthly_amt > 0 and ("Monthly", m) not in existing_periods:
            advance.append({"period_month": m, "fee_type": "Monthly", "amount": monthly_amt})
        if tport > 0 and ("Transport", m) not in existing_periods:
            advance.append({"period_month": m, "fee_type": "Transport", "amount": tport})
        if hostel_amt > 0 and ("Hostel", m) not in existing_periods:
            advance.append({"period_month": m, "fee_type": "Hostel", "amount": hostel_amt})
        m = _next_month(m)

    current_due = sum(f.get("amount_due", 0) for f in unpaid if f.get("period_month") == current_month)
    past_due = sum(f.get("amount_due", 0) for f in unpaid if (f.get("period_month") or "9999-99") < current_month)
    total_outstanding = current_due + past_due
    fy_total = sum(f.get("amount_due", 0) for f in fees if (f.get("period_month") or "0000-00") >= fy_start)
    paid_total = sum(f.get("amount_due", 0) for f in paid)
    return {
        "player": player,
        "summary": {
            "current_month_due": current_due,
            "previous_pending_due": past_due,
            "total_outstanding": total_outstanding,
            "financial_year_total": fy_total,
            "paid_total": paid_total,
        },
        "unpaid": unpaid,
        "paid": paid,
        "advance": advance,
        "financial_year_end": fy_end,
        "current_month": current_month,
    }


class AdvanceSel(BaseModel):
    period_month: str  # YYYY-MM (must be after the current month, within same FY)
    fee_type: Literal["Monthly", "Transport", "Hostel"] = "Monthly"


class MultiCollectIn(BaseModel):
    fee_ids: List[str] = []
    advance: List[AdvanceSel] = []   # future months paid in advance (same FY)
    player_id: Optional[str] = None  # required when only advance months are selected
    payment_mode: PaymentMode
    reference_id: Optional[str] = None
    transaction_date: Optional[str] = None  # YYYY-MM-DD, defaults to today
    notes: Optional[str] = None


@router.post("/collect-multi")
async def collect_multi(payload: MultiCollectIn, user: dict = Depends(get_current_user)):
    """Mark multiple FULL fee invoices as paid in a single transaction batch.

    Enforces:
    - No partial payments (each fee is fully paid as-is — amount cannot be edited here)
    - Online mode requires reference_id
    - All fee_ids must belong to the same player and must be unpaid
    - Advance months must be after the current month and within the same financial year
    - Sports Admin needs `collect_fees` permission; cannot edit amounts here
    """
    if not get_perm(user, "collect_fees"):
        raise HTTPException(403, "collect_fees permission required")
    if not payload.fee_ids and not payload.advance:
        raise HTTPException(400, "Select at least one fee")
    if payload.payment_mode in ("Online", "UPI") and not (payload.reference_id or "").strip():
        raise HTTPException(400, "Reference ID required for Online/UPI payments")
    fees = await db.fees.find({"id": {"$in": payload.fee_ids}}, {"_id": 0}).to_list(100) if payload.fee_ids else []
    if len(fees) != len(payload.fee_ids):
        raise HTTPException(404, "One or more fees not found")
    player_ids = {f["player_id"] for f in fees}
    if payload.player_id:
        player_ids.add(payload.player_id)
    if len(player_ids) != 1:
        raise HTTPException(400, "All selected fees must belong to the same player" if fees else "player_id is required when only advance months are selected")
    player_id = next(iter(player_ids))
    paid_already = [f for f in fees if f.get("status") == "paid"]
    if paid_already:
        raise HTTPException(400, "Some fees are already paid")

    player = await db.people.find_one({"id": player_id}, {"_id": 0})
    if not player:
        raise HTTPException(404, "Person not found")

    # ---- Advance (future) months: validate & materialize fee rows ----
    advance_ids: List[str] = []
    if payload.advance:
        current_month = now_utc().strftime("%Y-%m")
        fy_end = _fy_end(current_month)
        monthly_amt, tport = _monthly_amounts(player)
        amounts = await _recurring_amounts_async(player)
        seen = set()
        for sel in payload.advance:
            pm = (sel.period_month or "").strip()
            if len(pm) != 7 or pm[4] != "-":
                raise HTTPException(400, f"Invalid period {pm} — use YYYY-MM")
            if pm <= current_month:
                raise HTTPException(400, f"{pm} is not a future month — select it from outstanding dues instead")
            if pm > fy_end:
                raise HTTPException(400, f"{pm} is beyond the current financial year (ends {fy_end})")
            if (sel.fee_type, pm) in seen:
                raise HTTPException(400, f"Duplicate advance selection {sel.fee_type} {pm}")
            seen.add((sel.fee_type, pm))
            if await db.fees.find_one({"player_id": player_id, "fee_type": sel.fee_type, "period_month": pm}):
                raise HTTPException(400, f"{sel.fee_type} fee for {pm} already exists/paid")
            amt = amounts.get("monthly", 0) if sel.fee_type == "Monthly" else amounts.get("transport", 0) if sel.fee_type == "Transport" else amounts.get("hostel", 0)
            if amt <= 0:
                raise HTTPException(400, f"No {sel.fee_type} fee configured for this person")
            doc = _build_fee(player, sel.fee_type, amt, amt, pm, f"{pm}-05", extra={"advance_payment": True})
            await db.fees.insert_one(doc)
            advance_ids.append(doc["id"])

    all_ids = list(payload.fee_ids) + advance_ids
    batch_id = str(uuid.uuid4())
    paid_at = now_utc().isoformat()
    txn_date = (payload.transaction_date or now_utc().strftime("%Y-%m-%d"))

    update = {
        "status": "paid",
        "payment_mode": payload.payment_mode,
        "reference_id": payload.reference_id or None,
        "transaction_date": txn_date,
        "paid_at": paid_at,
        "collected_by_id": user["id"],
        "collected_by_name": user["name"],
        "batch_id": batch_id,
        "notes": payload.notes or None,
    }
    await db.fees.update_many({"id": {"$in": all_ids}}, {"$set": update})

    # Reload the fees with the update applied so we can render the receipt
    fees_after = await db.fees.find({"id": {"$in": all_ids}}, {"_id": 0}).sort("period_month", 1).to_list(100)
    total_amount = sum(f.get("amount_due", 0) for f in fees_after)
    receipt = {
        "batch_id": batch_id,
        "paid_at": paid_at,
        "transaction_date": txn_date,
        "player": {
            "id": player.get("id"),
            "name": player.get("name"),
            "centre": player.get("centre") or player.get("group"),
            "sport": player.get("sport"),
            "category": player.get("player_type") or _pws_category(player) if player.get("kind") == "student" else player.get("player_type"),
            "kind": player.get("kind"),
            "organization": player.get("organization"),
        },
        "fees": fees_after,
        "total_amount": total_amount,
        "payment_mode": payload.payment_mode,
        "reference_id": payload.reference_id,
        "notes": payload.notes,
        "collected_by": {"id": user["id"], "name": user["name"], "role": user["role"]},
    }
    return receipt


@router.post("/payments")
async def collect_payments(payload: MultiCollectIn, user: dict = Depends(get_current_user)):
    """Alias for collect-multi — used by the inline collection drawer."""
    return await collect_multi(payload, user)


# ------------------ Ad-Hoc / Manual Fee Creation (Super Admin only) ------------------
ADHOC_FEE_TYPES = ["Uniform", "Kit", "Tournament", "Books", "Event", "Other"]


class AdHocFeeIn(BaseModel):
    player_id: str
    fee_type: Literal["Uniform", "Kit", "Tournament", "Books", "Event", "Other"]
    amount: int
    due_date: str  # YYYY-MM-DD
    notes: Optional[str] = None


@router.get("/adhoc-types")
async def list_adhoc_types(user: dict = Depends(get_current_user)):
    _require_view_fees(user)
    return {"types": ADHOC_FEE_TYPES}


@router.post("")
async def create_adhoc_fee(payload: AdHocFeeIn, user: dict = Depends(get_current_user)):
    """Create an ad-hoc/manual fee invoice for a player (Uniform, Kit, Tournament, etc.).

    SUPER ADMIN ONLY. Used for one-off charges not covered by the rate card.
    Audit-logged with created_by_id / created_by_name / created_at.
    """
    if user.get("role") != "super_admin":
        raise HTTPException(403, "Super Admin only — ad-hoc fees can only be created by Super Admin.")
    if payload.fee_type not in ADHOC_FEE_TYPES:
        raise HTTPException(400, f"Invalid fee_type. Allowed: {ADHOC_FEE_TYPES}")
    if payload.amount <= 0:
        raise HTTPException(400, "Amount must be greater than 0")
    if not payload.due_date or len(payload.due_date) < 10:
        raise HTTPException(400, "Valid due date required (YYYY-MM-DD)")
    try:
        datetime.fromisoformat(payload.due_date)
    except Exception:
        raise HTTPException(400, "Invalid due_date format. Use YYYY-MM-DD")
    person = await db.people.find_one({"id": payload.player_id})
    if not person:
        raise HTTPException(404, "Person not found")
    if person.get("kind") == "player" and person.get("organization") != "ALPHA":
        raise HTTPException(400, "Ad-hoc fees can only be created for ALPHA players or PWS students")
    if person.get("kind") == "student" and person.get("organization") != "PWS":
        raise HTTPException(400, "Ad-hoc fees can only be created for PWS students")
    if person.get("kind") not in ("player", "student"):
        raise HTTPException(400, "Ad-hoc fees can only be created for players or students")
    player = person
    period = payload.due_date[:7]
    rec = _build_fee(player, payload.fee_type, payload.amount, payload.amount, period, payload.due_date, extra={
        "notes": payload.notes or None,
        "is_adhoc": True,
        "created_by_id": user["id"],
        "created_by_name": user["name"],
    })
    await db.fees.insert_one(rec)
    # Notify super admin audit feed
    await notify_role(
        "super_admin",
        ntype="fee_adhoc_created",
        title=f"Ad-hoc fee: {payload.fee_type}",
        message=f"{player['name']} · ₹{payload.amount:,} · due {payload.due_date} · by {user['name']}",
        entity_id=rec.get("entity_id"),
    )
    return await db.fees.find_one({"id": rec["id"]}, {"_id": 0})


# ------------------ Receipt PDF (shareable) ------------------
@router.get("/receipt/{batch_id}/pdf")
async def receipt_pdf(batch_id: str):
    """Generate a PDF receipt for a payment batch.

    PUBLIC endpoint — the batch_id is an unguessable UUID that acts as the
    capability token, so parents can open a shared receipt link without login.
    """
    fees = await db.fees.find({"batch_id": batch_id, "status": "paid"}, {"_id": 0}).sort("period_month", 1).to_list(100)
    if not fees:
        raise HTTPException(404, "Receipt not found")
    player = await db.people.find_one({"id": fees[0]["player_id"]}, {"_id": 0}) or {}
    total = sum(f.get("amount_due", 0) for f in fees)
    f0 = fees[0]
    is_pws = fees[0].get("entity_id") == "pws" or player.get("kind") == "student"
    org_title = "Prarambhika World School" if is_pws else "ALPHA Sports Academy"
    person_label = "Student" if is_pws else "Player"

    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas
    from fastapi.responses import Response

    buf = BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    W, H = A4

    def rs(n):
        return f"Rs. {n:,}"

    # Header band
    c.setFillColorRGB(0.06, 0.09, 0.16)  # slate-900
    c.rect(0, H - 38 * mm, W, 38 * mm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(20 * mm, H - 18 * mm, org_title)
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, H - 25 * mm, "Payment Receipt")
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(W - 20 * mm, H - 18 * mm, f"Receipt #{batch_id[:8].upper()}")
    c.setFont("Helvetica", 9)
    c.drawRightString(W - 20 * mm, H - 25 * mm, f"Transaction date: {format_date_display(f0.get('transaction_date'))}")

    y = H - 50 * mm
    # Player block
    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, f"{person_label} Details")
    y -= 7 * mm
    c.setFont("Helvetica", 10)
    if is_pws:
        lines = [
            ("Name", player.get("name") or f0.get("player_name") or "-"),
            ("Class / Section", player.get("group") or f0.get("centre") or "-"),
            ("Category", _pws_category(player) if player.get("kind") == "student" else f0.get("category") or "-"),
        ]
    else:
        lines = [
            ("Name", player.get("name") or f0.get("player_name") or "-"),
            ("Centre / Sport", f"{player.get('centre') or '-'}  ·  {player.get('sport') or '-'}"),
            ("Category", _canonical_category(player.get("player_type") or f0.get("category") or "-")),
        ]
    for label, val in lines:
        c.setFillColorRGB(0.39, 0.45, 0.55)
        c.drawString(20 * mm, y, label)
        c.setFillColorRGB(0.06, 0.09, 0.16)
        c.drawString(60 * mm, y, str(val))
        y -= 6 * mm

    y -= 4 * mm
    # Fee table header
    c.setFillColorRGB(0.95, 0.96, 0.98)
    c.rect(20 * mm, y - 2 * mm, W - 40 * mm, 8 * mm, fill=1, stroke=0)
    c.setFillColorRGB(0.28, 0.33, 0.41)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(23 * mm, y, "FEE")
    c.drawString(90 * mm, y, "PERIOD")
    c.drawRightString(W - 23 * mm, y, "AMOUNT")
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    for f in fees:
        c.setFillColorRGB(0.06, 0.09, 0.16)
        c.drawString(23 * mm, y, str(f.get("fee_type") or "-"))
        c.drawString(90 * mm, y, format_month_display(f.get("period_month")))
        c.drawRightString(W - 23 * mm, y, rs(f.get("amount_due", 0)))
        y -= 6.5 * mm
        if y < 60 * mm:
            c.showPage()
            y = H - 30 * mm
            c.setFont("Helvetica", 10)
    # Total
    y -= 2 * mm
    c.setStrokeColorRGB(0.89, 0.91, 0.94)
    c.line(20 * mm, y + 4 * mm, W - 20 * mm, y + 4 * mm)
    c.setFont("Helvetica-Bold", 12)
    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.drawString(23 * mm, y - 2 * mm, "Total Paid")
    c.setFillColorRGB(0.02, 0.53, 0.32)
    c.drawRightString(W - 23 * mm, y - 2 * mm, rs(total))
    y -= 14 * mm

    # Payment details
    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, "Payment Details")
    y -= 7 * mm
    c.setFont("Helvetica", 10)
    paid_at = f0.get("paid_at") or ""
    details = [
        ("Payment mode", f0.get("payment_mode") or "-"),
        ("Reference / Txn ID", f0.get("reference_id") or "-"),
        ("Collected by", f0.get("collected_by_name") or "-"),
        ("Timestamp", format_datetime_display(paid_at) if paid_at else "—"),
    ]
    for label, val in details:
        c.setFillColorRGB(0.39, 0.45, 0.55)
        c.drawString(20 * mm, y, label)
        c.setFillColorRGB(0.06, 0.09, 0.16)
        c.drawString(60 * mm, y, str(val))
        y -= 6 * mm

    # Footer
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColorRGB(0.58, 0.64, 0.72)
    c.drawCentredString(W / 2, 15 * mm, "This is a computer-generated receipt and does not require a signature.")
    c.drawCentredString(W / 2, 11 * mm, f"ALPHA Sports Academy · Receipt {batch_id[:8].upper()}")
    c.save()

    pdf_bytes = buf.getvalue()
    fname = f"receipt-{(player.get('name') or 'player').replace(' ', '-').lower()}-{batch_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )


class DiscountIn(BaseModel):
    discount_amount: int
    reason: str
    new_amount_due: Optional[int] = None  # optional explicit override


@router.patch("/{fee_id}/discount")
async def apply_discount(fee_id: str, payload: DiscountIn, user: dict = Depends(get_current_user)):
    """Apply a discount to an unpaid fee. Super Admin / approvers only; others submit approval requests."""
    from routers.approvals import _can_approve, _history_entry, _approval_out
    fee = await db.fees.find_one({"id": fee_id})
    if not fee:
        raise HTTPException(404, "Fee not found")
    if fee.get("status") == "paid":
        raise HTTPException(400, "Cannot discount a paid fee")
    if payload.discount_amount < 0:
        raise HTTPException(400, "Discount must be ≥ 0")
    if not payload.reason.strip():
        raise HTTPException(400, "Reason is required")

    if not _can_approve(user):
        if not (get_perm(user, "edit_fees") or get_perm(user, "collect_fees")):
            raise HTTPException(403, "Permission required to request fee concession")
        existing = await db.approval_requests.find_one({
            "type": "fee_concession",
            "subject_id": fee_id,
            "status": "pending",
        })
        if existing:
            raise HTTPException(400, "A pending concession request already exists for this fee")
        entity_id = fee.get("entity_id") or ("pws" if fee.get("organization") == "PWS" else "alpha")
        doc = {
            "id": str(uuid.uuid4()),
            "type": "fee_concession",
            "status": "pending",
            "entity_id": entity_id,
            "subject_id": fee_id,
            "subject_label": f"{fee.get('person_name', 'Fee')} · ₹{payload.discount_amount} off",
            "reason": payload.reason.strip(),
            "payload": {
                "fee_id": fee_id,
                "discount_amount": payload.discount_amount,
                "new_amount_due": payload.new_amount_due,
            },
            "requested_by_id": user["id"],
            "requested_by_name": user["name"],
            "requested_at": now_utc().isoformat(),
            "decided_by_id": None,
            "decided_by_name": None,
            "decided_at": None,
            "decision_note": None,
            "history": [_history_entry("submitted", user, payload.reason.strip())],
            "comments": [],
        }
        await db.approval_requests.insert_one(doc)
        from core import notify_role
        await notify_role(
            "super_admin",
            ntype="approval_request",
            title="Fee concession request",
            message=f"{user['name']} requested ₹{payload.discount_amount} concession",
            ref_id=doc["id"],
        )
        return {"approval_required": True, "approval": _approval_out(doc)}

    new_amt = payload.new_amount_due if payload.new_amount_due is not None else max(0, (fee.get("amount_due") or 0) - payload.discount_amount)
    upd = {
        "amount_due": new_amt,
        "discount_applied": (fee.get("discount_applied") or 0) + payload.discount_amount,
        "discount_reason": payload.reason,
        "discounted_by_id": user["id"],
        "discounted_by_name": user["name"],
        "discounted_at": now_utc().isoformat(),
    }
    await db.fees.update_one({"id": fee_id}, {"$set": upd})
    return await db.fees.find_one({"id": fee_id}, {"_id": 0})
