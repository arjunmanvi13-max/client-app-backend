"""Admission enquiry tracking for PWS and ALPHA."""
from __future__ import annotations

import re
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core import (
    db,
    get_current_user,
    is_alpha_accounts_user,
    is_alpha_admin_user,
    is_pws_accounts_user,
    is_pws_admin_user,
    is_super_admin,
    now_utc,
    today_ist,
    user_entity_scope,
)
from notifications_service import send_notification, send_to_role

router = APIRouter(prefix="/enquiries", tags=["enquiries"])

SOURCES = (
    "Phone Call", "Walk-in", "WhatsApp", "Website", "Social Media",
    "Referral", "Existing Parent", "Advertisement", "Other",
)
STATUSES = (
    "New", "Contacted", "Interested", "Visit Scheduled", "Application Started",
    "Application Submitted", "Admitted", "Not Interested", "Lost", "On Hold",
    "Pending Close",
)
ACTIVE_STATUSES = (
    "New", "Contacted", "Interested", "Visit Scheduled",
    "Application Started", "Application Submitted", "On Hold", "Pending Close",
)
FEE_DISCUSSION = ("Not Discussed", "Discussed", "Details Shared")
CONTACT_METHODS = ("Call", "WhatsApp", "Email")
PRIORITIES = ("Low", "Medium", "High")
GENDERS = ("Male", "Female", "Other")
RELATIONSHIPS = ("Mother", "Father", "Guardian", "Other")
ALPHA_SPORTS = ("Cricket", "Football")
ALPHA_CATEGORIES = ("Daily", "Hostel", "Boarding", "Day Boarding")
ALPHA_CAMPUSES = ("Balua", "Harding Park", "Defense Colony")
PWS_CLASSES = (
    "Nursery", "UKG",
    "Class I", "Class II", "Class III", "Class IV", "Class V", "Class VI",
    "Class VII", "Class VIII", "Class IX", "Class X",
)


def can_manage_enquiries(user: dict) -> bool:
    if is_super_admin(user):
        return True
    if is_pws_admin_user(user) or is_alpha_admin_user(user):
        return True
    if is_pws_accounts_user(user) or is_alpha_accounts_user(user):
        return True
    role = (user.get("role") or "").lower()
    return role in ("staff",)


def _assert_manage(user: dict) -> None:
    if not can_manage_enquiries(user):
        raise HTTPException(403, "Not allowed to manage enquiries")


def _entity_filter(user: dict) -> dict:
    scope = user_entity_scope(user)
    if scope == "both" or is_super_admin(user):
        return {}
    if scope == "alpha":
        return {"institution": "ALPHA"}
    return {"institution": "PWS"}


def _assert_access(user: dict, doc: dict) -> None:
    if is_super_admin(user):
        return
    if can_manage_enquiries(user):
        scope = user_entity_scope(user)
        inst = doc.get("institution")
        if scope == "both":
            return
        if scope == "alpha" and inst == "ALPHA":
            return
        if scope == "pws" and inst == "PWS":
            return
        raise HTTPException(403, "Not allowed to access this enquiry")
    uid = user["id"]
    if doc.get("assigned_to_id") == uid:
        return
    if uid in (doc.get("assignee_ids") or []):
        return
    raise HTTPException(403, "Not allowed to access this enquiry")


def _stamp(user: dict, action: str, note: Optional[str] = None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "at": now_utc().isoformat(),
        "by_id": user.get("id"),
        "by_name": user.get("name"),
        "by_role": user.get("role"),
        "action": action,
        "note": note,
    }


def _public(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k != "_id"}
    next_at = out.get("next_follow_up_at")
    out["follow_up_overdue"] = False
    if next_at and out.get("status") in ACTIVE_STATUSES and out.get("status") not in ("Pending Close",):
        try:
            out["follow_up_overdue"] = next_at[:16] < now_utc().isoformat()[:16]
        except Exception:
            out["follow_up_overdue"] = False
    return out


async def _next_enquiry_code() -> str:
    year = today_ist()[:4]
    prefix = f"ENQ-{year}-"
    last = await db.enquiries.find(
        {"enquiry_code": {"$regex": f"^{re.escape(prefix)}"}},
        {"_id": 0, "enquiry_code": 1},
    ).sort("enquiry_code", -1).to_list(1)
    n = 1
    if last:
        try:
            n = int(str(last[0].get("enquiry_code") or "").split("-")[-1]) + 1
        except ValueError:
            n = 1
    return f"{prefix}{n:04d}"


