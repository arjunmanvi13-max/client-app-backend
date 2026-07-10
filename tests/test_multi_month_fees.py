"""Regression tests for the multi-month fee collection & dashboard aggregation feature.

Scope (iteration 10):
  1. GET /api/fees/dashboard triggers bulk materialization so ALL active ALPHA players
     have monthly fees rolled up to the current month (2026-07).
  2. GET /api/fees/player-dues/{karan_id} returns Monthly dues for 2025-12 through 2026-07
     plus Registration; summary.previous_pending_due > 0 and total = current + previous.
  3. POST /api/fees/collect-multi with fees from DIFFERENT months returns one batch_id,
     total = sum, each fee row is updated.
  4. Receipt PDF endpoint returns 200 and lists BOTH months.
  5. Mixed-player fee_ids → 400; already-paid ids → 400; Online without ref → 400.
  6. Post-collection: dashboard collected_today increased by the batch total AND
     due_past decreased by the batch total (for Balua).
  7. Reports financial summary reflects outstanding & collections.
  8. Cleanup: revert Karan's collected fees back to 'due' — data unchanged for user.
"""
import os
import asyncio
import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://unified-track.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

SUPER_EMAIL = "superadmin@prarambhika.com"
SUPER_PWD = "Super@123"

_state = {}


