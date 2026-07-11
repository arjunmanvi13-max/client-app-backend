"""Task workflow — visibility, statuses, comments."""
import os
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "admin": ("admin@prarambhika.com", "Admin@123"),
    "teacher": ("teacher@prarambhika.com", "Teacher@123"),
    "principal": ("principal@prarambhika.com", "Principal@123"),
    "super_admin": ("superadmin@prarambhika.com", "Super@123"),
}
LEGACY_CREDS = {
    "admin": ("admin@pws-alpha.com", "Admin@123"),
    "teacher": ("teacher@pws-alpha.com", "Teacher@123"),
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


class TestTaskWorkflow:
    def test_create_with_workflow_fields(self):
        users = requests.get(f"{API}/users?role=teacher", headers=_hdr("admin"), timeout=15)
        assert users.status_code == 200
        teacher_id = users.json()[0]["id"]
        payload = {
            "title": "TEST_workflow task",
            "description": "entity scoped",
            "entity_id": "pws",
            "priority": "high",
            "assignee_id": teacher_id,
            "due_date": "2026-12-31T00:00:00Z",
        }
        r = requests.post(f"{API}/tasks", headers=_hdr("admin"), json=payload, timeout=15)
        assert r.status_code == 200, r.text
        t = r.json()
        assert t["status"] == "open"
        assert t["entity_id"] == "pws"
        assert t["assignee_id"] == teacher_id
        assert t["due_date"]

    def test_teacher_visibility_mine_only(self):
        r = requests.get(f"{API}/tasks", headers=_hdr("teacher"), timeout=15)
        assert r.status_code == 200
        for t in r.json():
            uid = requests.get(f"{API}/auth/me", headers=_hdr("teacher"), timeout=15).json()["id"]
            assert t.get("created_by") == uid or uid in (t.get("assignee_ids") or []) or t.get("assignee_id") == uid

    def test_principal_supervise_sees_all(self):
        admin_list = requests.get(f"{API}/tasks", headers=_hdr("admin"), timeout=15)
        principal_list = requests.get(f"{API}/tasks", headers=_hdr("principal"), timeout=15)
        assert admin_list.status_code == 200
        assert principal_list.status_code == 200
        assert len(principal_list.json()) >= len(admin_list.json())

    def test_status_lifecycle_and_completion_date(self):
        r = requests.post(
            f"{API}/tasks",
            headers=_hdr("admin"),
            json={"title": "TEST_status lifecycle", "description": "x", "priority": "low"},
            timeout=15,
        )
        assert r.status_code == 200
        tid = r.json()["id"]
        rp = requests.patch(f"{API}/tasks/{tid}", headers=_hdr("admin"), json={"status": "completed"}, timeout=15)
        assert rp.status_code == 200
        assert rp.json()["status"] == "completed"
        assert rp.json().get("completed_at")

    def test_comment_and_blocked_status(self):
        r = requests.post(
            f"{API}/tasks",
            headers=_hdr("admin"),
            json={"title": "TEST_blocked", "description": "blocked path"},
            timeout=15,
        )
        tid = r.json()["id"]
        rc = requests.post(f"{API}/tasks/{tid}/comments", headers=_hdr("admin"), json={"text": "blocked reason"}, timeout=15)
        assert rc.status_code == 200
        rb = requests.patch(f"{API}/tasks/{tid}", headers=_hdr("admin"), json={"status": "blocked"}, timeout=15)
        assert rb.status_code == 200
        assert rb.json()["status"] == "blocked"