class EnquiryUpsert(BaseModel):
    institution: Literal["PWS", "ALPHA"]
    academic_year: Optional[str] = None
    enquiry_at: Optional[str] = None
    source: str
    referred_by: Optional[str] = None
    assigned_to_id: Optional[str] = None
    student_name: str
    dob: Optional[str] = None
    gender: Optional[str] = None
    current_school: Optional[str] = None
    current_class: Optional[str] = None
    applying_for: str
    preferred_campus: Optional[str] = None
    sibling_enrolled: bool = False
    sibling_name: Optional[str] = None
    sibling_class: Optional[str] = None
    parent_name: str
    relationship: Optional[str] = None
    mobile: str
    alternate_mobile: Optional[str] = None
    email: Optional[str] = None
    locality: Optional[str] = None
    address: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    alpha_sport: Optional[str] = None
    alpha_category: Optional[str] = None
    parent_expectations: Optional[str] = None
    fee_discussion_status: Optional[str] = "Not Discussed"
    prospectus_shared: bool = False
    visit_required: bool = False
    preferred_visit_at: Optional[str] = None
    interaction_notes: Optional[str] = None
    status: str = "New"
    last_contacted_at: Optional[str] = None
    next_follow_up_at: Optional[str] = None
    follow_up_remarks: Optional[str] = None
    lost_reason: Optional[str] = None
    priority: str = "Medium"


class AssignIn(BaseModel):
    assignee_id: str
    action_note: str = Field(min_length=3)


class CompleteAssignIn(BaseModel):
    remarks: str = Field(min_length=3)


class FollowUpIn(BaseModel):
    remarks: Optional[str] = None
    next_follow_up_at: Optional[str] = None


class CloseIn(BaseModel):
    reason: str = Field(min_length=3)
    lost_reason: Optional[str] = None


def _validate(payload: EnquiryUpsert) -> None:
    if payload.source not in SOURCES:
        raise HTTPException(400, "Invalid enquiry source")
    if payload.status not in STATUSES:
        raise HTTPException(400, "Invalid enquiry status")
    if payload.priority not in PRIORITIES:
        raise HTTPException(400, "Invalid priority")
    if payload.gender and payload.gender not in GENDERS:
        raise HTTPException(400, "Invalid gender")
    if payload.preferred_contact_method and payload.preferred_contact_method not in CONTACT_METHODS:
        raise HTTPException(400, "Invalid contact method")
    if payload.fee_discussion_status and payload.fee_discussion_status not in FEE_DISCUSSION:
        raise HTTPException(400, "Invalid fee discussion status")
    if not payload.student_name.strip():
        raise HTTPException(400, "Student name is required")
    if not payload.parent_name.strip():
        raise HTTPException(400, "Parent/guardian name is required")
    if not payload.mobile.strip():
        raise HTTPException(400, "Primary mobile number is required")
    if not payload.applying_for.strip():
        raise HTTPException(400, "Applying for is required")
    if payload.institution == "ALPHA":
        if payload.preferred_campus and payload.preferred_campus not in ALPHA_CAMPUSES:
            raise HTTPException(400, "Invalid ALPHA campus")
        if payload.alpha_sport and payload.alpha_sport not in ALPHA_SPORTS:
            raise HTTPException(400, "Invalid sport")
        if payload.alpha_category and payload.alpha_category not in ALPHA_CATEGORIES:
            raise HTTPException(400, "Invalid ALPHA category")
    if payload.status in ACTIVE_STATUSES and payload.status not in ("Pending Close", "On Hold"):
        if not payload.next_follow_up_at:
            raise HTTPException(400, "Next follow-up date is required for active enquiries")
    if payload.status in ("Not Interested", "Lost") and not (payload.lost_reason or "").strip():
        raise HTTPException(400, "Reason is required when closing a lead")


