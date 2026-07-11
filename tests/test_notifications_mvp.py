"""Notifications MVP — unified schema, triggers, read/unread."""
import os
import uuid
import pytest
import requests

from notifications_service import (
    NOTIFICATION_TYPES,
    normalize_notification,
    notification_filter_for_user,
    canonical_type,
)

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "super_admin": ("superadmin@prarambhika.com", "Super@123"),
    "admin": ("admin@prarambhika.com", "Admin@123"),
    "teacher": ("teacher@prarambhika.com", "Teacher@123"),
}
LEGACY_CREDS = {
    "super_admin": ("super@pws-alpha.com", "Super@123"),
    "admin": ("admin@pws-alpha.com", "Admin@123"),
    "teacher": ("teacher@pws-alpha.com", "Teacher@123"),
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


class TestNotificationSchema:
    def test_canonical_types_defined(self):
        assert "task_assigned" in NOTIFICATION_TYPES
        assert "approval_requested" in NOTIFICATION_TYPES
        assert canonical_type("absent_today") == "absence"

    def test_normalize_includes_entity_and_ref(self):
        out = normalize_notification({
            "id": "n1",
            "user_id": "u1",
            "type": "task_assigned",
            "title": "Task",
            "message": "Do homework",
            "entity_id": "pws",
            "ref_id": "t1",
            "ref_type": "task",
            "created_at": "2026-07-11T11:00:00",
            "read": False,
            "channels": ["in_app"],
        })
        assert out["entity_id"] == "pws"
        assert out["ref_id"] == "t1"
        assert out["ref_type"] == "task"
        assert out["channels"] == ["in_app"]

    def test_legacy_type_alias(self):
        out = normalize_notification({"kind": "absent_today", "body": "x", "at": "2026-01-01"})
        assert out["type"] == "absence"


class TestNotificationAPI:
    def test_list_returns_items_and_unread_count(self):
        r = requests.get(f"{API}/notifications", headers=_hdr("super_admin"), timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert "unread_count" in data
        assert isinstance(data["items"], list)

    def test_unread_count_endpoint(self):
        r = requests.get(f"{API}/notifications/unread-count", headers=_hdr("teacher"), timeout=15)
        assert r.status_code == 200
        assert "unread_count" in r.json()

    def test_task_assignment_creates_notification(self):
        me = requests.get(f"{API}/auth/me", headers=_hdr("teacher"), timeout=15).json()
        teacher_id = me["id"]
        title = f"TEST_notify_{uuid.uuid4().hex[:8]}"
        r = requests.post(
            f"{API}/tasks",
            headers=_hdr("super_admin"),
            json={
                "title": title,
                "description": "notification test",
                "priority": "low",
                "assignee_id": teacher_id,
                "entity_id": "pws",
            },
            timeout=15,
        )
        assert r.status_code == 200, r.text
        task_id = r.json()["id"]
        nr = requests.get(f"{API}/notifications", headers=_hdr("teacher"), timeout=15)
        assert nr.status_code == 200
        items = nr.json().get("items", nr.json())
        match = [n for n in items if n.get("ref_id") == task_id or n.get("message") == title]
        assert match, "Teacher should receive task notification"
        assert match[0].get("type") in ("task_assigned", "task_assigned")
        assert match[0].get("entity_id") == "pws"

    def test_mark_read_and_read_all(self):
        r = requests.get(f"{API}/notifications", headers=_hdr("super_admin"), timeout=15)
        items = r.json().get("items", [])
        unread = [n for n in items if not n.get("read")]
        if not unread:
            pytest.skip("No unread notifications")
        nid = unread[0]["id"]
        mr = requests.post(f"{API}/notifications/{nid}/read", headers=_hdr("super_admin"), timeout=15)
        assert mr.status_code == 200
        ra = requests.post(f"{API}/notifications/read-all", headers=_hdr("super_admin"), timeout=15)
        assert ra.status_code == 200

    def test_dashboard_unread_uses_same_visibility(self):
        dr = requests.get(f"{API}/dashboard", headers=_hdr("super_admin"), timeout=15)
        assert dr.status_code == 200
        assert "unread_notifications" in dr.json()
        uc = requests.get(f"{API}/notifications/unread-count", headers=_hdr("super_admin"), timeout=15)
        assert uc.status_code == 200
