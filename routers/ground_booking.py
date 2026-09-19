"""ALPHA Ground Booking — calendar, customers, discount approval tasks."""
from __future__ import annotations

import re
import uuid
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core import (
    db,
    get_current_user,
    is_alpha_accounts_user,
    is_alpha_admin_user,
    is_super_admin,
    now_utc,
    today_ist,
)
from pymongo.errors import DuplicateKeyError

from ground_booking import (
    BALL_COLORS,
    BALL_TYPES,
    SPORTS,
    STATUSES,
    booking_days,
    compute_pricing,
)
from notifications_service import send_notification, send_to_role

router = APIRouter(prefix="/ground-bookings", tags=["ground-bookings"])


def _is_operations_admin(user: dict) -> bool:
    designation = (user.get("designation") or "").strip().upper()
    if designation in {"OPERATIONS_ADMIN", "PWS_OFFICE_STAFF", "ALPHA_OFFICE_STAFF"}:
        return True
    return (user.get("permission_set") or "").strip().lower() == "operations_admin"


def _has_ground_perm(user: dict, *keys: str) -> bool:
    perms = user.get("permissions") or {}
    return any(bool(perms.get(k)) for k in keys)


def _role_based_ground_bookings(user: dict) -> bool:
    if _is_operations_admin(user):
        return False
    role = (user.get("role") or "").strip().lower()
    if role in ("admin", "alpha_admin", "alpha_accounts"):
        return True
    return is_alpha_admin_user(user) or is_alpha_accounts_user(user)


def can_access_ground_bookings(user: dict) -> bool:
    if is_super_admin(user):
        return True
    if _has_ground_perm(user, "view_ground_bookings", "manage_ground_bookings"):
        return True
    return _role_based_ground_bookings(user)


def can_manage_ground_bookings(user: dict) -> bool:
    if is_super_admin(user):
        return True
    if _has_ground_perm(user, "manage_ground_bookings"):
        return True
    if _has_ground_perm(user, "view_ground_bookings") and not _role_based_ground_bookings(user):
        return False
    return _role_based_ground_bookings(user)


def _assert_access(user: dict) -> None:
    if not can_access_ground_bookings(user):
        raise HTTPException(403, "Not allowed to access Ground Booking")


def _assert_manage(user: dict) -> None:
    if not can_manage_ground_bookings(user):
        raise HTTPException(403, "Ground Booking is limited to ALPHA Admin, ALPHA Accounts, or users granted the module")


def _public(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k != "_id"}
    end = (out.get("dates") or {}).get("endDate") or ""
    today = today_ist()
    status = out.get("status") or "Tentative"
    out["isPast"] = bool(end and end < today and status != "Cancelled")
    out["calendarTone"] = (
        "past" if out["isPast"] or status == "Cancelled"
        else "confirmed" if status == "Confirmed"
        else "tentative"
    )
    return out


class AddonFood(BaseModel):
    enabled: bool = False
    ratePerPlate: float = 0
    people: Optional[int] = None


class AddonTransport(BaseModel):
    enabled: bool = False
    ratePerPerson: float = 0
    people: Optional[int] = None


class AddonUmpire(BaseModel):
    enabled: bool = False
    ratePerDay: float = 0
    people: Optional[int] = None


class AddonBalls(BaseModel):
    enabled: bool = False
    type: Optional[Literal["Tennis Ball", "Leather Ball"]] = None
    color: Optional[Literal["Red", "White"]] = None
    quantity: int = 0
    ratePerBall: float = 0


class CustomerIn(BaseModel):
    name: str = Field(min_length=1)
    organization: Optional[str] = None
    phone: str = Field(min_length=8, max_length=15)
    address: str = Field(min_length=3)
    sourcePersonId: Optional[str] = None


class BookingCreate(BaseModel):
    sport: Literal["Cricket", "Football"]
    campus: Literal["Balua", "Harding Park", "Defense Colony"]
    customer: CustomerIn
    startDate: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    endDate: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timeSlot: Literal["half_day", "full_day", "custom"]
    customHours: Optional[float] = None
    eventType: Literal["Friendly Match", "Tournament", "Scouting", "Social Event"]
    numberOfPeople: int = Field(ge=1)
    groundRate: Optional[float] = None
    food: AddonFood = Field(default_factory=AddonFood)
    transport: AddonTransport = Field(default_factory=AddonTransport)
    umpire: AddonUmpire = Field(default_factory=AddonUmpire)
    balls: AddonBalls = Field(default_factory=AddonBalls)


