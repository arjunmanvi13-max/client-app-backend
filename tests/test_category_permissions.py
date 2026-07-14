"""Tests for category-based module permissions API."""
import os
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE}/api"

SUPER = ("superadmin@prarambhika.com", "Super@123")
TOKEN = None


def _login():
    global TOKEN
    if TOKEN:
        return TOKEN
    r = requests.post(f"{API}/auth/login", json={"email": SUPER[0], "password": SUPER[1]}, timeout=15)
    if r.status_code != 200:
        pytest.skip("Could not log in as super admin")
    TOKEN = r.json()["access_token"]
    return TOKEN


def _hdr():
    return {"Authorization": f"Bearer {_login()}"}


@pytest.fixture(scope="module", autouse=True)
def _api_up():
    try:
        r = requests.get(f"{API}/", timeout=5)
        if r.status_code != 200:
            pytest.skip("API not reachable")
    except Exception:
        pytest.skip("API not reachable")


class TestCategoryPermissions:
    def test_list_categories_returns_seven(self):
        r = requests.get(f"{API}/permissions/categories", headers=_hdr(), timeout=15)
        assert r.status_code == 200, r.text
        cats = r.json()["categories"]
        assert len(cats) == 7
        codes = {c["user_type"] for c in cats}
        names = {c["display_name"] for c in cats}
        assert "super_admin" in codes
        assert "pws_teacher" in codes
        assert "alpha_coach" in codes
        assert "PWS Teachers" in names
        assert "ALPHA Coaches" in names
        for c in cats:
            assert "active_user_count" in c
            assert isinstance(c["active_user_count"], int)

    def test_super_admin_category_is_locked(self):
        r = requests.get(f"{API}/permissions/categories/super_admin", headers=_hdr(), timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body["locked"] is True
        assert all(body["modules"].values())

    def test_pws_teacher_has_catalog_groups(self):
        r = requests.get(f"{API}/permissions/categories/pws_teacher", headers=_hdr(), timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body["locked"] is False
        assert len(body["catalog"]) >= 1
        group_ids = {g["id"] for g in body["catalog"]}
        assert "operations" in group_ids or "academics" in group_ids

    def test_save_and_restore_pws_teacher_modules(self):
        get_r = requests.get(f"{API}/permissions/categories/pws_teacher", headers=_hdr(), timeout=15)
        assert get_r.status_code == 200
        original = get_r.json()["modules"]

        toggled = dict(original)
        toggled["reports"] = not original.get("reports", False)
        put_r = requests.put(
            f"{API}/permissions/categories/pws_teacher",
            headers=_hdr(),
            json={"modules": toggled},
            timeout=20,
        )
        assert put_r.status_code == 200, put_r.text
        saved = put_r.json()
        assert saved["modules"]["reports"] == toggled["reports"]
        assert "users_updated" in saved

        # Restore
        restore_r = requests.put(
            f"{API}/permissions/categories/pws_teacher",
            headers=_hdr(),
            json={"modules": original},
            timeout=20,
        )
        assert restore_r.status_code == 200

    def test_cannot_modify_super_admin_category(self):
        r = requests.put(
            f"{API}/permissions/categories/super_admin",
            headers=_hdr(),
            json={"modules": {"dashboard": False}},
            timeout=15,
        )
        assert r.status_code == 400

    def test_non_super_admin_denied(self):
        r = requests.get(f"{API}/permissions/categories", timeout=15)
        assert r.status_code in (401, 403)
