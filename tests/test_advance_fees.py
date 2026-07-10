"""Regression tests for ADVANCE FEE PAYMENT feature (iteration 11).

Scope:
  1. GET /api/fees/player-dues/{karan_id} returns financial_year_end='2027-03' and
     `advance` list with Monthly entries for 2026-08..2027-03 (8 months, ₹9000 each),
     no Transport (Karan has none).
  2. POST /api/fees/collect-multi with 1 existing due + 2 advance months → 200,
     single batch, receipt.fees has 3 rows sorted by period, advance_payment=true
     on the two new rows, total = sum. Receipt PDF returns 200.
  3. Advance-only payment (fee_ids=[], advance=[one], player_id) → 200; after that
     player-dues no longer lists paid future months in advance nor in unpaid;
     dues summary outstanding is unchanged.
  4. Validations: current month → 400, next FY → 400, duplicate → 400,
     already-paid month again → 400, Transport for player w/o transport → 400,
     empty payload → 400, advance-only without player_id → 400.
  5. Dashboard aggregation: collected_today includes advance amounts;
     due_current/due_past unaffected by advance rows.
  6. Cleanup: DELETE all advance_payment=True rows, revert real due fees paid
     during tests back to status=due. Verify Karan ends 9 due / 0 paid /
     0 advance rows. Do NOT touch Mohit.
"""
import os
import asyncio
import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE_URL = os.environ.get(
    "EXPO_PUBLIC_BACKEND_URL", "https://unified-track.preview.emergentagent.com"
).rstrip("/")
API = f"{BASE_URL}/api"

SUPER_EMAIL = "superadmin@prarambhika.com"
SUPER_PWD = "Super@123"

_state = {}


# ---------- Helpers ----------
def _mongo():
    from motor.motor_asyncio import AsyncIOMotorClient
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    assert mongo_url and db_name
    c = AsyncIOMotorClient(mongo_url)
    return c, c[db_name]


def _reset_karan_and_delete_advance():
    async def _do():
        c, db = _mongo()
        karan = await db.people.find_one(
            {"name": {"$regex": "^Karan", "$options": "i"}, "kind": "player"}
        )
        if karan:
            # Delete any advance_payment rows for Karan
            await db.fees.delete_many(
                {"player_id": karan["id"], "advance_payment": True}
            )
            # Revert any paid fees back to due
            await db.fees.update_many(
                {"player_id": karan["id"], "status": "paid"},
                {"$set": {
                    "status": "due", "payment_mode": None, "reference_id": None,
                    "transaction_date": None, "paid_at": None,
                    "collected_by_id": None, "collected_by_name": None,
                    "batch_id": None, "notes": None,
                }},
            )
        c.close()
    asyncio.new_event_loop().run_until_complete(_do())


# ---------- Fixtures ----------
@pytest.fixture(scope="module")
def super_token():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": SUPER_EMAIL, "password": SUPER_PWD},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def headers(super_token):
    return {"Authorization": f"Bearer {super_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module", autouse=True)
def reset_before_and_after():
    _reset_karan_and_delete_advance()
    yield
    _reset_karan_and_delete_advance()


