"""Milestone 10 — fee catalogue and fee plans MVP."""
import os
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "principal": ("principal@prarambhika.com", "Principal@123"),
    "super_admin": ("superadmin@prarambhika.com", "Super@123"),
    "admin": ("admin@prarambhika.com", "Admin@123"),
}
LEGACY_CREDS = {
    "principal": ("admin@pws-alpha.com", "Admin@123"),
    "super_admin": ("super@pws-alpha.com", "Super@123"),
    "admin": ("admin@pws-alpha.com", "Admin@123"),
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


@pytest.mark.integration
class TestFeeCatalogMVP:
    def test_meta_lists_fee_types_and_frequencies(self):
        r = requests.get(f"{API}/fee-catalog/meta", headers=_hdr("principal"), timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "tuition" in body["fee_types"]
        assert "monthly" in body["frequencies"]
        assert "one_time" in body["frequencies"]

    def test_seeded_catalogue_items(self):
        r = requests.get(f"{API}/fee-catalog/items", headers=_hdr("principal"), params={"entity_id": "pws"}, timeout=15)
        assert r.status_code == 200, r.text
        items = r.json()
        if not items:
            pytest.skip("Fee catalogue not seeded")
        types = {i["fee_type"] for i in items}
        assert "tuition" in types or "registration" in types

    def test_seeded_fee_plans(self):
        r = requests.get(f"{API}/fee-catalog/plans", headers=_hdr("principal"), params={"entity_id": "pws"}, timeout=15)
        assert r.status_code == 200, r.text
        plans = r.json()
        if not plans:
            pytest.skip("Fee plans not seeded")
        plan = plans[0]
        assert plan.get("resolved_items") or plan.get("items")
        assert plan.get("rates") is not None

    def test_resolve_pws_day_scholar(self):
        r = requests.get(
            f"{API}/fee-catalog/plans/resolve",
            headers=_hdr("principal"),
            params={"entity_id": "pws", "kind": "student", "is_resident": False},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        rates = body.get("rates") or {}
        if not rates:
            pytest.skip("No matching PWS plan")
        assert rates.get("registration") or rates.get("monthly")

    def test_resolve_alpha_daily_cricket(self):
        r = requests.get(
            f"{API}/fee-catalog/plans/resolve",
            headers=_hdr("admin"),
            params={
                "entity_id": "alpha",
                "kind": "player",
                "player_type": "Daily",
                "sport": "Cricket",
            },
            timeout=15,
        )
        assert r.status_code == 200, r.text
        rates = r.json().get("rates") or {}
        if not rates:
            pytest.skip("No matching ALPHA plan")
        assert rates.get("registration") == 3000
        assert rates.get("monthly") == 2500

    def test_legacy_fees_still_listable(self):
        """Paid/due fee history must remain accessible."""
        people = requests.get(
            f"{API}/people",
            headers=_hdr("principal"),
            params={"kind": "student", "organization": "PWS"},
            timeout=15,
        )
        if people.status_code != 200 or not people.json():
            pytest.skip("No PWS students")
        sid = people.json()[0]["id"]
        dues = requests.get(f"{API}/fees/player-dues/{sid}", headers=_hdr("principal"), timeout=15)
        assert dues.status_code == 200, dues.text
        assert "unpaid" in dues.json()
        assert "paid" in dues.json()

    def test_rate_card_shows_catalogue_flag(self):
        r = requests.get(f"{API}/fees/rate-card", headers=_hdr("principal"), params={"entity": "pws"}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "catalogue_enabled" in data or "Day Scholar" in data or "rates" in data

    def test_super_admin_can_create_catalogue_item(self):
        code = "test_uniform_mvp"
        create = requests.post(
            f"{API}/fee-catalog/items",
            headers=_hdr("super_admin"),
            json={
                "entity_id": "pws",
                "code": code,
                "name": "Test Uniform",
                "fee_type": "uniform",
                "amount": 1500,
                "billing_frequency": "one_time",
                "active": True,
            },
            timeout=15,
        )
        if create.status_code == 409:
            pytest.skip("Item already exists from prior run")
        assert create.status_code == 200, create.text
        assert create.json()["fee_type"] == "uniform"