class BookingStatusIn(BaseModel):
    status: Literal["Tentative", "Confirmed", "Cancelled"]


ALLOWED_STATUS_FROM = {
    "Tentative": ["Tentative"],
    "Confirmed": ["Tentative", "Confirmed"],
    "Cancelled": ["Tentative", "Confirmed"],
}


def _phone_ok(phone: str) -> bool:
    digits = re.sub(r"\D", "", phone or "")
    return 10 <= len(digits) <= 13


def _pricing_from_payload(payload: BookingCreate) -> dict:
    if payload.timeSlot == "custom" and payload.groundRate is None:
        raise HTTPException(400, "Custom time slot requires a ground/venue rate")
    try:
        return compute_pricing(
            time_slot=payload.timeSlot,
            start_date=payload.startDate,
            end_date=payload.endDate,
            ground_rate=payload.groundRate,
            people=payload.numberOfPeople,
            food_enabled=payload.food.enabled,
            food_rate_per_plate=payload.food.ratePerPlate,
            food_people=payload.food.people,
            transport_enabled=payload.transport.enabled,
            transport_rate_per_person=payload.transport.ratePerPerson,
            transport_people=payload.transport.people,
            umpire_enabled=payload.umpire.enabled,
            umpire_rate_per_day=payload.umpire.ratePerDay,
            umpire_people=payload.umpire.people,
            balls_enabled=payload.balls.enabled,
            ball_qty=payload.balls.quantity,
            ball_rate=payload.balls.ratePerBall,
            custom_list_rate=payload.groundRate if payload.timeSlot == "custom" else None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _validate_payload(payload: BookingCreate) -> None:
    if payload.sport not in SPORTS:
        raise HTTPException(400, "Sport must be Cricket or Football")
    if not _phone_ok(payload.customer.phone):
        raise HTTPException(400, "Enter a valid contact number")
    if payload.balls.enabled:
        if payload.balls.type not in BALL_TYPES:
            raise HTTPException(400, "Select a ball type")
        if payload.balls.color not in BALL_COLORS:
            raise HTTPException(400, "Select a ball color")
        if payload.balls.quantity < 1:
            raise HTTPException(400, "Ball quantity must be at least 1")


def _dump(model) -> dict:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _editor_stamp(user: dict, at: str) -> dict:
    return {
        "user_id": user.get("id"),
        "name": user.get("name"),
        "email": user.get("email"),
        "role": user.get("role"),
        "at": at,
    }


def _core_fields(payload: BookingCreate, pricing: dict) -> dict:
    return {
        "sport": payload.sport,
        "campus": payload.campus,
        "customer": {
            "name": payload.customer.name.strip(),
            "organization": (payload.customer.organization or "").strip() or None,
            "phone": payload.customer.phone.strip(),
            "address": payload.customer.address.strip(),
            "sourcePersonId": payload.customer.sourcePersonId,
        },
        "dates": {
            "startDate": payload.startDate,
            "endDate": payload.endDate,
            "timeSlot": payload.timeSlot,
            "customHours": payload.customHours,
        },
        "eventDetails": {
            "type": payload.eventType,
            "numberOfPeople": payload.numberOfPeople,
        },
        "addOns": {
            "food": _dump(payload.food),
            "transport": _dump(payload.transport),
            "umpire": _dump(payload.umpire),
            "balls": _dump(payload.balls),
        },
        "pricing": {
            "groundRate": pricing["groundRate"],
            "listGroundRate": pricing["listGroundRate"],
            "addOnTotal": pricing["addOnTotal"],
            "totalRevenue": pricing["totalRevenue"],
            "foodCost": pricing["foodCost"],
            "transportCost": pricing["transportCost"],
            "umpireCost": pricing["umpireCost"],
            "ballCost": pricing["ballCost"],
            "discountAmount": pricing["discountAmount"],
            "discountPending": pricing["discountRequested"],
        },
    }


async def _super_admin_ids() -> List[str]:
    rows = await db.users.find(
        {"role": "super_admin", "status": {"$ne": "deactivated"}},
        {"_id": 0, "id": 1},
    ).to_list(20)
    return [r["id"] for r in rows if r.get("id")]


async def _open_discount_task(user: dict, booking: dict, pricing: dict) -> dict:
    assignee_ids = await _super_admin_ids()
    assignee_id = assignee_ids[0] if assignee_ids else None
    title = f"Approve ground booking discount — {booking['customer']['name']}"
    desc = (
        f"{booking['sport']} · {booking['dates']['startDate']} to {booking['dates']['endDate']}\n"
        f"List rate ₹{pricing['listGroundRate']:.0f} → quoted ₹{pricing['groundRate']:.0f} "
        f"(discount ₹{pricing['discountAmount']:.0f})."
    )
    task = {
        "id": str(uuid.uuid4()),
        "title": title,
        "description": desc,
        "entity_id": "alpha",
        "priority": "high",
        "due_date": None,
        "deadline": None,
        "assignee_id": assignee_id,
        "assignee_name": "Super Admin",
        "assignee_role": "super_admin",
        "assignee_ids": assignee_ids,
        "department": "Ground Booking",
        "category": "ground_booking_discount",
        "follow_up_required": True,
        "status": "open",
        "created_by": user["id"],
        "created_by_name": user.get("name"),
        "created_by_role": user.get("role"),
        "created_at": now_utc().isoformat(),
        "updated_at": now_utc().isoformat(),
        "completed_at": None,
        "ref_type": "ground_booking",
        "ref_id": booking["id"],
        "comments": [],
    }
    await db.tasks.insert_one(task)
    approval = {
        "id": str(uuid.uuid4()),
        "type": "ground_booking_discount",
        "status": "pending",
        "entity_id": "alpha",
        "organization": "ALPHA",
        "subject_id": booking["id"],
        "subject_label": f"{booking['customer']['name']} · {booking['sport']}",
        "reason": (
            f"Discount ₹{pricing['discountAmount']:.0f} on ground rate "
            f"(₹{pricing['listGroundRate']:.0f} → ₹{pricing['groundRate']:.0f})"
        ),
        "payload": {
            "booking_id": booking["id"],
            "list_ground_rate": pricing["listGroundRate"],
            "quoted_ground_rate": pricing["groundRate"],
            "discount_amount": pricing["discountAmount"],
            "task_id": task["id"],
            "target_role": "Ground booking",
        },
        "requested_by_id": user["id"],
        "requested_by_name": user.get("name"),
        "requested_at": now_utc().isoformat(),
        "decided_by_id": None,
        "decided_by_name": None,
        "decided_at": None,
        "decision_note": None,
        "history": [{
            "id": str(uuid.uuid4()),
            "action": "submitted",
            "user_id": user["id"],
            "user_name": user.get("name"),
            "note": "Discount requested with ground booking",
            "at": now_utc().isoformat(),
        }],
        "comments": [],
    }
    await db.approval_requests.insert_one(approval)
    await send_to_role(
        "super_admin",
        ntype="approval_requested",
        title="Ground booking discount",
        message=f"{user.get('name')} requested a discount on {booking['customer']['name']}'s booking",
        ref_id=approval["id"],
        ref_type="approval",
        entity_id="alpha",
    )
    for aid in assignee_ids:
        if aid == user["id"]:
            continue
        await send_notification(
            aid,
            ntype="task_assigned",
            title="New task assigned",
            message=title,
            ref_id=task["id"],
            ref_type="task",
            entity_id="alpha",
        )
    return {"task_id": task["id"], "approval_id": approval["id"]}


@router.get("")
async def list_bookings(
    sport: Optional[str] = None,
    status: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    month: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    _assert_access(user)
    q: dict = {"entity": "ALPHA"}
    if sport in SPORTS:
        q["sport"] = sport
    if status in STATUSES:
        q["status"] = status
    if month and re.match(r"^\d{4}-\d{2}$", month):
        y, mth = int(month[:4]), int(month[5:7])
        month_start = f"{month}-01"
        nxt = f"{y + 1}-01-01" if mth == 12 else f"{y}-{mth + 1:02d}-01"
        q["dates.startDate"] = {"$lt": nxt}
        q["dates.endDate"] = {"$gte": month_start}
    elif start or end:
        rng: dict = {}
        if start:
            rng["$gte"] = start
        if end:
            rng["$lte"] = end
        q["dates.endDate"] = rng
    rows = await db.ground_bookings.find(q, {"_id": 0}).sort("dates.startDate", 1).to_list(1000)
    return [_public(r) for r in rows]


def _text_or_phone_clauses(q: str, *, name_key: str, org_keys: list, phone_keys: list) -> list:
    term = (q or "").strip()
    rx = re.escape(term)
    clauses = [{name_key: {"$regex": rx, "$options": "i"}}]
    for key in org_keys:
        clauses.append({key: {"$regex": rx, "$options": "i"}})
    for key in phone_keys:
        clauses.append({key: {"$regex": rx, "$options": "i"}})
    digits = re.sub(r"\D", "", term)
    if len(digits) >= 4:
        d_rx = re.escape(digits)
        for key in phone_keys:
            clauses.append({key: {"$regex": d_rx}})
    return clauses


def _addon_labels(doc: dict) -> list:
    add = doc.get("addOns") or {}
    labels = []
    if (add.get("food") or {}).get("enabled"):
        labels.append("Food")
    if (add.get("transport") or {}).get("enabled"):
        labels.append("Transport")
    if (add.get("umpire") or {}).get("enabled"):
        labels.append("Umpire")
    if (add.get("balls") or {}).get("enabled"):
        labels.append("Balls")
    return labels


def _last_booking_summary(doc: dict) -> dict:
    dates = doc.get("dates") or {}
    event = doc.get("eventDetails") or {}
    pricing = doc.get("pricing") or {}
    customer = doc.get("customer") or {}
    return {
        "id": doc.get("id"),
        "sport": doc.get("sport"),
        "startDate": dates.get("startDate"),
        "endDate": dates.get("endDate"),
        "timeSlot": dates.get("timeSlot"),
        "eventType": event.get("type"),
        "numberOfPeople": event.get("numberOfPeople"),
        "status": doc.get("status"),
        "totalRevenue": pricing.get("totalRevenue"),
        "addOns": _addon_labels(doc),
        "customerName": customer.get("name"),
        "organization": customer.get("organization"),
    }


async def _attach_last_booking(item: dict) -> dict:
    phone = (item.get("phone") or "").strip()
    name = (item.get("name") or "").strip()
    clauses = []
    if phone:
        clauses.append({"customer.phone": phone})
        digits = re.sub(r"\D", "", phone)
        if len(digits) >= 6:
            clauses.append({"customer.phone": {"$regex": re.escape(digits)}})
    if name:
        clauses.append({"customer.name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}})
    if not clauses:
        item["lastBooking"] = None
        return item
    rows = await db.ground_bookings.find({"$or": clauses}, {"_id": 0}).sort("created_at", -1).to_list(1)
    item["lastBooking"] = _last_booking_summary(rows[0]) if rows else None
    return item


@router.get("/customers")
async def search_customers(q: str = Query(..., min_length=2), user: dict = Depends(get_current_user)):
    _assert_manage(user)
    people_filt = {
        "status": {"$ne": "deactivated"},
        "$or": _text_or_phone_clauses(
            q,
            name_key="name",
            org_keys=["organization", "organisation", "club", "centre"],
            phone_keys=["mobile", "phone", "guardian_phone"],
        ),
        "organization": {"$in": ["ALPHA", "BOTH"]},
    }
    people = await db.people.find(people_filt, {"_id": 0}).sort("name", 1).to_list(25)
    seen = set()
    out = []
    for p in people:
        phone = p.get("mobile") or p.get("phone") or p.get("guardian_phone") or ""
        key = ((p.get("name") or "").strip().lower(), re.sub(r"\D", "", phone))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "organization": p.get("organization") or p.get("club") or p.get("centre") or "",
            "phone": phone,
            "address": p.get("address") or "",
            "kind": p.get("kind") or "directory",
        })
    prior = await db.ground_bookings.find(
        {
            "$or": _text_or_phone_clauses(
                q,
                name_key="customer.name",
                org_keys=["customer.organization"],
                phone_keys=["customer.phone"],
            )
        },
        {"_id": 0},
    ).sort("created_at", -1).to_list(40)
    for row in prior:
        c = row.get("customer") or {}
        phone = c.get("phone") or ""
        key = ((c.get("name") or "").strip().lower(), re.sub(r"\D", "", phone))
        if not c.get("name") or key in seen:
            continue
        seen.add(key)
        out.append({
            "id": c.get("sourcePersonId"),
            "name": c.get("name"),
            "organization": c.get("organization") or "",
            "phone": phone,
            "address": c.get("address") or "",
            "kind": "previous",
            "lastBooking": _last_booking_summary(row),
        })
    hydrated = []
    for item in out[:30]:
        if item.get("lastBooking"):
            hydrated.append(item)
        else:
            hydrated.append(await _attach_last_booking(item))
    return hydrated


@router.get("/{booking_id}")
async def get_booking(booking_id: str, user: dict = Depends(get_current_user)):
    _assert_access(user)
    doc = await db.ground_bookings.find_one({"id": booking_id, "entity": "ALPHA"}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Booking not found")
    return _public(doc)


async def _claim_slots(booking_id: str, campus: str, sport: str, start: str, end: str) -> None:
    """Reserve one lock row per occupied day; the unique index is the conflict check."""
    days = booking_days(start, end)
    locks = [
        {"id": str(uuid.uuid4()), "booking_id": booking_id, "campus": campus,
         "sport": sport, "day": day}
        for day in days
    ]
    taken: List[str] = []
    for lock in locks:
        try:
            await db.ground_slot_locks.insert_one(dict(lock))
        except DuplicateKeyError:
            await db.ground_slot_locks.delete_many({"booking_id": booking_id})
            raise HTTPException(
                409,
                f"{sport} at {campus} is already booked on {lock['day']}",
            )
        taken.append(lock["day"])


async def _release_slots(booking_id: str) -> None:
    await db.ground_slot_locks.delete_many({"booking_id": booking_id})


@router.post("")
async def create_booking(payload: BookingCreate, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    _validate_payload(payload)
    pricing = _pricing_from_payload(payload)
    now = now_utc().isoformat()
    editor = _editor_stamp(user, now)
    doc = {
        "id": str(uuid.uuid4()),
        "entity": "ALPHA",
        **_core_fields(payload, pricing),
        "status": "Tentative",
        "created_by": editor["user_id"],
        "created_by_name": editor["name"],
        "created_by_email": editor["email"],
        "created_by_role": editor["role"],
        "created_at": now,
        "updated_by": editor["user_id"],
        "updated_by_name": editor["name"],
        "updated_by_email": editor["email"],
        "updated_by_role": editor["role"],
        "updated_at": now,
        "edit_history": [],
        "discount_task_id": None,
        "discount_approval_id": None,
    }
    await _claim_slots(doc["id"], payload.campus, payload.sport, payload.startDate, payload.endDate)
    try:
        await db.ground_bookings.insert_one(dict(doc))
    except BaseException:
        await _release_slots(doc["id"])
        raise
    if pricing["discountRequested"]:
        refs = await _open_discount_task(user, doc, pricing)
        doc["discount_task_id"] = refs["task_id"]
        doc["discount_approval_id"] = refs["approval_id"]
        await db.ground_bookings.update_one({"id": doc["id"]}, {"$set": {
            "discount_task_id": refs["task_id"],
            "discount_approval_id": refs["approval_id"],
        }})
    out = _public(doc)
    out["discount_submitted"] = bool(pricing["discountRequested"])
    return out


@router.put("/{booking_id}")
async def update_booking(booking_id: str, payload: BookingCreate, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    doc = await db.ground_bookings.find_one({"id": booking_id, "entity": "ALPHA"}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Booking not found")
    if doc.get("status") != "Tentative":
        raise HTTPException(400, "Only tentative bookings can be edited")
    _validate_payload(payload)
    pricing = _pricing_from_payload(payload)
    now = now_utc().isoformat()
    editor = _editor_stamp(user, now)
    fields = _core_fields(payload, pricing)
    history = list(doc.get("edit_history") or [])
    history.append({
        **editor,
        "action": "edited",
        "previous_total": (doc.get("pricing") or {}).get("totalRevenue"),
        "new_total": pricing["totalRevenue"],
    })
    fields["pricing"]["discountPending"] = bool(
        pricing["discountRequested"] or (doc.get("pricing") or {}).get("discountPending")
    )
    if pricing["discountRequested"] and not (doc.get("pricing") or {}).get("discountPending"):
        fields["pricing"]["discountPending"] = True
        refs = await _open_discount_task(user, {**doc, **fields, "id": booking_id}, pricing)
        fields["discount_task_id"] = refs["task_id"]
        fields["discount_approval_id"] = refs["approval_id"]
    elif not pricing["discountRequested"]:
        fields["pricing"]["discountPending"] = False
    await db.ground_bookings.update_one(
        {"id": booking_id},
        {"$set": {
            **fields,
            "updated_by": editor["user_id"],
            "updated_by_name": editor["name"],
            "updated_by_email": editor["email"],
            "updated_by_role": editor["role"],
            "updated_at": now,
            "edit_history": history,
        }},
    )
    updated = await db.ground_bookings.find_one({"id": booking_id, "entity": "ALPHA"}, {"_id": 0})
    out = _public(updated)
    out["discount_submitted"] = bool(pricing["discountRequested"] and not (doc.get("pricing") or {}).get("discountPending"))
    return out


@router.patch("/{booking_id}/status")
async def update_status(booking_id: str, payload: BookingStatusIn, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    doc = await db.ground_bookings.find_one({"id": booking_id, "entity": "ALPHA"}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Booking not found")
    if payload.status == "Confirmed" and (doc.get("pricing") or {}).get("discountPending"):
        raise HTTPException(400, "Confirm after Super Admin approves the discounted rate")
    allowed_from = ALLOWED_STATUS_FROM[payload.status]
    now = now_utc().isoformat()
    editor = _editor_stamp(user, now)
    res = await db.ground_bookings.update_one(
        {"id": booking_id, "status": {"$in": allowed_from}},
        {
            "$set": {
                "status": payload.status,
                "updated_at": now,
                "updated_by": editor["user_id"],
                "updated_by_name": editor["name"],
                "updated_by_email": editor["email"],
                "updated_by_role": editor["role"],
            },
            "$push": {"edit_history": {**editor, "action": payload.status.lower()}},
        },
    )
    if not res.matched_count:
        raise HTTPException(409, f"Booking cannot move from {doc.get('status')} to {payload.status}")
    if payload.status == "Cancelled":
        await _release_slots(booking_id)
    updated = await db.ground_bookings.find_one({"id": booking_id, "entity": "ALPHA"}, {"_id": 0})
    return _public(updated)


async def apply_discount_decision(req: dict, *, approved: bool) -> None:
    payload = req.get("payload") or {}
    booking_id = payload.get("booking_id") or req.get("subject_id")
    booking = await db.ground_bookings.find_one({"id": booking_id, "entity": "ALPHA"})
    if not booking:
        raise HTTPException(404, "Ground booking not found")
    pricing = dict(booking.get("pricing") or {})
    if approved:
        pricing["discountPending"] = False
    else:
        list_rate = float(payload.get("list_ground_rate") or pricing.get("listGroundRate") or 0)
        add_on = float(pricing.get("addOnTotal") or 0)
        pricing["groundRate"] = list_rate
        pricing["discountAmount"] = 0
        pricing["discountPending"] = False
        pricing["totalRevenue"] = round(list_rate + add_on, 2)
    await db.ground_bookings.update_one(
        {"id": booking_id},
        {"$set": {"pricing": pricing, "updated_at": now_utc().isoformat()}},
    )
    task_id = payload.get("task_id") or booking.get("discount_task_id")
    if task_id:
        await db.tasks.update_one(
            {"id": task_id},
            {"$set": {
                "status": "completed" if approved else "cancelled",
                "completion_remark": "Discount approved" if approved else "Discount rejected — list rate restored",
                "completed_at": now_utc().isoformat(),
                "updated_at": now_utc().isoformat(),
            }},
        )


@router.post("/quote")
async def quote_pricing(payload: BookingCreate, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    return _pricing_from_payload(payload)