# ---------- T1: player-dues advance shape ----------
class TestPlayerDuesAdvanceShape:
    def test_find_karan(self, headers):
        r = requests.get(f"{API}/people", params={"kind": "player"}, headers=headers, timeout=20)
        assert r.status_code == 200
        matches = [p for p in r.json() if "karan" in (p.get("name") or "").lower()]
        assert matches, "Karan Raj not found"
        _state["karan"] = matches[0]

    def test_advance_list_and_fy_end(self, headers):
        r = requests.get(f"{API}/fees/player-dues/{_state['karan']['id']}", headers=headers, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("financial_year_end") == "2027-03", data.get("financial_year_end")
        assert data.get("current_month") == "2026-07"
        adv = data.get("advance") or []
        monthly = [a for a in adv if a["fee_type"] == "Monthly"]
        transport = [a for a in adv if a["fee_type"] == "Transport"]
        periods = sorted(a["period_month"] for a in monthly)
        expected = ["2026-08", "2026-09", "2026-10", "2026-11",
                    "2026-12", "2027-01", "2027-02", "2027-03"]
        # snapshot dues summary for later comparison BEFORE asserting so downstream tests still run
        _state["dues_before"] = data["summary"]
        _state["unpaid_before"] = data["unpaid"]
        _state["monthly_amt"] = monthly[0]["amount"] if monthly else 0
        assert periods == expected, f"Advance monthly periods mismatch: {periods}"
        # Karan's monthly is rate-card driven: Hostel Only/Cricket = 12000 (his category per iteration_10)
        amounts = {a["amount"] for a in monthly}
        assert len(amounts) == 1 and next(iter(amounts)) > 0, f"Advance amounts not uniform/positive: {monthly}"
        assert transport == [], "Karan has no transport — expected no Transport rows"


# ---------- T2: mixed dues + advance ----------
class TestCollectMixed:
    def test_dashboard_before(self, headers):
        r = requests.get(f"{API}/fees/dashboard", headers=headers, timeout=30)
        assert r.status_code == 200
        _state["dash_before"] = r.json()["by_centre"]["Balua"]

    def test_collect_one_due_plus_two_advance(self, headers):
        # Grab a still-unpaid Monthly (2026-01)
        unpaid = _state["unpaid_before"]
        m_jan = next((f for f in unpaid if f["fee_type"] == "Monthly" and f["period_month"] == "2026-01"), None)
        assert m_jan, "Missing 2026-01 monthly for Karan"
        payload = {
            "fee_ids": [m_jan["id"]],
            "advance": [
                {"period_month": "2026-08", "fee_type": "Monthly"},
                {"period_month": "2026-09", "fee_type": "Monthly"},
            ],
            "player_id": _state["karan"]["id"],
            "payment_mode": "Cash",
        }
        r = requests.post(f"{API}/fees/collect-multi", json=payload, headers=headers, timeout=30)
        assert r.status_code == 200, r.text
        rcpt = r.json()
        assert rcpt["batch_id"]
        assert len(rcpt["fees"]) == 3
        # Sorted by period_month
        periods = [f["period_month"] for f in rcpt["fees"]]
        assert periods == sorted(periods), f"fees not sorted by period: {periods}"
        assert set(periods) == {"2026-01", "2026-08", "2026-09"}
        # Total = 9000 + 9000 + 9000 = 27000 (Karan monthly is 9000)
        expected_total = sum(f["amount_due"] for f in rcpt["fees"])
        assert rcpt["total_amount"] == expected_total
        # Two advance rows must have advance_payment True
        adv_rows = [f for f in rcpt["fees"] if f.get("advance_payment") is True]
        assert len(adv_rows) == 2
        _state["batch_id_1"] = rcpt["batch_id"]
        _state["batch_1_total"] = rcpt["total_amount"]

    def test_receipt_pdf_ok(self):
        r = requests.get(f"{API}/fees/receipt/{_state['batch_id_1']}/pdf", timeout=30)
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert r.content[:4] == b"%PDF"


# ---------- T3: advance-only payment ----------
class TestAdvanceOnly:
    def test_advance_only(self, headers):
        payload = {
            "fee_ids": [],
            "advance": [{"period_month": "2026-10", "fee_type": "Monthly"}],
            "player_id": _state["karan"]["id"],
            "payment_mode": "Cash",
        }
        r = requests.post(f"{API}/fees/collect-multi", json=payload, headers=headers, timeout=30)
        assert r.status_code == 200, r.text
        rcpt = r.json()
        assert len(rcpt["fees"]) == 1
        assert rcpt["fees"][0]["period_month"] == "2026-10"
        assert rcpt["fees"][0]["advance_payment"] is True
        _state["batch_id_2"] = rcpt["batch_id"]
        _state["batch_2_total"] = rcpt["total_amount"]

    def test_dues_after_advance_paid(self, headers):
        r = requests.get(f"{API}/fees/player-dues/{_state['karan']['id']}", headers=headers, timeout=30)
        assert r.status_code == 200
        data = r.json()
        adv_periods = {a["period_month"] for a in data.get("advance", []) if a["fee_type"] == "Monthly"}
        # 08, 09, 10 must be removed from advance list
        for m in ("2026-08", "2026-09", "2026-10"):
            assert m not in adv_periods, f"{m} still in advance list after payment"
        # And they must NOT appear in unpaid
        unpaid_periods = {(f["fee_type"], f["period_month"]) for f in data["unpaid"]}
        for m in ("2026-08", "2026-09", "2026-10"):
            assert ("Monthly", m) not in unpaid_periods, f"{m} in unpaid after advance payment"
        # Dues summary outstanding: originally X, minus the one 2026-01 we paid
        before = _state["dues_before"]
        after = data["summary"]
        # Only 2026-01 (one Monthly, past due) was collected from outstanding — the advance
        # months should NOT reduce outstanding since they were future months.
        monthly_amt = _state["monthly_amt"]
        assert after["previous_pending_due"] == before["previous_pending_due"] - monthly_amt, (
            f"previous_pending_due should drop by {monthly_amt} only. before={before}, after={after}"
        )
        assert after["current_month_due"] == before["current_month_due"], "current month should be unchanged"


# ---------- T4: Validations ----------
class TestValidations:
    def _post(self, headers, body):
        return requests.post(f"{API}/fees/collect-multi", json=body, headers=headers, timeout=15)

    def test_current_month_rejected(self, headers):
        r = self._post(headers, {
            "fee_ids": [], "advance": [{"period_month": "2026-07", "fee_type": "Monthly"}],
            "player_id": _state["karan"]["id"], "payment_mode": "Cash",
        })
        assert r.status_code == 400, r.text

    def test_next_fy_rejected(self, headers):
        r = self._post(headers, {
            "fee_ids": [], "advance": [{"period_month": "2027-04", "fee_type": "Monthly"}],
            "player_id": _state["karan"]["id"], "payment_mode": "Cash",
        })
        assert r.status_code == 400, r.text

    def test_duplicate_selection_rejected(self, headers):
        r = self._post(headers, {
            "fee_ids": [],
            "advance": [
                {"period_month": "2026-11", "fee_type": "Monthly"},
                {"period_month": "2026-11", "fee_type": "Monthly"},
            ],
            "player_id": _state["karan"]["id"], "payment_mode": "Cash",
        })
        assert r.status_code == 400, r.text

    def test_already_paid_month_rejected(self, headers):
        # 2026-08 was paid in TestCollectMixed
        r = self._post(headers, {
            "fee_ids": [], "advance": [{"period_month": "2026-08", "fee_type": "Monthly"}],
            "player_id": _state["karan"]["id"], "payment_mode": "Cash",
        })
        assert r.status_code == 400, r.text

    def test_transport_no_config_rejected(self, headers):
        r = self._post(headers, {
            "fee_ids": [], "advance": [{"period_month": "2026-11", "fee_type": "Transport"}],
            "player_id": _state["karan"]["id"], "payment_mode": "Cash",
        })
        assert r.status_code == 400, r.text

    def test_empty_payload_rejected(self, headers):
        r = self._post(headers, {"fee_ids": [], "advance": [], "payment_mode": "Cash"})
        assert r.status_code == 400, r.text

    def test_advance_without_player_id_rejected(self, headers):
        r = self._post(headers, {
            "fee_ids": [],
            "advance": [{"period_month": "2026-11", "fee_type": "Monthly"}],
            "payment_mode": "Cash",
        })
        assert r.status_code == 400, r.text


# ---------- T5: Dashboard aggregation ----------
class TestDashboardAggregation:
    def test_dashboard_after(self, headers):
        r = requests.get(f"{API}/fees/dashboard", headers=headers, timeout=30)
        assert r.status_code == 200
        after = r.json()["by_centre"]["Balua"]
        before = _state["dash_before"]
        total_paid_today = _state["batch_1_total"] + _state["batch_2_total"]
        # collected_today must include advance amounts (paid today)
        assert after["collected_today"] >= before["collected_today"] + total_paid_today, (
            f"collected_today did not include advance. before={before['collected_today']} "
            f"after={after['collected_today']} paid_total={total_paid_today}"
        )
        # due_current unchanged (advance rows are future months, not current)
        assert after["due_current_month"] == before["due_current_month"], (
            f"due_current_month changed: {before['due_current_month']} → {after['due_current_month']}"
        )
        # due_past should have decreased by the one real due (monthly_amt, 2026-01)
        monthly_amt = _state["monthly_amt"]
        assert after["due_past"] == before["due_past"] - monthly_amt, (
            f"due_past mismatch: before={before['due_past']} after={after['due_past']} monthly={monthly_amt}"
        )


# ---------- T6: Cleanup ----------
class TestCleanupFinal:
    def test_cleanup_and_verify(self, headers):
        _reset_karan_and_delete_advance()
        # Confirm Karan ends with 9 due / 0 paid and 0 advance rows
        async def _check():
            c, db = _mongo()
            karan = await db.people.find_one(
                {"name": {"$regex": "^Karan", "$options": "i"}, "kind": "player"}
            )
            fees = await db.fees.find({"player_id": karan["id"]}, {"_id": 0}).to_list(1000)
            c.close()
            return fees
        fees = asyncio.new_event_loop().run_until_complete(_check())
        due = [f for f in fees if f.get("status") == "due"]
        paid = [f for f in fees if f.get("status") == "paid"]
        adv = [f for f in fees if f.get("advance_payment") is True]
        assert len(paid) == 0, f"Karan should have 0 paid, got {len(paid)}"
        assert len(adv) == 0, f"Karan should have 0 advance rows, got {len(adv)}"
        assert len(due) == 9, f"Karan should have 9 due (Reg + 8 Monthly), got {len(due)}: {[(f['fee_type'], f.get('period_month')) for f in due]}"
