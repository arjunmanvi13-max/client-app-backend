"""Unified notification delivery — in-app now; SMS/WhatsApp pluggable later."""
from __future__ import annotations

import uuid
from typing import Optional, Literal

from core import db, now_utc, resolve_user_institution, logger

# Canonical MVP notification types
NOTIFICATION_TYPES = (
    "attendance_marked",
    "absence",
    "invoice_issued",
    "invoice_overdue",
    "report_card_published",
    "task_assigned",
    "approval_requested",
    "approval_completed",
)

REF_TYPES = (
    "attendance",
    "person",
    "invoice",
    "report_card",
    "task",
    "approval",
    "fee",
)

CHANNELS = ("in_app", "sms", "whatsapp")

TYPE_ALIASES = {
    "absent_today": "absence",
    "absent": "absence",
    "approval_request": "approval_requested",
    "approval_approved": "approval_completed",
    "approval_rejected": "approval_completed",
    "deactivation_request": "approval_requested",
    "deactivation_approved": "approval_completed",
    "deactivation_rejected": "approval_completed",
    "fees_created": "invoice_issued",
}

DEFAULT_REF_TYPE = {
    "attendance_marked": "attendance",
    "absence": "attendance",
    "invoice_issued": "invoice",
    "invoice_overdue": "invoice",
    "report_card_published": "report_card",
    "task_assigned": "task",
    "approval_requested": "approval",
    "approval_completed": "approval",
}


def canonical_type(ntype: str) -> str:
    return TYPE_ALIASES.get(ntype, ntype)


def normalize_notification(doc: dict) -> dict:
    """Return a consistent API shape; supports legacy body/at/kind fields."""
    out = {k: v for k, v in doc.items() if k != "_id"}
    if not out.get("message") and out.get("body"):
        out["message"] = out["body"]
    if not out.get("created_at") and out.get("at"):
        out["created_at"] = out["at"]
    raw_type = out.get("type") or out.get("kind") or "general"
    out["type"] = canonical_type(raw_type)
    out["read"] = bool(out.get("read", False))
    if out.get("read") and not out.get("read_at"):
        out["read_at"] = out.get("read_at")
    if not out.get("ref_type") and out.get("ref_id"):
        out["ref_type"] = DEFAULT_REF_TYPE.get(out["type"])
    if not out.get("channels"):
        out["channels"] = ["in_app"]
    return out


def notification_filter_for_user(user: dict) -> dict:
    """Mongo filter matching notifications visible to this user (incl. legacy role fan-out)."""
    uid = user["id"]
    role = user.get("role")
    clauses: list[dict] = [{"user_id": uid}]
    if role:
        clauses.append({"audience_role": role, "user_id": {"$exists": False}})
    clauses.append({"audience_user": uid})
    base = {"$or": clauses}
    inst = resolve_user_institution(user)
    if inst == "BOTH":
        return base
    ent = "pws" if inst == "PWS" else "alpha"
    entity_clause = {
        "$or": [
            {"entity_id": ent},
            {"entity_id": "both"},
            {"entity_id": {"$exists": False}, "$or": [{"user_id": uid}, {"audience_user": uid}]},
        ]
    }
    return {"$and": [base, entity_clause]}


async def _deliver_in_app(doc: dict) -> None:
    """In-app channel — document is already persisted."""
    delivery = doc.setdefault("delivery", {})
    delivery["in_app"] = {"status": "delivered", "at": now_utc().isoformat()}


async def _enqueue_external(doc: dict) -> None:
    """Queue for future SMS/WhatsApp workers (no-op delivery today)."""
    for channel in ("sms", "whatsapp"):
        if channel in (doc.get("channels") or []):
            outbox = {
                "id": str(uuid.uuid4()),
                "notification_id": doc["id"],
                "user_id": doc.get("user_id"),
                "channel": channel,
                "type": doc.get("type"),
                "title": doc.get("title"),
                "message": doc.get("message"),
                "status": "pending",
                "created_at": now_utc().isoformat(),
            }
            try:
                await db.notification_outbox.insert_one(outbox)
            except Exception as exc:
                logger.warning("Could not queue %s notification: %s", channel, exc)


