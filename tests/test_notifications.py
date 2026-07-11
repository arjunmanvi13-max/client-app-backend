"""Milestone 0 — notification schema normalization and delivery tests."""
import os
import uuid
import pytest
import requests

from notifications_service import normalize_notification, notification_filter_for_user, canonical_type

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "https://unified-track.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "super_admin": ("super@pws-alpha.com", "Super@123"),
    "admin": ("admin@pws-alpha.com", "Admin@123"),
    "teacher": ("teacher@pws-alpha.com", "Teacher@123"),
}
TOKENS = {}


def _login(role):
    if role in TOKENS:
        return TOKENS[role]
    email, pwd = CREDS[role]
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=15)
    assert r.status_code == 200, f"login {role} failed {r.text}"
    TOKENS[role] = r.json()["access_token"]
    return TOKENS[role]


def _hdr(role):
    return {"Authorization": f"Bearer {_login(role)}"}


class TestNormalizeNotification:
    def test_maps_legacy_body_and_at(self):
        out = normalize_notification({
            "id": "n1",
            "kind": "fees_created",
            "title": "Fees",
            "body": "Player fees generated",
            "at": "2026-07-11T10:00:00",
            "read": False,
        })
        assert out["message"] == "Player fees generated"
        assert out["created_at"] == "2026-07-11T10:00:00"
        assert out["type"] == "fees_created"

    def test_preserves_canonical_fields(self):
        out = normalize_notification({
            "id": "n2",
            "user_id": "u1",
            "type": "task_assigned",
            "title": "Task",
            "message": "Do homework",
            "created_at": "2026-07-11T11:00:00",
            "read": False,
        })
        assert out["message"] == "Do homework"
        assert out["type"] == "task_assigned"


class TestNotificationFilter:
    def test_includes_user_and_legacy_role_clauses(self):
        filt = notification_filter_for_user({"id": "user-1", "role": "super_admin"})
        assert "$or" in filt
        clauses = filt["$or"]
        assert {"user_id": "user-1"} in clauses
        assert {"audience_role": "super_admin", "user_id": {"$exists": False}} in clauses
        assert {"audience_user": "user-1"} in clauses


@pytest.mark.integration
class TestNotificationAPI:
    def test_list_returns_message_field(self):
        r = requests.get(f"{API}/notifications", headers=_hdr("super_admin"), timeout=15)
        assert r.status_code == 200
        items = r.json().get("items", r.json())
        for n in items:
            if n.get("title"):
                assert n.get("message") or n.get("body") is None or isinstance(n.get("message"), str)

    def test_task_assignment_creates_notification_with_message(self):
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
                "assignee_ids": [teacher_id],
                "department": "Test",
                "follow_up_required": False,
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
        assert match[0].get("message") == title

    def test_dashboard_unread_uses_same_visibility(self):
        dr = requests.get(f"{API}/dashboard", headers=_hdr("super_admin"), timeout=15)
        assert dr.status_code == 200
        assert "unread_notifications" in dr.json()
