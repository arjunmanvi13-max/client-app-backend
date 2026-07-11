"""Milestone 11 — invoice and payment MVP."""
import os
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "super_admin": ("superadmin@prarambhika.com", "Super@123"),
    "principal": ("principal@prarambhika.com", "Principal@123"),
}
LEGACY_CREDS = {
    "super_admin": ("super@pws-alpha.com", "Super@123"),
    "principal": ("admin@pws-alpha.com", "Admin@123"),
}
TOKENS = {}


def _login(role):
    if role in TOKENS:
        return TOKENS[role]
    for creds in (CREDS, LEGACY_CREDS):
        email, pwd = creds[role]
        r = requests.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=15)
        if r.status_code == 200:
            TOKENS[role] = r.json()["access_token"]
            return TOKENS[role]
    pytest.skip(f"Could not log in as {role}")


def _hdr(role):
    return {"Authorization": f"Bearer {_login(role)}"}


def _student():
    r = requests.get(f"{API}/people", headers=_hdr("principal"), params={"kind": "student", "organization": "PWS"}, timeout=15)
    if r.status_code != 200 or not r.json():
        return None
    return r.json()[0]


@pytest.mark.integration
class TestInvoicePaymentMVP:
    def test_config_includes_statuses(self):
        r = requests.get(f"{API}/invoices/config", headers=_hdr("super_admin"), timeout=15)
        assert r.status_code == 200, r.text
        statuses = r.json().get("invoice_statuses") or []
        assert "partially_paid" in statuses
        assert "overdue" in statuses
        assert "refunded" in statuses

    def test_legacy_fees_still_listable(self):
        r = requests.get(f"{API}/fees", headers=_hdr("super_admin"), params={"entity_id": "alpha"}, timeout=15)
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)

    def test_reconcile_alpha(self):
        r = requests.get(f"{API}/invoices/reconcile/alpha", headers=_hdr("super_admin"), timeout=20)
        assert r.status_code == 200, r.text
        assert "legacy" in r.json()

    def test_invoice_number_format_after_migration(self):
        mig = requests.post(f"{API}/invoices/migrate-legacy/alpha", headers=_hdr("super_admin"), timeout=60)
        if mig.status_code != 200:
            pytest.skip("Migration not available")
        listed = requests.get(f"{API}/invoices", headers=_hdr("super_admin"), params={"entity_id": "alpha"}, timeout=15)
        if listed.status_code != 200 or not listed.json():
            pytest.skip("No invoices after migration")
        num = listed.json()[0].get("invoice_number", "")
        assert num.startswith("ALPHA-"), num

    def test_partial_payment_and_receipt_pdf(self):
        listed = requests.get(f"{API}/invoices", headers=_hdr("principal"), timeout=15)
        if listed.status_code != 200 or not listed.json():
            pytest.skip("No invoices")
        inv = next((i for i in listed.json() if i.get("balance_due", 0) > 0 and i.get("status") not in ("cancelled", "paid")), None)
        if not inv:
            pytest.skip("No payable invoice")
        detail = requests.get(f"{API}/invoices/{inv['id']}", headers=_hdr("principal"), timeout=15)
        if detail.status_code != 200:
            pytest.skip("Cannot load invoice")
        items = detail.json().get("items") or []
        if not items:
            pytest.skip("No line items")
        target = next((it for it in items if it.get("balance_due", 0) > 0), items[0])
        partial = min(100, target.get("balance_due", 0))
        if partial <= 0:
            pytest.skip("No balance")
        pay = requests.post(
            f"{API}/invoices/{inv['id']}/payments",
            headers=_hdr("principal"),
            json={
                "amount": partial,
                "payment_mode": "Cash",
                "allocations": [{"item_id": target["id"], "amount": partial}],
            },
            timeout=15,
        )
        assert pay.status_code == 200, pay.text
        body = pay.json()
        assert body.get("payment", {}).get("receipt_number") or body.get("receipt_number")
        assert body.get("status") in ("partially_paid", "partial", "paid", "overdue", "issued")
        pay_id = (body.get("payment") or {}).get("id")
        if pay_id:
            pdf = requests.get(f"{API}/invoices/receipts/{pay_id}/pdf", timeout=15)
            assert pdf.status_code == 200, pdf.text
            assert pdf.content[:4] == b"%PDF"

    def test_invoice_pdf(self):
        listed = requests.get(f"{API}/invoices", headers=_hdr("principal"), timeout=15)
        if listed.status_code != 200 or not listed.json():
            pytest.skip("No invoices")
        inv_id = listed.json()[0]["id"]
        pdf = requests.get(f"{API}/invoices/{inv_id}/pdf", timeout=15)
        assert pdf.status_code == 200
        assert pdf.content[:4] == b"%PDF"

    def test_create_draft_invoice_when_engine_on(self):
        student = _student()
        if not student:
            pytest.skip("No PWS student")
        requests.patch(f"{API}/invoices/config/pws", headers=_hdr("super_admin"), json={"use_invoice_engine": True}, timeout=15)
        create = requests.post(
            f"{API}/invoices",
            headers=_hdr("principal"),
            json={
                "entity_id": "pws",
                "person_id": student["id"],
                "due_date": "2026-12-31",
                "as_draft": True,
                "items": [{"description": "Test tuition", "fee_type": "tuition", "line_total": 500}],
            },
            timeout=15,
        )
        if create.status_code == 400:
            pytest.skip("Engine not enabled")
        assert create.status_code == 200, create.text
        assert create.json().get("status") in ("draft", "issued")
        num = create.json().get("invoice_number", "")
        assert num.startswith("PWS-"), num
