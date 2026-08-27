"""First-run factory reset — wipe operational data before going live.

The most destructive operation in the system, so it is gated four ways: Super
Admin only, the caller re-enters their own password, an exact confirmation
phrase, and a dry run that reports exactly what would be deleted. Super Admin
accounts are never removed — a reset must not lock the operator out.
"""
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core import db, get_current_user, is_super_admin, logger, now_utc, verify_password_async
from login_throttle import check_login_allowed, record_login_failure, reset_login_failures

router = APIRouter(prefix="/admin/factory-reset", tags=["factory-reset"])

CONFIRMATION_PHRASE = "DELETE ALL DATA"

OPERATIONAL_COLLECTIONS = (
    "people",
    "fees",
    "invoices",
    "invoice_items",
    "payments",
    "attendance",
    "attendance_audit",
    "academic_marks",
    "report_cards",
    "report_card_audit",
    "tasks",
    "approval_requests",
    "notifications",
    "notification_outbox",
    "gate_passes",
    "roll_calls",
    "player_assessments",
    "expense_entries",
    "expense_audit_logs",
    "otps",
    "coach_assignment_audit",
    "user_type_audit",
    "permission_audit",
    "timetable_substitutions",
    "timetable_duty_roster",
    "timetable_audit_log",
    "teacher_class_assignments",
    "teacher_section_assignments",
)

ACADEMIC_COLLECTIONS = (
    "academic_years",
    "grades",
    "sections",
    "subjects",
    "exam_terms",
    "assessments",
    "grading_scales",
    "timetable_periods",
    "timetable_slots",
)

FINANCE_COLLECTIONS = (
    "fee_catalogue",
    "fee_plans",
    "expense_heads",
    "entity_settings",
    "counters",
)

PRESERVED_COLLECTIONS = ("app_meta", "category_module_access", "factory_reset_audit")


class FactoryResetIn(BaseModel):
    password: str = Field(min_length=1, description="The caller's own password, re-entered")
    confirmation: str = Field(description=f'Must be exactly "{CONFIRMATION_PHRASE}"')
    include_academic_structure: bool = True
    include_finance_setup: bool = True
    include_login_accounts: bool = True
    reason: Optional[str] = None


def _targets(payload: FactoryResetIn) -> List[str]:
    names = list(OPERATIONAL_COLLECTIONS)
    if payload.include_academic_structure:
        names.extend(ACADEMIC_COLLECTIONS)
    if payload.include_finance_setup:
        names.extend(FINANCE_COLLECTIONS)
    return names


async def _assert_reauthenticated(request: Request, user: dict, password: str) -> None:
    """Re-entering the password is what stops a walk-up on an unlocked laptop."""
    email = (user.get("email") or "").lower()
    client_ip = (request.client.host if request.client else "") or "unknown"
    check_login_allowed(email, client_ip)

    fresh = await db.users.find_one({"id": user["id"]}, {"_id": 0, "password_hash": 1})
    stored = (fresh or {}).get("password_hash") or ""
    if not stored or not await verify_password_async(password, stored):
        record_login_failure(email, client_ip)
        logger.warning("Factory reset refused — bad password for %s from %s", email, client_ip)
        raise HTTPException(403, "Password is incorrect")
    reset_login_failures(email, client_ip)


@router.post("")
async def factory_reset(
    payload: FactoryResetIn,
    request: Request,
    dry_run: bool = Query(True, description="Report what would be deleted; write nothing"),
    user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    if not is_super_admin(user):
        raise HTTPException(403, "Only a Super Admin can reset the system")
    if payload.confirmation.strip() != CONFIRMATION_PHRASE:
        raise HTTPException(400, f'Type "{CONFIRMATION_PHRASE}" to confirm')

    await _assert_reauthenticated(request, user, payload.password)

    names = _targets(payload)
    counts: Dict[str, int] = {}
    for name in names:
        n = await db[name].count_documents({})
        if n:
            counts[name] = n

    other_users_q = {"role": {"$ne": "super_admin"}}
    other_users = await db.users.count_documents(other_users_q)
    if payload.include_login_accounts and other_users:
        counts["users"] = other_users

    total = sum(counts.values())
    kept_admins = await db.users.count_documents({"role": "super_admin"})

    if dry_run:
        return {
            "status": "preview",
            "dry_run": True,
            "total_documents": total,
            "counts": counts,
            "super_admins_kept": kept_admins,
            "preserved_collections": list(PRESERVED_COLLECTIONS),
        }

    if kept_admins < 1:
        raise HTTPException(409, "Refusing to reset — no Super Admin account would remain")

    deleted: Dict[str, int] = {}
    failures: Dict[str, str] = {}
    for name in names:
        try:
            res = await db[name].delete_many({})
            if res.deleted_count:
                deleted[name] = res.deleted_count
        except Exception as exc:
            failures[name] = str(exc)
            logger.exception("Factory reset could not clear %s", name)

    if payload.include_login_accounts:
        try:
            res = await db.users.delete_many(other_users_q)
            if res.deleted_count:
                deleted["users"] = res.deleted_count
        except Exception as exc:
            failures["users"] = str(exc)
            logger.exception("Factory reset could not clear login accounts")

    remaining_admins = await db.users.count_documents({"role": "super_admin"})
    audit = {
        "id": str(uuid.uuid4()),
        "at": now_utc().isoformat(),
        "actor_id": user["id"],
        "actor_name": user.get("name"),
        "actor_email": user.get("email"),
        "reason": (payload.reason or "").strip() or None,
        "scope": {
            "academic_structure": payload.include_academic_structure,
            "finance_setup": payload.include_finance_setup,
            "login_accounts": payload.include_login_accounts,
        },
        "deleted": deleted,
        "failures": failures or None,
        "total_deleted": sum(deleted.values()),
        "super_admins_remaining": remaining_admins,
    }
    await db.factory_reset_audit.insert_one(dict(audit))
    audit.pop("_id", None)
    logger.warning(
        "FACTORY RESET by %s — %s documents cleared across %s collections",
        user.get("email"), audit["total_deleted"], len(deleted),
    )

    return {
        "status": "ok" if not failures else "partial",
        "dry_run": False,
        "total_deleted": audit["total_deleted"],
        "deleted": deleted,
        "failures": failures,
        "super_admins_kept": remaining_admins,
        "detail": (
            "System cleared. Sign out and back in to start fresh."
            if not failures
            else "Some collections could not be cleared — see failures."
        ),
    }


@router.get("/history")
async def reset_history(user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    if not is_super_admin(user):
        raise HTTPException(403, "Only a Super Admin can view reset history")
    rows = await db.factory_reset_audit.find({}, {"_id": 0}).sort("at", -1).to_list(50)
    return {"resets": rows}