def _core(payload: EnquiryUpsert, assigned: Optional[dict]) -> dict:
    applying = payload.applying_for.strip()
    if payload.institution == "PWS" and not applying:
        applying = payload.current_class or ""
    return {
        "institution": payload.institution,
        "academic_year": (payload.academic_year or "2026-27").strip(),
        "enquiry_at": payload.enquiry_at or now_utc().isoformat(),
        "source": payload.source,
        "referred_by": (payload.referred_by or "").strip() or None,
        "assigned_to_id": assigned["id"] if assigned else payload.assigned_to_id,
        "assigned_to_name": assigned["name"] if assigned else None,
        "assigned_to_role": assigned.get("role") if assigned else None,
        "office_queue": not bool(assigned or payload.assigned_to_id),
        "student_name": payload.student_name.strip(),
        "dob": payload.dob or None,
        "gender": payload.gender or None,
        "current_school": (payload.current_school or "").strip() or None,
        "current_class": (payload.current_class or "").strip() or None,
        "applying_for": applying,
        "preferred_campus": payload.preferred_campus if payload.institution == "ALPHA" else None,
        "sibling_enrolled": bool(payload.sibling_enrolled),
        "sibling_name": (payload.sibling_name or "").strip() or None,
        "sibling_class": (payload.sibling_class or "").strip() or None,
        "parent_name": payload.parent_name.strip(),
        "relationship": payload.relationship or None,
        "mobile": payload.mobile.strip(),
        "alternate_mobile": (payload.alternate_mobile or "").strip() or None,
        "email": (payload.email or "").strip() or None,
        "locality": (payload.locality or "").strip() or None,
        "address": (payload.address or "").strip() or None,
        "preferred_contact_method": payload.preferred_contact_method or "Call",
        "alpha_sport": payload.alpha_sport if payload.institution == "ALPHA" else None,
        "alpha_category": payload.alpha_category if payload.institution == "ALPHA" else None,
        "parent_expectations": (payload.parent_expectations or "").strip() or None,
        "fee_discussion_status": payload.fee_discussion_status or "Not Discussed",
        "prospectus_shared": bool(payload.prospectus_shared),
        "visit_required": bool(payload.visit_required),
        "preferred_visit_at": payload.preferred_visit_at or None,
        "interaction_notes": (payload.interaction_notes or "").strip() or None,
        "status": payload.status,
        "last_contacted_at": payload.last_contacted_at or None,
        "next_follow_up_at": payload.next_follow_up_at or None,
        "follow_up_remarks": (payload.follow_up_remarks or "").strip() or None,
        "lost_reason": (payload.lost_reason or "").strip() or None,
        "priority": payload.priority,
    }


async def _load_user(user_id: str) -> dict:
    row = await db.users.find_one({"id": user_id}, {"_id": 0, "id": 1, "name": 1, "role": 1, "email": 1})
    if not row:
        raise HTTPException(404, "Staff member not found")
    return row


@router.get("")
async def list_enquiries(
    q: Optional[str] = None,
    institution: Optional[str] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
    assigned_to_id: Optional[str] = None,
    follow_up_on: Optional[str] = None,
    overdue: bool = False,
    user: dict = Depends(get_current_user),
):
    query: dict = {}
    if can_manage_enquiries(user):
        query.update(_entity_filter(user))
    else:
        query["assigned_to_id"] = user["id"]
    if institution in ("PWS", "ALPHA"):
        query["institution"] = institution
    if status in STATUSES:
        query["status"] = status
    if source in SOURCES:
        query["source"] = source
    if assigned_to_id:
        query["assigned_to_id"] = assigned_to_id
    if follow_up_on and re.match(r"^\d{4}-\d{2}-\d{2}$", follow_up_on):
        query["next_follow_up_at"] = {"$regex": f"^{follow_up_on}"}
    if q and q.strip():
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        query["$or"] = [
            {"enquiry_code": rx},
            {"student_name": rx},
            {"parent_name": rx},
            {"mobile": rx},
            {"applying_for": rx},
        ]
    rows = await db.enquiries.find(query, {"_id": 0}).sort("enquiry_at", -1).to_list(500)
    out = [_public(r) for r in rows]
    if overdue:
        out = [r for r in out if r.get("follow_up_overdue")]
    return out


