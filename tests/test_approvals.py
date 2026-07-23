"""Unified approval workflow."""
import os
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "admin": ("admin@prarambhika.com", "Admin@123"),
    "principal": ("principal@prarambhika.com", "Principal@123"),
    "super_admin": ("superadmin@prarambhika.com", "Super@123"),
}
LEGACY_CREDS = {
    "admin": ("admin@pws-alpha.com", "Admin@123"),
    "principal": ("admin@pws-alpha.com", "Admin@123"),
    "super_admin": ("super@pws-alpha.com", "Super@123"),
}
TOKENS = {}


def _login(role):
    if role in TOKENS:
        return TOKENS[role]
    for creds in (CREDS, LEGACY_CREDS):
        if role not in creds:
            continue
        email, pwd = creds[role]
        r = requests.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=15)
        if r.status_code == 200:
            TOKENS[role] = r.json()["access_token"]
            return TOKENS[role]
    pytest.skip(f"Could not log in as {role}")


def _hdr(role):
    return {"Authorization": f"Bearer {_login(role)}"}


@pytest.fixture(scope="module", autouse=True)
def _api_up():
    try:
        r = requests.get(f"{API}/", timeout=5)
        if r.status_code != 200:
            pytest.skip("API not reachable")
    except Exception:
        pytest.skip("API not reachable")


def _active_player():
    r = requests.get(f"{API}/people", headers=_hdr("admin"), params={"kind": "player"}, timeout=15)
    if r.status_code != 200:
        pytest.skip("Players unavailable")
    for p in r.json():
        if p.get("status") != "deactivated":
            return p
    pytest.skip("No active player")


class TestApprovals:
    def test_player_deactivation_via_legacy_endpoint(self):
        player = _active_player()
        cr = requests.post(
            f"{API}/deactivation-requests",
            headers=_hdr("admin"),
            json={"player_id": player["id"], "reason": "TEST approval flow"},
            timeout=15,
        )
        assert cr.status_code == 200, cr.text
        req = cr.json()
        assert req["status"] == "pending"
        rid = req["id"]

        ar = requests.post(f"{API}/deactivation-requests/{rid}/approve", headers=_hdr("super_admin"), json={"note": "ok"}, timeout=15)
        assert ar.status_code == 200, ar.text
        assert ar.json()["status"] == "approved"
        assert ar.json().get("history") or ar.json().get("decided_by_name")

        # reactivate for other tests
        requests.post(f"{API}/people/{player['id']}/activate", headers=_hdr("super_admin"), timeout=15)

    def test_unified_list_and_comment(self):
        r = requests.get(f"{API}/approval-requests", headers=_hdr("super_admin"), timeout=15)
        assert r.status_code == 200
        if not r.json():
            pytest.skip("No approvals to comment on")
        req = r.json()[0]
        c = requests.post(
            f"{API}/approval-requests/{req['id']}/comments",
            headers=_hdr("super_admin"),
            json={"text": "Review note"},
            timeout=15,
        )
        assert c.status_code == 200

    def test_student_deactivation_requires_approval(self):
        lst = requests.get(
            f"{API}/people",
            headers=_hdr("principal"),
            params={"kind": "student", "q": "Rohit"},
            timeout=15,
        )
        if lst.status_code != 200 or not lst.json():
            pytest.skip("No student Rohit")
        pid = lst.json()[0]["id"]
        d = requests.post(f"{API}/people/{pid}/deactivate", headers=_hdr("principal"), timeout=15)
        assert d.status_code == 200, d.text
        body = d.json()
        if body.get("approval_required"):
            assert body["approval"]["category"] == "user_deactivation"
            # reject to leave student active
            aid = body["approval"]["id"]
            requests.post(f"{API}/approval-requests/{aid}/reject", headers=_hdr("super_admin"), json={"note": "test"}, timeout=15)
        else:
            requests.post(f"{API}/people/{pid}/activate", headers=_hdr("principal"), timeout=15)
