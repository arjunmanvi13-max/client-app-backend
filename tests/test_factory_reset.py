"""Factory reset — the guards matter more than the happy path.

These cover refusal and preview only. The destructive path is deliberately NOT
exercised here: a real wipe against the shared dev database would destroy the
fixtures every other test depends on. Verify it against a throwaway database:

    DB_NAME=pws_alpha_resettest SEED_DEMO_DATA=1         .venv/Scripts/python.exe -m uvicorn server:app --port 8001
    EXPO_PUBLIC_BACKEND_URL=http://127.0.0.1:8001 python scripts/verify_factory_reset.py
"""
import os

import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/") + "/api"
SUPER = ("superadmin@prarambhika.com", "Super@123")
PHRASE = "DELETE ALL DATA"

CREDS = {
    "super_admin": SUPER,
    "principal": ("principal@prarambhika.com", "Principal@123"),
    "admin": ("admin@prarambhika.com", "Admin@123"),
    "teacher": ("teacher@prarambhika.com", "Teacher@123"),
}


def _token(role):
    email, pwd = CREDS[role]
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": pwd}, timeout=20)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _hdr(role):
    return {"Authorization": f"Bearer {_token(role)}", "Content-Type": "application/json"}


def _post(role, body, dry_run=True):
    return requests.post(
        f"{BASE}/admin/factory-reset",
        headers=_hdr(role),
        params={"dry_run": str(dry_run).lower()},
        json=body,
        timeout=60,
    )


def _valid_body(**over):
    body = {"password": SUPER[1], "confirmation": PHRASE}
    body.update(over)
    return body


class TestFactoryResetGuards:
    @pytest.mark.parametrize("role", ["principal", "admin", "teacher"])
    def test_non_super_admin_is_refused(self, role):
        r = _post(role, _valid_body())
        assert r.status_code == 403, r.text
        assert "Super Admin" in r.text

    def test_unauthenticated_is_refused(self):
        r = requests.post(
            f"{BASE}/admin/factory-reset",
            params={"dry_run": "true"},
            json=_valid_body(),
            timeout=30,
        )
        assert r.status_code in (401, 403), r.text

    def test_wrong_password_is_refused(self):
        r = _post("super_admin", _valid_body(password="not-the-password"))
        assert r.status_code == 403, r.text
        assert "Password is incorrect" in r.text

    def test_missing_confirmation_phrase_is_refused(self):
        r = _post("super_admin", _valid_body(confirmation="yes please"))
        assert r.status_code == 400, r.text
        assert PHRASE in r.text

    def test_confirmation_is_case_sensitive(self):
        r = _post("super_admin", _valid_body(confirmation="delete all data"))
        assert r.status_code == 400, r.text

    def test_password_is_required(self):
        r = _post("super_admin", {"confirmation": PHRASE})
        assert r.status_code == 422, r.text

    def test_wrong_password_is_rejected_before_anything_is_counted(self):
        """A bad password must not leak how much data exists."""
        r = _post("super_admin", _valid_body(password="wrong"))
        assert r.status_code == 403
        assert "counts" not in r.text


class TestFactoryResetPreview:
    def test_dry_run_reports_without_deleting(self):
        before = requests.get(f"{BASE}/people?kind=student", headers=_hdr("super_admin"), timeout=30)
        assert before.status_code == 200
        rows = before.json()
        rows = rows if isinstance(rows, list) else rows.get("data", [])
        assert rows, "need seeded data for a meaningful preview"

        r = _post("super_admin", _valid_body(), dry_run=True)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "preview"
        assert body["dry_run"] is True
        assert body["total_documents"] > 0
        assert body["counts"].get("people", 0) > 0
        assert body["super_admins_kept"] >= 1

        after = requests.get(f"{BASE}/people?kind=student", headers=_hdr("super_admin"), timeout=30)
        after_rows = after.json()
        after_rows = after_rows if isinstance(after_rows, list) else after_rows.get("data", [])
        assert len(after_rows) == len(rows), "a dry run must not delete anything"

    def test_preview_never_lists_preserved_collections(self):
        r = _post("super_admin", _valid_body(), dry_run=True)
        assert r.status_code == 200
        body = r.json()
        for name in body["preserved_collections"]:
            assert name not in body["counts"], f"{name} must never be cleared"
        assert "factory_reset_audit" in body["preserved_collections"]

    def test_scope_flags_shrink_the_blast_radius(self):
        full = _post("super_admin", _valid_body(), dry_run=True).json()
        narrow = _post(
            "super_admin",
            _valid_body(
                include_academic_structure=False,
                include_finance_setup=False,
                include_login_accounts=False,
            ),
            dry_run=True,
        ).json()
        assert narrow["total_documents"] <= full["total_documents"]
        assert "users" not in narrow["counts"]
        for name in ("academic_years", "grades", "subjects"):
            assert name not in narrow["counts"]
        for name in ("fee_catalogue", "fee_plans"):
            assert name not in narrow["counts"]

    @pytest.mark.asyncio
    async def test_super_admins_are_never_counted_for_deletion(self):
        """GET /users is filtered and paginated — compare against the collection."""
        from core import db

        body = _post("super_admin", _valid_body(), dry_run=True).json()
        total = await db.users.count_documents({})
        supers = await db.users.count_documents({"role": "super_admin"})
        assert supers >= 1
        assert body["super_admins_kept"] == supers
        assert body["counts"].get("users", 0) == total - supers


class TestFactoryResetHistory:
    def test_history_is_super_admin_only(self):
        assert requests.get(f"{BASE}/admin/factory-reset/history",
                            headers=_hdr("principal"), timeout=30).status_code == 403
        r = requests.get(f"{BASE}/admin/factory-reset/history",
                         headers=_hdr("super_admin"), timeout=30)
        assert r.status_code == 200
        assert "resets" in r.json()