@router.get("/options")
async def options(user: dict = Depends(get_current_user)):
    _assert_manage(user)
    staff = await db.users.find(
        {"status": {"$ne": "deactivated"}},
        {"_id": 0, "id": 1, "name": 1, "role": 1, "email": 1, "user_type": 1},
    ).sort("name", 1).to_list(400)
    return {
        "sources": list(SOURCES),
        "statuses": list(STATUSES),
        "priorities": list(PRIORITIES),
        "genders": list(GENDERS),
        "relationships": list(RELATIONSHIPS),
        "contact_methods": list(CONTACT_METHODS),
        "fee_discussion": list(FEE_DISCUSSION),
        "pws_classes": list(PWS_CLASSES),
        "alpha_sports": list(ALPHA_SPORTS),
        "alpha_categories": list(ALPHA_CATEGORIES),
        "alpha_campuses": list(ALPHA_CAMPUSES),
        "staff": staff,
    }


@router.get("/{enquiry_id}")
async def get_enquiry(enquiry_id: str, user: dict = Depends(get_current_user)):
    doc = await db.enquiries.find_one({"id": enquiry_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Enquiry not found")
    _assert_access(user, doc)
    return _public(doc)


@router.post("")
async def create_enquiry(payload: EnquiryUpsert, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    _validate(payload)
    assigned = await _load_user(payload.assigned_to_id) if payload.assigned_to_id else None
    now = now_utc().isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "enquiry_code": await _next_enquiry_code(),
        **_core(payload, assigned),
        "converted_person_id": None,
        "close_approval_id": None,
        "active_task_id": None,
        "history": [_stamp(user, "created")],
        "created_by": user["id"],
        "created_by_name": user.get("name"),
        "created_at": now,
        "updated_by": user["id"],
        "updated_by_name": user.get("name"),
        "updated_at": now,
    }
    await db.enquiries.insert_one(doc)
    return _public(doc)


@router.put("/{enquiry_id}")
async def update_enquiry(enquiry_id: str, payload: EnquiryUpsert, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    doc = await db.enquiries.find_one({"id": enquiry_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Enquiry not found")
    _assert_access(user, doc)
    if doc.get("status") == "Admitted":
        raise HTTPException(400, "Admitted enquiries cannot be edited")
    if payload.status in ("Not Interested", "Lost"):
        raise HTTPException(400, "Closing a lead requires Principal or Super Admin approval")
    _validate(payload)
    assigned = await _load_user(payload.assigned_to_id) if payload.assigned_to_id else None
    fields = _core(payload, assigned)
    history = list(doc.get("history") or [])
    history.append(_stamp(user, "updated"))
    await db.enquiries.update_one({"id": enquiry_id}, {"$set": {
        **fields,
        "history": history,
        "updated_by": user["id"],
        "updated_by_name": user.get("name"),
        "updated_at": now_utc().isoformat(),
    }})
    out = await db.enquiries.find_one({"id": enquiry_id}, {"_id": 0})
    return _public(out)


@router.post("/{enquiry_id}/assign")
async def assign_enquiry(enquiry_id: str, payload: AssignIn, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    doc = await db.enquiries.find_one({"id": enquiry_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Enquiry not found")
    _assert_access(user, doc)
    assignee = await _load_user(payload.assignee_id)
    now = now_utc().isoformat()
    note = payload.action_note.strip()
    task = {
        "id": str(uuid.uuid4()),
        "title": f"Enquiry {doc.get('enquiry_code')} — {note[:80]}",
        "description": (
            f"{doc.get('student_name')} · {doc.get('parent_name')} · {doc.get('mobile')}\n"
            f"Action: {note}"
        ),
        "entity_id": (doc.get("institution") or "PWS").lower(),
        "priority": (doc.get("priority") or "medium").lower(),
        "due_date": doc.get("next_follow_up_at"),
        "deadline": doc.get("next_follow_up_at"),
        "assignee_id": assignee["id"],
        "assignee_name": assignee.get("name"),
        "assignee_role": assignee.get("role"),
        "assignee_ids": [assignee["id"]],
        "department": "Operations",
        "category": "enquiry_follow_up",
        "follow_up_required": True,
        "status": "open",
        "created_by": user["id"],
        "created_by_name": user.get("name"),
        "created_by_role": user.get("role"),
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
        "ref_type": "enquiry",
        "ref_id": enquiry_id,
        "comments": [],
    }
    await db.tasks.insert_one(task)
    history = list(doc.get("history") or [])
    history.append(_stamp(user, "assigned", note))
    status = doc.get("status") if doc.get("status") not in ("New",) else "Contacted"
    await db.enquiries.update_one({"id": enquiry_id}, {"$set": {
        "assigned_to_id": assignee["id"],
        "assigned_to_name": assignee.get("name"),
        "assigned_to_role": assignee.get("role"),
        "office_queue": False,
        "active_task_id": task["id"],
        "status": status,
        "history": history,
        "updated_by": user["id"],
        "updated_by_name": user.get("name"),
        "updated_at": now,
    }})
    if assignee["id"] != user["id"]:
        await send_notification(
            assignee["id"],
            ntype="task_assigned",
            title="Enquiry assigned",
            message=f"{doc.get('enquiry_code')}: {note}",
            ref_id=task["id"],
            ref_type="task",
            entity_id=(doc.get("institution") or "PWS").lower(),
        )
    out = await db.enquiries.find_one({"id": enquiry_id}, {"_id": 0})
    out = _public(out)
    out["task_id"] = task["id"]
    return out


@router.post("/{enquiry_id}/complete-assignment")
async def complete_assignment(enquiry_id: str, payload: CompleteAssignIn, user: dict = Depends(get_current_user)):
    doc = await db.enquiries.find_one({"id": enquiry_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Enquiry not found")
    _assert_access(user, doc)
    if doc.get("assigned_to_id") != user["id"] and not can_manage_enquiries(user):
        raise HTTPException(403, "Only the assigned person can complete this step")
    now = now_utc().isoformat()
    remarks = payload.remarks.strip()
    task_id = doc.get("active_task_id")
    if task_id:
        await db.tasks.update_one({"id": task_id}, {"$set": {
            "status": "completed",
            "completed_at": now,
            "updated_at": now,
        }})
    office_id = doc.get("created_by")
    office = await db.users.find_one({"id": office_id}, {"_id": 0, "id": 1, "name": 1, "role": 1}) if office_id else None
    history = list(doc.get("history") or [])
    history.append(_stamp(user, "completed_and_returned", remarks))
    await db.enquiries.update_one({"id": enquiry_id}, {"$set": {
        "assigned_to_id": office.get("id") if office else None,
        "assigned_to_name": office.get("name") if office else "Admissions office",
        "assigned_to_role": office.get("role") if office else None,
        "office_queue": True,
        "active_task_id": None,
        "last_contacted_at": now,
        "follow_up_remarks": remarks,
        "history": history,
        "updated_by": user["id"],
        "updated_by_name": user.get("name"),
        "updated_at": now,
    }})
    if office and office.get("id") and office["id"] != user["id"]:
        await send_notification(
            office["id"],
            ntype="task_assigned",
            title="Enquiry returned to office",
            message=f"{doc.get('enquiry_code')}: {remarks}",
            ref_id=enquiry_id,
            ref_type="enquiry",
            entity_id=(doc.get("institution") or "PWS").lower(),
        )
    out = await db.enquiries.find_one({"id": enquiry_id}, {"_id": 0})
    return _public(out)


@router.post("/{enquiry_id}/follow-up-done")
async def mark_follow_up(enquiry_id: str, payload: FollowUpIn, user: dict = Depends(get_current_user)):
    doc = await db.enquiries.find_one({"id": enquiry_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Enquiry not found")
    _assert_access(user, doc)
    now = now_utc().isoformat()
    history = list(doc.get("history") or [])
    history.append(_stamp(user, "follow_up_completed", payload.remarks))
    nxt = payload.next_follow_up_at or doc.get("next_follow_up_at")
    await db.enquiries.update_one({"id": enquiry_id}, {"$set": {
        "last_contacted_at": now,
        "follow_up_remarks": (payload.remarks or doc.get("follow_up_remarks")),
        "next_follow_up_at": nxt,
        "history": history,
        "updated_by": user["id"],
        "updated_by_name": user.get("name"),
        "updated_at": now,
    }})
    out = await db.enquiries.find_one({"id": enquiry_id}, {"_id": 0})
    return _public(out)


@router.post("/{enquiry_id}/close")
async def request_close(enquiry_id: str, payload: CloseIn, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    doc = await db.enquiries.find_one({"id": enquiry_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Enquiry not found")
    _assert_access(user, doc)
    if doc.get("status") in ("Admitted", "Lost", "Not Interested"):
        raise HTTPException(400, "Enquiry is already closed")
    existing = await db.approval_requests.find_one({
        "type": "enquiry_close",
        "subject_id": enquiry_id,
        "status": "pending",
    })
    if existing:
        raise HTTPException(400, "A close request is already pending")
    now = now_utc().isoformat()
    entity = doc.get("institution") or "PWS"
    task = {
        "id": str(uuid.uuid4()),
        "title": f"Approve closing enquiry {doc.get('enquiry_code')}",
        "description": payload.reason.strip(),
        "entity_id": entity.lower(),
        "priority": "high",
        "due_date": None,
        "deadline": None,
        "assignee_id": None,
        "assignee_name": "Principal / Super Admin",
        "assignee_role": "super_admin",
        "assignee_ids": [],
        "department": "Operations",
        "category": "enquiry_close",
        "status": "open",
        "created_by": user["id"],
        "created_by_name": user.get("name"),
        "created_at": now,
        "updated_at": now,
        "ref_type": "enquiry",
        "ref_id": enquiry_id,
        "comments": [],
    }
    await db.tasks.insert_one(task)
    approval = {
        "id": str(uuid.uuid4()),
        "type": "enquiry_close",
        "status": "pending",
        "entity_id": entity.lower(),
        "organization": entity,
        "subject_id": enquiry_id,
        "subject_label": f"{doc.get('enquiry_code')} · {doc.get('student_name')}",
        "reason": payload.reason.strip(),
        "payload": {
            "enquiry_id": enquiry_id,
            "previous_status": doc.get("status"),
            "lost_reason": payload.lost_reason or payload.reason.strip(),
            "task_id": task["id"],
            "target_role": "Enquiry",
        },
        "requested_by_id": user["id"],
        "requested_by_name": user.get("name"),
        "requested_at": now,
        "decided_by_id": None,
        "decided_by_name": None,
        "decided_at": None,
        "decision_note": None,
        "history": [_stamp(user, "submitted", payload.reason.strip())],
        "comments": [],
    }
    await db.approval_requests.insert_one(approval)
    history = list(doc.get("history") or [])
    history.append(_stamp(user, "close_requested", payload.reason.strip()))
    await db.enquiries.update_one({"id": enquiry_id}, {"$set": {
        "status": "Pending Close",
        "lost_reason": payload.lost_reason or payload.reason.strip(),
        "close_approval_id": approval["id"],
        "history": history,
        "updated_by": user["id"],
        "updated_by_name": user.get("name"),
        "updated_at": now,
    }})
    await send_to_role(
        "super_admin",
        ntype="approval_requested",
        title="Enquiry close requested",
        message=f"{user.get('name')} asked to close {doc.get('enquiry_code')}",
        ref_id=approval["id"],
        ref_type="approval",
        entity_id=entity.lower(),
    )
    out = await db.enquiries.find_one({"id": enquiry_id}, {"_id": 0})
    out = _public(out)
    out["approval_id"] = approval["id"]
    return out


async def apply_enquiry_close_decision(req: dict, approved: bool) -> None:
    enquiry_id = (req.get("payload") or {}).get("enquiry_id") or req.get("subject_id")
    doc = await db.enquiries.find_one({"id": enquiry_id})
    if not doc:
        return
    payload = req.get("payload") or {}
    task_id = payload.get("task_id")
    now = now_utc().isoformat()
    history = list(doc.get("history") or [])
    history.append({
        "id": str(uuid.uuid4()),
        "at": now,
        "by_id": req.get("decided_by_id"),
        "by_name": req.get("decided_by_name"),
        "action": "close_approved" if approved else "close_rejected",
        "note": req.get("decision_note"),
    })
    if approved:
        await db.enquiries.update_one({"id": enquiry_id}, {"$set": {
            "status": "Lost",
            "lost_reason": payload.get("lost_reason") or doc.get("lost_reason"),
            "history": history,
            "updated_at": now,
        }})
    else:
        await db.enquiries.update_one({"id": enquiry_id}, {"$set": {
            "status": payload.get("previous_status") or "On Hold",
            "close_approval_id": None,
            "history": history,
            "updated_at": now,
        }})
    if task_id:
        await db.tasks.update_one({"id": task_id}, {"$set": {
            "status": "completed" if approved else "cancelled",
            "completed_at": now,
            "updated_at": now,
        }})


@router.post("/{enquiry_id}/convert")
async def convert_to_admission(enquiry_id: str, user: dict = Depends(get_current_user)):
    _assert_manage(user)
    doc = await db.enquiries.find_one({"id": enquiry_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Enquiry not found")
    _assert_access(user, doc)
    if doc.get("converted_person_id"):
        person = await db.people.find_one({"id": doc["converted_person_id"]}, {"_id": 0})
        return {"enquiry": _public(doc), "person": person, "already_converted": True}
    inst = doc.get("institution")
    now = now_utc().isoformat()
    if inst == "PWS":
        person = {
            "id": str(uuid.uuid4()),
            "kind": "student",
            "name": doc.get("student_name"),
            "organization": "PWS",
            "entities": ["PWS"],
            "dob": doc.get("dob"),
            "gender": doc.get("gender"),
            "guardian_name": doc.get("parent_name"),
            "guardian_phone": doc.get("mobile"),
            "father_name": doc.get("parent_name") if doc.get("relationship") == "Father" else None,
            "mother_name": doc.get("parent_name") if doc.get("relationship") == "Mother" else None,
            "mobile": doc.get("mobile"),
            "email": doc.get("email"),
            "locality": doc.get("locality"),
            "address": doc.get("address"),
            "pws_class": doc.get("applying_for"),
            "date_of_admission": today_ist(),
            "status": "active",
            "enquiry_id": enquiry_id,
            "created_at": now,
        }
        from student_academic import sync_student_academic_fields
        from people_enrollment import assign_enrollment_ids
        person = await sync_student_academic_fields(person)
        person = await assign_enrollment_ids(person)
        await db.people.insert_one(person)
        try:
            from routers.fees import auto_create_fees_for_student
            await auto_create_fees_for_student(person)
        except Exception:
            pass
        href_kind = "students"
    else:
        if not doc.get("preferred_campus") or not doc.get("alpha_sport") or not doc.get("alpha_category"):
            raise HTTPException(400, "ALPHA conversion needs campus, sport, and category on the enquiry")
        category = doc.get("alpha_category")
        if category == "Hostel":
            category = "Hostel"
        person = {
            "id": str(uuid.uuid4()),
            "kind": "player",
            "name": doc.get("student_name"),
            "organization": "ALPHA",
            "entities": ["ALPHA"],
            "dob": doc.get("dob"),
            "gender": doc.get("gender"),
            "guardian_name": doc.get("parent_name"),
            "guardian_phone": doc.get("mobile"),
            "mobile": doc.get("mobile"),
            "email": doc.get("email"),
            "locality": doc.get("locality"),
            "address": doc.get("address"),
            "centre": doc.get("preferred_campus"),
            "sport": doc.get("alpha_sport"),
            "player_type": category,
            "date_of_admission": today_ist(),
            "status": "active",
            "enquiry_id": enquiry_id,
            "created_at": now,
        }
        from people_enrollment import assign_enrollment_ids
        person = await assign_enrollment_ids(person)
        await db.people.insert_one(person)
        try:
            from routers.fees import auto_create_fees_for_player
            await auto_create_fees_for_player(person)
        except Exception:
            pass
        href_kind = "players"
    history = list(doc.get("history") or [])
    history.append(_stamp(user, "converted_to_admission"))
    await db.enquiries.update_one({"id": enquiry_id}, {"$set": {
        "status": "Admitted",
        "converted_person_id": person["id"],
        "history": history,
        "updated_by": user["id"],
        "updated_by_name": user.get("name"),
        "updated_at": now,
    }})
    person.pop("_id", None)
    out = await db.enquiries.find_one({"id": enquiry_id}, {"_id": 0})
    return {
        "enquiry": _public(out),
        "person": person,
        "directory_kind": href_kind,
        "already_converted": False,
    }