async def send_notification(
    user_id: str,
    *,
    ntype: str,
    title: str,
    message: str,
    ref_id: Optional[str] = None,
    ref_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    dedupe_today: bool = False,
    extra_channels: Optional[list[str]] = None,
) -> str:
    """Create and deliver a notification to one user."""
    ctype = canonical_type(ntype)
    if dedupe_today and ref_id:
        today = now_utc().strftime("%Y-%m-%d")
        existing = await db.notifications.find_one({
            "user_id": user_id,
            "type": {"$in": [ctype, ntype]},
            "ref_id": ref_id,
            "created_at": {"$gte": f"{today}T00:00:00"},
        }, {"id": 1})
        if existing:
            return existing["id"]

    if dedupe_today is False and ref_id and ctype in ("invoice_overdue", "invoice_issued", "report_card_published"):
        existing = await db.notifications.find_one({
            "user_id": user_id,
            "type": ctype,
            "ref_id": ref_id,
        }, {"id": 1})
        if existing:
            return existing["id"]

    channels = ["in_app"]
    if extra_channels:
        for ch in extra_channels:
            if ch in CHANNELS and ch not in channels:
                channels.append(ch)

    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "type": ctype,
        "title": title,
        "message": message,
        "read": False,
        "read_at": None,
        "created_at": now_utc().isoformat(),
        "channels": channels,
        "delivery": {},
    }
    if ref_id:
        doc["ref_id"] = ref_id
    if ref_type or ctype in DEFAULT_REF_TYPE:
        doc["ref_type"] = ref_type or DEFAULT_REF_TYPE.get(ctype)
    if entity_id:
        doc["entity_id"] = entity_id.lower()

    await db.notifications.insert_one(doc)
    await _deliver_in_app(doc)
    await _enqueue_external(doc)
    return doc["id"]


async def send_to_role(
    role: str,
    *,
    ntype: str,
    title: str,
    message: str,
    ref_id: Optional[str] = None,
    ref_type: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> int:
    """Fan out one notification per active user with the given role (entity-scoped)."""
    users = await db.users.find(
        {"role": role, "status": {"$ne": "deactivated"}},
        {"id": 1, "organization": 1},
    ).to_list(500)
    count = 0
    for u in users:
        if entity_id:
            inst = resolve_user_institution(u)
            want = entity_id.lower()
            if inst == "PWS" and want not in ("pws", "both"):
                continue
            if inst == "ALPHA" and want not in ("alpha", "both"):
                continue
        await send_notification(
            u["id"],
            ntype=ntype,
            title=title,
            message=message,
            ref_id=ref_id,
            ref_type=ref_type,
            entity_id=entity_id,
        )
        count += 1
    return count


async def notify_person_parents(
    person_id: str,
    *,
    ntype: str,
    title: str,
    message: str,
    entity_id: Optional[str] = None,
    ref_type: str = "person",
    dedupe_today: bool = True,
) -> int:
    """Notify all linked parent accounts for a student/player."""
    person = await db.people.find_one({"id": person_id}, {"_id": 0, "parent_user_ids": 1, "entities": 1, "organization": 1})
    if not person:
        return 0
    parent_ids = person.get("parent_user_ids") or []
    if not entity_id:
        ents = person.get("entities") or []
        if "PWS" in ents and "ALPHA" in ents:
            entity_id = "both"
        elif "PWS" in ents or person.get("organization") == "PWS":
            entity_id = "pws"
        else:
            entity_id = "alpha"
    sent = 0
    for pid in parent_ids:
        await send_notification(
            pid,
            ntype=ntype,
            title=title,
            message=message,
            ref_id=person_id,
            ref_type=ref_type,
            entity_id=entity_id,
            dedupe_today=dedupe_today,
        )
        sent += 1
    return sent


async def unread_count_for_user(user: dict) -> int:
    filt = {**notification_filter_for_user(user), "read": False}
    return await db.notifications.count_documents(filt)


async def mark_read(user: dict, notification_id: str) -> bool:
    filt = {**notification_filter_for_user(user), "id": notification_id}
    result = await db.notifications.update_one(
        filt,
        {"$set": {"read": True, "read_at": now_utc().isoformat()}},
    )
    return result.matched_count > 0


async def mark_all_read(user: dict) -> int:
    filt = {**notification_filter_for_user(user), "read": False}
    result = await db.notifications.update_many(
        filt,
        {"$set": {"read": True, "read_at": now_utc().isoformat()}},
    )
    return result.modified_count
