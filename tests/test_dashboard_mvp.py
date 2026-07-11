"""Dashboard MVP — role-based tiles via GET /dashboard/mvp."""
import os
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "super_admin": ("superadmin@prarambhika.com", "Super@123"),
    "admin": ("admin@prarambhika.com", "Admin@123"),
    "teacher": ("teacher@prarambhika.com", "Teacher@123"),
    "coach": ("coach@prarambhika.com", "Coach@123"),
}
TOKENS = {}


def _login(role):
    if role in TOKENS:
        return TOKENS[role]
    if role not in CREDS:
        pytest.skip(f"No creds for {role}")
    email, pwd = CREDS[role]
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=15)
    if r.status_code != 200:
        pytest.skip(f"Could not log in as {role}")
    TOKENS[role] = r.json()["access_token"]
    return TOKENS[role]


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


class TestSuperAdminDashboard:
    def test_combined_mvp(self):
        r = requests.get(f"{API}/dashboard/mvp", headers=_hdr("super_admin"), params={"entity": "both"}, timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["role"] == "super_admin"
        assert "active_people" in d
        assert "attendance_today" in d
        assert "fees_collected_today" in d
        assert "outstanding_invoices" in d
        assert "pending_approvals" in d
        assert "open_tasks" in d

    def test_pws_entity_filter(self):
        r = requests.get(f"{API}/dashboard/mvp", headers=_hdr("super_admin"), params={"entity": "pws"}, timeout=20)
        assert r.status_code == 200
        assert r.json()["entity"] == "PWS"


class TestTeacherDashboard:
    def test_teacher_mvp(self):
        r = requests.get(f"{API}/dashboard/mvp", headers=_hdr("teacher"), timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["role"] == "teacher"
        assert "assigned_classes" in d
        assert "attendance_today" in d
        assert "pending_marks_entry" in d
        assert "recent_notifications" in d


class TestCoachDashboard:
    def test_coach_mvp(self):
        r = requests.get(f"{API}/dashboard/mvp", headers=_hdr("coach"), timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["role"] == "coach"
        assert "assigned_centres" in d
        assert "attendance_today" in d
        assert "pending_assessments" in d
