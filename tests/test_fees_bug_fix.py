"""Regression tests for player-fees auto-generation bug fix.

Bug: A player saved with organization='BOTH' had no fees auto-generated (silent failure).
Fixes verified here:
  - POST /api/people with kind=player forces organization='ALPHA' regardless of payload
  - Fees are auto-created for the newly created ALPHA player
  - Existing player 'Mohit Raj' shows backfilled Registration (15000) + Monthly (12000) dues
  - Fee collection works and dashboard reflects the collection
"""
import os
import time
import pytest
import requests
from dotenv import load_dotenv

# Load backend .env so cleanup step (Mongo direct) has MONGO_URL / DB_NAME
load_dotenv("/app/backend/.env")

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://unified-track.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

SUPER_EMAIL = "superadmin@prarambhika.com"
SUPER_PWD = "Super@123"


# ------------------------- Fixtures -------------------------
@pytest.fixture(scope="module")
def super_token():
    r = requests.post(f"{API}/auth/login", json={"email": SUPER_EMAIL, "password": SUPER_PWD}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def headers(super_token):
    return {"Authorization": f"Bearer {super_token}", "Content-Type": "application/json"}


# Shared state between tests (module-scoped)
_state = {}


@pytest.fixture(scope="module", autouse=True)
def reset_mohit_monthly_before_run():
    """Ensure Mohit's Monthly 2026-07 fee is 'due' BEFORE the test run (in case a
    previous run left it paid and cleanup didn't complete). This makes the suite
    idempotent."""
    from motor.motor_asyncio import AsyncIOMotorClient
    import asyncio
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not (mongo_url and db_name):
        yield
        return
    async def _reset():
        c = AsyncIOMotorClient(mongo_url)
        db = c[db_name]
        mohit = await db.people.find_one({"name": {"$regex": "^Mohit", "$options": "i"}, "kind": "player"})
        if mohit:
            await db.fees.update_many(
                {"player_id": mohit["id"], "fee_type": "Monthly"},
                {"$set": {"status": "due", "payment_mode": None, "reference_id": None,
                          "transaction_date": None, "paid_at": None, "collected_by_id": None,
                          "collected_by_name": None, "batch_id": None, "notes": None}},
            )
        c.close()
    asyncio.new_event_loop().run_until_complete(_reset())
    yield


# ------------------------- Test 1: PRIMARY — Mohit Raj shows dues -------------------------
class TestMohitDues:
    def test_find_mohit_raj_player(self, headers):
        r = requests.get(f"{API}/people", params={"kind": "player"}, headers=headers, timeout=20)
        assert r.status_code == 200
        people = r.json()
        matches = [p for p in people if "mohit" in (p.get("name") or "").lower()]
        assert matches, "Mohit Raj not found in players list"
        mohit = matches[0]
        assert mohit.get("organization") == "ALPHA", f"Expected ALPHA, got {mohit.get('organization')}"
        _state["mohit_id"] = mohit["id"]
        _state["mohit_name"] = mohit["name"]

    def test_mohit_has_registration_and_monthly_dues(self, headers):
        pid = _state["mohit_id"]
        r = requests.get(f"{API}/fees/player-dues/{pid}", headers=headers, timeout=20)
        assert r.status_code == 200, r.text
        data = r.json()
        unpaid = data.get("unpaid", [])
        assert unpaid, f"Mohit has no unpaid dues — dashboard would be empty. unpaid={unpaid}"
        types = {f["fee_type"] for f in unpaid}
        assert "Registration" in types, f"Missing Registration due. types={types}"
        assert "Monthly" in types, f"Missing Monthly due. types={types}"
        reg = next(f for f in unpaid if f["fee_type"] == "Registration")
        monthly = next(f for f in unpaid if f["fee_type"] == "Monthly")
        assert reg["amount_due"] == 15000, f"Registration amount_due expected 15000, got {reg['amount_due']}"
        assert monthly["amount_due"] == 12000, f"Monthly amount_due expected 12000, got {monthly['amount_due']}"
        _state["mohit_monthly_fee_id"] = monthly["id"]
        _state["mohit_orig_monthly"] = monthly


# ------------------------- Test 2: Collect Monthly fee for Mohit + Dashboard verify -------------------------
class TestCollectAndDashboard:
    def test_dashboard_before_collect(self, headers):
        r = requests.get(f"{API}/fees/dashboard", headers=headers, timeout=20)
        assert r.status_code == 200
        d = r.json()
        _state["collected_today_before"] = d["by_centre"].get("Balua", {}).get("collected_today", 0)

    def test_collect_monthly_fee(self, headers):
        fee_id = _state["mohit_monthly_fee_id"]
        # Use collect-multi to mirror the real UI flow (multi returns receipt with batch_id)
        r = requests.post(
            f"{API}/fees/collect-multi",
            json={"fee_ids": [fee_id], "payment_mode": "Cash"},
            headers=headers, timeout=20,
        )
        assert r.status_code == 200, r.text
        receipt = r.json()
        assert receipt["total_amount"] == 12000
        assert receipt["payment_mode"] == "Cash"
        assert receipt["batch_id"], "Receipt should have batch_id for PDF/share"
        _state["batch_id"] = receipt["batch_id"]

    def test_receipt_pdf_available(self):
        batch_id = _state["batch_id"]
        r = requests.get(f"{API}/fees/receipt/{batch_id}/pdf", timeout=20)
        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert len(r.content) > 500

    def test_dashboard_after_collect_shows_amount(self, headers):
        r = requests.get(f"{API}/fees/dashboard", headers=headers, timeout=20)
        assert r.status_code == 200
        d = r.json()
        after = d["by_centre"].get("Balua", {}).get("collected_today", 0)
        before = _state["collected_today_before"]
        assert after >= before + 12000, f"Dashboard did not reflect collection: before={before}, after={after}"


# ------------------------- Test 3: REGRESSION — new player with organization='BOTH' -------------------------
class TestNewPlayerForcesAlphaAndAutoFees:
    def test_create_player_with_org_both_forced_to_alpha(self, headers):
        today = time.strftime("%Y-%m-%d")
        body = {
            "name": "TEST_Fee Test Player",
            "kind": "player",
            "organization": "BOTH",   # <-- deliberately wrong; backend must force ALPHA
            "sport": "Cricket",
            "centre": "Balua",
            "player_type": "Daily",
            "slot": "Morning",
            "skill_level": "Beginner",
            "date_of_admission": today,
        }
        r = requests.post(f"{API}/people", json=body, headers=headers, timeout=20)
        assert r.status_code == 200, r.text
        created = r.json()
        assert created.get("organization") == "ALPHA", f"Backend did NOT force ALPHA. Got {created.get('organization')}"
        _state["new_player_id"] = created["id"]

    def test_new_player_has_auto_generated_fees(self, headers):
        pid = _state["new_player_id"]
        # small wait in case any async task
        time.sleep(0.5)
        r = requests.get(f"{API}/fees", params={"player_id": pid}, headers=headers, timeout=20)
        assert r.status_code == 200
        fees = r.json()
        types = {f["fee_type"] for f in fees}
        assert "Registration" in types, f"No auto Registration for new player. fees={fees}"
        assert "Monthly" in types, f"No auto Monthly for new player. fees={fees}"
        # Verify amounts match Daily/Cricket rate card: Reg 3000, Monthly 2500
        reg = next(f for f in fees if f["fee_type"] == "Registration")
        assert reg["amount"] == 3000
        monthly = next(f for f in fees if f["fee_type"] == "Monthly")
        assert monthly["amount"] == 2500
        _state["new_player_fee_ids"] = [f["id"] for f in fees]

    def test_db_record_has_alpha_organization(self, headers):
        # GET back via listing and verify persistence
        r = requests.get(f"{API}/people", params={"kind": "player"}, headers=headers, timeout=20)
        assert r.status_code == 200
        matches = [p for p in r.json() if p["id"] == _state["new_player_id"]]
        assert matches, "New player not found"
        assert matches[0]["organization"] == "ALPHA"


# ------------------------- Test 4: CLEANUP -------------------------
class TestCleanup:
    def test_revert_mohit_monthly_fee_to_due(self, headers):
        # Direct DB manipulation via Mongo is not exposed. We use $set via a raw update: but there's no
        # generic PATCH endpoint on a paid fee that reverts status. We must go via Mongo directly.
        # Use the app's DB by importing config.
        from motor.motor_asyncio import AsyncIOMotorClient
        import asyncio
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME")
        assert mongo_url and db_name, "MONGO_URL / DB_NAME must be set"
        async def _revert():
            c = AsyncIOMotorClient(mongo_url)
            db = c[db_name]
            await db.fees.update_one(
                {"id": _state["mohit_monthly_fee_id"]},
                {"$set": {
                    "status": "due",
                    "payment_mode": None,
                    "reference_id": None,
                    "transaction_date": None,
                    "paid_at": None,
                    "collected_by_id": None,
                    "collected_by_name": None,
                    "batch_id": None,
                    "notes": None,
                }},
            )
            # Verify
            f = await db.fees.find_one({"id": _state["mohit_monthly_fee_id"]})
            c.close()
            return f
        f = asyncio.new_event_loop().run_until_complete(_revert())
        assert f["status"] == "due", f"Revert failed. status={f['status']}"
        assert f.get("payment_mode") is None

    def test_delete_test_player_and_fees(self, headers):
        pid = _state.get("new_player_id")
        assert pid
        # delete fees first
        from motor.motor_asyncio import AsyncIOMotorClient
        import asyncio
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME")
        async def _cleanup():
            c = AsyncIOMotorClient(mongo_url)
            db = c[db_name]
            fdel = await db.fees.delete_many({"player_id": pid})
            pdel = await db.people.delete_one({"id": pid})
            c.close()
            return fdel.deleted_count, pdel.deleted_count
        fc, pc = asyncio.new_event_loop().run_until_complete(_cleanup())
        assert pc == 1, f"Test player not deleted: {pc}"
        assert fc >= 2, f"Fees not cleaned: {fc}"
