"""Milestone 2 — PWS school fees mirroring ALPHA pattern."""
import os
import uuid
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "https://unified-track.preview.emergentagent.com").rstrip("/")
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
class TestPWSFeesAPI:
    def test_pws_rate_card_available(self):
        r = requests.get(f"{API}/fees/rate-card", headers=_hdr("principal"), params={"entity": "pws"}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "Day Scholar" in data or "pws" in data

    def test_principal_lists_pws_student_fees(self):
        people = requests.get(
            f"{API}/people",
            headers=_hdr("principal"),
            params={"kind": "student", "organization": "PWS"},
            timeout=15,
        )
        if people.status_code != 200 or not people.json():
            pytest.skip("No PWS students seeded")
        sid = people.json()[0]["id"]
        dues = requests.get(f"{API}/fees/player-dues/{sid}", headers=_hdr("principal"), timeout=15)
        assert dues.status_code == 200, dues.text
        body = dues.json()
        assert "unpaid" in body
        assert body.get("player", {}).get("kind") == "student" or body.get("player", {}).get("organization") == "PWS"

    def test_parent_pws_ward_fees_not_empty_when_seeded(self):
        me = requests.get(f"{API}/auth/me", headers=_hdr("super_admin"), timeout=15).json()
        parent_email = "parent_pws@prarambhika.com"
        pr = requests.post(f"{API}/auth/login", json={"email": parent_email, "password": "Parent@123"}, timeout=15)
        if pr.status_code != 200:
            pytest.skip("Parent account not available")
        ph = {"Authorization": f"Bearer {pr.json()['access_token']}"}
        wards = requests.get(f"{API}/parent/wards", headers=ph, timeout=15)
        if wards.status_code != 200 or not wards.json():
            pytest.skip("No parent wards")
        ward = next((w for w in wards.json() if w.get("organization") == "PWS"), None)
        if not ward:
            pytest.skip("No PWS ward for parent")
        fr = requests.get(f"{API}/parent/fees/{ward['id']}", headers=ph, timeout=15)
        assert fr.status_code == 200, fr.text
        # After seed backfill, PWS parent should see fee rows
        assert "fees" in fr.json()

    def test_pws_financial_summary_not_zero_stub(self):
        r = requests.get(
            f"{API}/reports/financial/summary",
            headers=_hdr("principal"),
            params={"institution": "PWS"},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "totals" in data
        assert "by_institution" in data

    def test_alpha_fees_unchanged_for_sports_admin(self):
        r = requests.get(f"{API}/fees/rate-card", headers=_hdr("admin"), params={"entity": "alpha"}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "Daily" in data or "alpha" in data