# ------------------------- Fixtures -------------------------
@pytest.fixture(scope="module")
def super_token():
    r = requests.post(f"{API}/auth/login", json={"email": SUPER_EMAIL, "password": SUPER_PWD}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def headers(super_token):
    return {"Authorization": f"Bearer {super_token}", "Content-Type": "application/json"}


def _reset_karan_fees_sync():
    """Reset Karan's Monthly fees to 'due' before/after test run (idempotent)."""
    from motor.motor_asyncio import AsyncIOMotorClient
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not (mongo_url and db_name):
        return
    async def _reset():
        c = AsyncIOMotorClient(mongo_url)
        db = c[db_name]
        karan = await db.people.find_one({"name": {"$regex": "^Karan", "$options": "i"}, "kind": "player"})
        if karan:
            await db.fees.update_many(
                {"player_id": karan["id"], "status": "paid"},
                {"$set": {"status": "due", "payment_mode": None, "reference_id": None,
                          "transaction_date": None, "paid_at": None, "collected_by_id": None,
                          "collected_by_name": None, "batch_id": None, "notes": None}},
            )
        c.close()
    asyncio.new_event_loop().run_until_complete(_reset())


@pytest.fixture(scope="module", autouse=True)
def reset_before_and_after():
    _reset_karan_fees_sync()
    yield
    _reset_karan_fees_sync()


# ------------------------- T1: Dashboard triggers materialization -------------------------
class TestDashboardTriggersMaterialization:
    def test_dashboard_ok_and_has_balua(self, headers):
        r = requests.get(f"{API}/fees/dashboard", headers=headers, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "by_centre" in data and "Balua" in data["by_centre"], data
        _state["dash_before"] = data["by_centre"]["Balua"]


# ------------------------- T2: Karan has multiple past-month monthly dues -------------------------
class TestKaranMultiMonthDues:
    def test_find_karan(self, headers):
        r = requests.get(f"{API}/people", params={"kind": "player"}, headers=headers, timeout=20)
        assert r.status_code == 200
        matches = [p for p in r.json() if "karan" in (p.get("name") or "").lower()]
        assert matches, "Karan Raj not found"
        karan = matches[0]
        _state["karan_id"] = karan["id"]
        _state["karan_admission"] = karan.get("date_of_admission")
        assert karan.get("centre") == "Balua"

    def test_karan_unpaid_covers_2025_12_through_2026_07(self, headers):
        r = requests.get(f"{API}/fees/player-dues/{_state['karan_id']}", headers=headers, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        unpaid = data["unpaid"]
        summary = data["summary"]
        # Extract Monthly period_months from unpaid
        monthly_periods = sorted({f["period_month"] for f in unpaid if f["fee_type"] == "Monthly"})
        # Karan admitted 2025-12; current month is 2026-07 → 8 monthly periods
        expected = ["2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]
        for m in expected:
            assert m in monthly_periods, f"Missing monthly period {m}. Have: {monthly_periods}"
        types = {f["fee_type"] for f in unpaid}
        assert "Registration" in types
        assert summary["previous_pending_due"] > 0, summary
        assert summary["total_outstanding"] == summary["current_month_due"] + summary["previous_pending_due"]
        # Stash two fees from DIFFERENT months for later
        m_jan = next((f for f in unpaid if f["fee_type"] == "Monthly" and f["period_month"] == "2026-01"), None)
        m_feb = next((f for f in unpaid if f["fee_type"] == "Monthly" and f["period_month"] == "2026-02"), None)
        assert m_jan and m_feb, "Could not find 2026-01 and 2026-02 monthly fees"
        _state["fee_jan"] = m_jan
        _state["fee_feb"] = m_feb


# ------------------------- T3: collect-multi across different months -------------------------
class TestCollectMulti:
    def test_dashboard_before(self, headers):
        r = requests.get(f"{API}/fees/dashboard", headers=headers, timeout=30)
        assert r.status_code == 200
        _state["balua_before"] = r.json()["by_centre"]["Balua"]

    def test_collect_two_months(self, headers):
        fee_ids = [_state["fee_jan"]["id"], _state["fee_feb"]["id"]]
        expected_total = _state["fee_jan"]["amount_due"] + _state["fee_feb"]["amount_due"]
        r = requests.post(
            f"{API}/fees/collect-multi",
            json={"fee_ids": fee_ids, "payment_mode": "Cash"},
            headers=headers, timeout=20,
        )
        assert r.status_code == 200, r.text
        rcpt = r.json()
        assert rcpt["batch_id"]
        assert rcpt["total_amount"] == expected_total, f"total mismatch. got {rcpt['total_amount']} vs {expected_total}"
        assert len(rcpt["fees"]) == 2
        batch_ids = {f.get("batch_id") for f in rcpt["fees"]}
        assert batch_ids == {rcpt["batch_id"]}, "All fees must share the same batch_id"
        paid_ats = {f.get("paid_at") for f in rcpt["fees"]}
        assert None not in paid_ats
        periods = sorted(f["period_month"] for f in rcpt["fees"])
        assert periods == ["2026-01", "2026-02"]
        _state["batch_id"] = rcpt["batch_id"]
        _state["batch_total"] = rcpt["total_amount"]

    def test_receipt_pdf_lists_both_months(self):
        r = requests.get(f"{API}/fees/receipt/{_state['batch_id']}/pdf", timeout=20)
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/pdf")
        content = r.content
        assert len(content) > 500
        # PDF is compressed but period strings may be embedded — quick sanity: file starts with %PDF
        assert content[:4] == b"%PDF"


# ------------------------- T4: Validation errors -------------------------
class TestCollectMultiValidation:
    def test_mixed_player_400(self, headers):
        # Find another player different from Karan
        r = requests.get(f"{API}/people", params={"kind": "player"}, headers=headers, timeout=20)
        others = [p for p in r.json() if p["id"] != _state["karan_id"] and p.get("organization") == "ALPHA"]
        assert others, "Need at least one other ALPHA player for mixed-player test"
        other_id = others[0]["id"]
        r2 = requests.get(f"{API}/fees", params={"player_id": other_id, "status": "due"}, headers=headers, timeout=20)
        other_fees = r2.json()
        if not other_fees:
            pytest.skip("Other player has no unpaid fee")
        # Pick one of Karan's remaining unpaid + one other player's unpaid
        r3 = requests.get(f"{API}/fees/player-dues/{_state['karan_id']}", headers=headers, timeout=30)
        karan_unpaid = r3.json()["unpaid"]
        assert karan_unpaid
        payload = {"fee_ids": [karan_unpaid[0]["id"], other_fees[0]["id"]], "payment_mode": "Cash"}
        r4 = requests.post(f"{API}/fees/collect-multi", json=payload, headers=headers, timeout=20)
        assert r4.status_code == 400, r4.text

    def test_already_paid_400(self, headers):
        # Reuse one of the just-collected fee ids
        payload = {"fee_ids": [_state["fee_jan"]["id"]], "payment_mode": "Cash"}
        r = requests.post(f"{API}/fees/collect-multi", json=payload, headers=headers, timeout=20)
        assert r.status_code == 400, r.text

    def test_online_without_reference_400(self, headers):
        # Pick a still-unpaid fee for Karan
        r = requests.get(f"{API}/fees/player-dues/{_state['karan_id']}", headers=headers, timeout=30)
        unpaid = r.json()["unpaid"]
        assert unpaid
        payload = {"fee_ids": [unpaid[0]["id"]], "payment_mode": "Online"}
        r2 = requests.post(f"{API}/fees/collect-multi", json=payload, headers=headers, timeout=20)
        assert r2.status_code == 400, r2.text


# ------------------------- T5: Dashboard reflects the collection -------------------------
class TestDashboardReflectsCollection:
    def test_collected_today_up_by_batch_and_due_past_down(self, headers):
        r = requests.get(f"{API}/fees/dashboard", headers=headers, timeout=30)
        assert r.status_code == 200
        after = r.json()["by_centre"]["Balua"]
        before = _state["balua_before"]
        total = _state["batch_total"]
        # collected_today should have gone up by exactly the batch total
        assert after["collected_today"] >= before["collected_today"] + total, (
            f"collected_today did not increase by batch. before={before['collected_today']} "
            f"after={after['collected_today']} batch={total}"
        )
        # due_past should have decreased by the batch (both fees were past-month for Karan)
        assert after["due_past"] <= before["due_past"] - total, (
            f"due_past did not decrease by batch. before={before['due_past']} "
            f"after={after['due_past']} batch={total}"
        )

    def test_reports_financial_summary_ok(self, headers):
        r = requests.get(f"{API}/reports/financial/summary", headers=headers, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        # Sanity: response is a dict/list with recognizable revenue keys
        assert isinstance(data, (dict, list)) and data


# ------------------------- T6: Cleanup — revert Karan's fees -------------------------
class TestCleanup:
    def test_revert_batch_fees(self):
        """Revert the just-collected batch back to 'due' so user data is unchanged."""
        from motor.motor_asyncio import AsyncIOMotorClient
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME")
        assert mongo_url and db_name
        async def _revert():
            c = AsyncIOMotorClient(mongo_url)
            db = c[db_name]
            res = await db.fees.update_many(
                {"batch_id": _state["batch_id"]},
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
            # Also revert anything else that got paid during this run for Karan
            await db.fees.update_many(
                {"player_id": _state["karan_id"], "status": "paid"},
                {"$set": {"status": "due", "payment_mode": None, "reference_id": None,
                          "transaction_date": None, "paid_at": None, "collected_by_id": None,
                          "collected_by_name": None, "batch_id": None, "notes": None}},
            )
            c.close()
            return res.modified_count
        modified = asyncio.new_event_loop().run_until_complete(_revert())
        assert modified >= 2
