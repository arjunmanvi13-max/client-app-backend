"""Concurrency guards on the money paths.

Each test fires the same request simultaneously and asserts exactly one succeeds.
Guards against double receipts, double concessions and duplicate monthly fee rows.

Requires a running backend (EXPO_PUBLIC_BACKEND_URL) seeded with demo data.
"""
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/") + "/api"
SUPER = ("superadmin@prarambhika.com", "Super@123")


def _login(email, password):
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=20)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def token():
    return _login(*SUPER)


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}"}


def _a_player(headers):
    r = requests.get(f"{BASE}/people?kind=player", headers=headers, timeout=20)
    assert r.status_code == 200, r.text
    people = r.json()
    people = people if isinstance(people, list) else people.get("items", [])
    assert people, "no players seeded"
    return people[0]


def _fresh_unpaid_fee(headers, player):
    """Create an ad-hoc fee we can safely collect in a test."""
    r = requests.post(
        f"{BASE}/fees",
        headers=headers,
        json={
            "player_id": player["id"],
            "fee_type": "Other",
            "amount": 500,
            "due_date": "2026-12-01",
            "notes": f"concurrency probe {uuid.uuid4().hex[:8]}",
        },
        timeout=20,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _parallel(fn, n=8):
    """Fire n identical requests at once, released together to widen the race window."""
    barrier = threading.Barrier(n)

    def wrapped():
        barrier.wait()
        return fn()

    with ThreadPoolExecutor(max_workers=n) as ex:
        return [f.result() for f in [ex.submit(wrapped) for _ in range(n)]]


class TestFeeCollectionRace:
    def test_double_submit_collects_once(self, headers):
        player = _a_player(headers)
        fee = _fresh_unpaid_fee(headers, player)
        fee_id = fee.get("id") or fee.get("fee", {}).get("id")
        assert fee_id, fee

        def collect():
            return requests.post(
                f"{BASE}/fees/{fee_id}/collect",
                headers=headers,
                json={"payment_mode": "Cash"},
                timeout=30,
            ).status_code

        codes = _parallel(collect, 8)
        assert codes.count(200) == 1, f"fee collected twice: {codes}"
        loser = [c for c in codes if c != 200]
        assert loser and 400 <= loser[0] < 500, f"loser should be rejected, got {codes}"

    def test_collect_multi_double_submit_mints_one_receipt(self, headers):
        player = _a_player(headers)
        fee = _fresh_unpaid_fee(headers, player)
        fee_id = fee.get("id") or fee.get("fee", {}).get("id")

        def collect():
            r = requests.post(
                f"{BASE}/fees/collect-multi",
                headers=headers,
                json={"player_id": player["id"], "fee_ids": [fee_id], "payment_mode": "Cash"},
                timeout=30,
            )
            return r.status_code, r.json() if r.content else None

        results = _parallel(collect, 8)
        codes = [c for c, _ in results]
        assert codes.count(200) == 1, f"batch collected twice: {codes}"

        receipts = {
            (body or {}).get("receipt_number")
            for c, body in results
            if c == 200 and body
        }
        assert len(receipts) <= 1, f"two receipts minted for one payment: {receipts}"


class TestDiscountRace:
    def test_discount_cannot_exceed_amount_due(self, headers):
        player = _a_player(headers)
        fee = _fresh_unpaid_fee(headers, player)
        fee_id = fee.get("id") or fee.get("fee", {}).get("id")

        r = requests.patch(
            f"{BASE}/fees/{fee_id}/discount",
            headers=headers,
            json={"discount_amount": 999_999, "reason": "probe"},
            timeout=20,
        )
        assert r.status_code == 400, r.text

    def test_negative_balance_rejected(self, headers):
        player = _a_player(headers)
        fee = _fresh_unpaid_fee(headers, player)
        fee_id = fee.get("id") or fee.get("fee", {}).get("id")

        r = requests.patch(
            f"{BASE}/fees/{fee_id}/discount",
            headers=headers,
            json={"discount_amount": 100, "new_amount_due": -5000, "reason": "probe"},
            timeout=20,
        )
        assert r.status_code == 400, r.text

    def test_concurrent_discounts_do_not_stack_past_zero(self, headers):
        player = _a_player(headers)
        fee = _fresh_unpaid_fee(headers, player)
        fee_id = fee.get("id") or fee.get("fee", {}).get("id")

        def discount():
            return requests.patch(
                f"{BASE}/fees/{fee_id}/discount",
                headers=headers,
                json={"discount_amount": 400, "reason": "probe"},
                timeout=30,
            ).status_code

        _parallel(discount, 8)
        r = requests.get(f"{BASE}/fees?player_id={player['id']}", headers=headers, timeout=20)
        assert r.status_code == 200, r.text
        rows = r.json()
        rows = rows if isinstance(rows, list) else rows.get("items", [])
        row = next((f for f in rows if f.get("id") == fee_id), None)
        assert row is not None, "fee row disappeared"
        assert int(row.get("amount_due") or 0) >= 0, f"amount_due went negative: {row}"


class TestMoneyBounds:
    def test_negative_adhoc_amount_rejected(self, headers):
        player = _a_player(headers)
        r = requests.post(
            f"{BASE}/fees",
            headers=headers,
            json={
                "player_id": player["id"],
                "fee_type": "Other",
                "amount": -5000,
                "due_date": "2026-12-01",
            },
            timeout=20,
        )
        assert r.status_code == 422, r.text

    def test_malformed_due_date_rejected(self, headers):
        player = _a_player(headers)
        r = requests.post(
            f"{BASE}/fees",
            headers=headers,
            json={
                "player_id": player["id"],
                "fee_type": "Other",
                "amount": 100,
                "due_date": "01/12/2026",
            },
            timeout=20,
        )
        assert r.status_code == 422, r.text


class TestInvoiceSerialization:
    """POST /invoices and POST /invoices/{id}/payments must not 500.

    pymongo's insert_one/insert_many mutate the passed dict, adding an ObjectId
    `_id`, and those same dicts were returned to FastAPI — which cannot serialize
    an ObjectId. Invoice creation failed for every caller even though the invoice
    was written.
    """

    def _student_id(self, headers):
        r = requests.get(f"{BASE}/people?kind=student", headers=headers, timeout=20)
        assert r.status_code == 200, r.text
        rows = r.json()
        rows = rows if isinstance(rows, list) else rows.get("items", [])
        assert rows, "no students seeded"
        return rows[0]["id"]

    def test_create_invoice_and_pay(self, headers):
        person_id = self._student_id(headers)
        created = requests.post(
            f"{BASE}/invoices",
            headers=headers,
            json={
                "entity_id": "pws",
                "person_id": person_id,
                "due_date": "2026-12-31",
                "items": [{
                    "description": f"regression {uuid.uuid4().hex[:6]}",
                    "quantity": 1,
                    "unit_price": 500,
                    "line_total": 500,
                }],
            },
            timeout=30,
        )
        assert created.status_code == 200, created.text
        invoice = created.json()
        assert "_id" not in invoice, "raw Mongo _id leaked into the response"
        assert invoice["items"], invoice
        assert all("_id" not in it for it in invoice["items"]), "item _id leaked"

        item_id = invoice["items"][0]["id"]
        paid = requests.post(
            f"{BASE}/invoices/{invoice['id']}/payments",
            headers=headers,
            json={
                "amount": 500,
                "payment_mode": "Cash",
                "allocations": [{"item_id": item_id, "amount": 500}],
            },
            timeout=30,
        )
        assert paid.status_code == 200, paid.text
        body = paid.json()
        assert body["balance_due"] == 0, body
        assert body.get("receipt_number"), body
        assert "_id" not in body.get("payment", {}), "payment _id leaked"

        over = requests.post(
            f"{BASE}/invoices/{invoice['id']}/payments",
            headers=headers,
            json={
                "amount": 500,
                "payment_mode": "Cash",
                "allocations": [{"item_id": item_id, "amount": 500}],
            },
            timeout=30,
        )
        assert over.status_code == 400, f"overpayment should be rejected: {over.text}"


class TestPwsRoadmapCollectionRace:
    """Two operators collecting the same PWS roadmap fee must mint one receipt."""

    def _a_pws_student(self, headers):
        r = requests.get(f"{BASE}/people?kind=student", headers=headers, timeout=20)
        assert r.status_code == 200, r.text
        rows = r.json()
        rows = rows if isinstance(rows, list) else rows.get("data", rows.get("items", []))
        for person in rows:
            roadmap = requests.get(f"{BASE}/pws-fees/roadmap/{person['id']}", headers=headers, timeout=30)
            if roadmap.status_code == 200:
                return person, roadmap.json()
        pytest.skip("no PWS student with a fee roadmap")

    @pytest.mark.asyncio
    async def test_double_submit_collects_once(self, headers):
        person, roadmap = self._a_pws_student(headers)
        due = [
            item
            for month in roadmap.get("months", [])
            for item in month.get("items", [])
            if item.get("fee_id") and item.get("status") != "paid"
        ]
        if not due:
            pytest.skip("student has no unpaid roadmap fee")
        fee_id = due[0]["fee_id"]

        def collect():
            return requests.post(
                f"{BASE}/pws-fees/collect",
                headers=headers,
                json={"fee_ids": [fee_id], "payment_mode": "Cash"},
                timeout=30,
            )

        results = _parallel(collect)
        codes = [r.status_code for r in results]
        assert codes.count(200) == 1, f"expected exactly one receipt, got {codes}"

        batches = {r.json().get("batch_id") for r in results if r.status_code == 200}
        assert len(batches) == 1

        from core import db

        stamped = await db.fees.count_documents({"id": fee_id, "status": "paid"})
        assert stamped == 1, "fee should be paid exactly once"

        await db.fees.update_one({"id": fee_id}, {
            "$set": {"status": "due"},
            "$unset": {
                "batch_id": "", "receipt_number": "", "paid_at": "", "paid_on": "",
                "payment_mode": "", "reference_id": "", "transaction_date": "",
                "collected_by_id": "", "collected_by_name": "", "notes": "",
            },
        })
