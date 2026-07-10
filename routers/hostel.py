import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from core import db, GatePassCreate, GatePassDecision, RollCallIn, get_current_user, require_roles, now_utc

router = APIRouter(prefix="/hostel", tags=["hostel"])

@router.post("/gate-pass")
async def request_gate_pass(payload: GatePassCreate, user: dict = Depends(get_current_user)):
    doc = {
        "id": str(uuid.uuid4()),
        "resident_id": payload.resident_id,
        "requested_by": user["id"],
        "requested_by_name": user["name"],
        "reason": payload.reason,
        "out_time": payload.out_time.isoformat(),
        "expected_return": payload.expected_return.isoformat(),
        "destination": payload.destination,
        "status": "pending",
        "decision_note": None,
        "decided_by": None,
        "created_at": now_utc().isoformat(),
    }
    await db.gate_passes.insert_one(doc)
    doc.pop("_id", None)
    return doc

@router.get("/gate-pass")
async def list_gate_passes(status_filter: Optional[str] = None, _user: dict = Depends(get_current_user)):
    q = {}
    if status_filter:
        q["status"] = status_filter
    return await db.gate_passes.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)

@router.post("/gate-pass/{gp_id}/decision")
async def decide_gate_pass(
    gp_id: str,
    payload: GatePassDecision,
    user: dict = Depends(require_roles("warden", "admin", "super_admin")),
):
    res = await db.gate_passes.update_one(
        {"id": gp_id},
        {"$set": {
            "status": payload.decision, "decision_note": payload.note,
            "decided_by": user["id"], "decided_at": now_utc().isoformat(),
        }},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Gate pass not found")
    return await db.gate_passes.find_one({"id": gp_id}, {"_id": 0})

@router.post("/roll-call")
async def submit_roll_call(payload: RollCallIn, user: dict = Depends(require_roles("warden", "admin", "super_admin"))):
    saved = []
    for e in payload.entries:
        rec = {
            "id": str(uuid.uuid4()),
            "date": payload.date,
            "session": payload.session,
            "resident_id": e.resident_id,
            "present": e.present,
            "note": e.note,
            "marked_by": user["id"],
            "created_at": now_utc().isoformat(),
        }
        await db.roll_calls.update_one(
            {"date": payload.date, "session": payload.session, "resident_id": e.resident_id},
            {"$set": rec},
            upsert=True,
        )
        saved.append(rec)
    return {"count": len(saved)}

@router.get("/roll-call")
async def list_roll_call(date: Optional[str] = None, session: Optional[str] = None, _user: dict = Depends(get_current_user)):
    q = {}
    if date: q["date"] = date
    if session: q["session"] = session
    return await db.roll_calls.find(q, {"_id": 0}).sort("created_at", -1).to_list(1000)

@router.get("/dashboard")
async def warden_dashboard(_user: dict = Depends(require_roles("warden", "admin", "super_admin"))):
    today = now_utc().strftime("%Y-%m-%d")
    residents_count = await db.users.count_documents({"role": {"$in": ["student", "player"]}})
    pending_passes = await db.gate_passes.count_documents({"status": "pending"})
    morning = await db.roll_calls.count_documents({"date": today, "session": "morning", "present": True})
    night = await db.roll_calls.count_documents({"date": today, "session": "night", "present": True})
    return {
        "residents_count": residents_count,
        "pending_passes": pending_passes,
        "morning_present": morning,
        "night_present": night,
        "date": today,
    }
