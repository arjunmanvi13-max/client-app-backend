"""Tests for STAFF <-> USER (Permissions module) auto-sync.

Covers:
  1. Backfill: existing 6 seeded staff + Sonu Kumar (7) have linked users.
  2. Create → linked user auto-created with expected email + department.
  3. Patch (name/group) → linked user updates.
  4. Deactivate → linked user 'deactivated' + login blocked. Activate → active + login works.
  5. Staff can login (must_change_password=true) and permissions PATCH persists.
  6. Idempotency: restart backend, staff accounts stable, no duplicates.
"""
import os
import time
import uuid
import subprocess
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "").rstrip("/")
assert BASE, "EXPO_PUBLIC_BACKEND_URL must be set"
API = f"{BASE}/api"

SUPER = ("superadmin@prarambhika.com", "Super@123")


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    return r


@pytest.fixture(scope="module")
def super_token():
    r = _login(*SUPER)
    assert r.status_code == 200, f"Super login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def super_hdr(super_token):
    return {"Authorization": f"Bearer {super_token}"}


@pytest.fixture(scope="module")
def state():
    return {}


# ---------- Backfill: staff-linked users ----------

class TestBackfill:
    def test_super_login_and_users_list(self, super_hdr, state):
        r = requests.get(f"{API}/users", headers=super_hdr, timeout=20)
        assert r.status_code == 200, r.text
        users = r.json()
        state["users"] = users
        # every user must have id + role
        assert all("id" in u and "role" in u for u in users)

    def test_staff_linked_users_exist(self, state):
        staff_users = [u for u in state["users"] if u.get("role") == "staff" and u.get("person_id")]
        state["staff_users"] = staff_users
        assert len(staff_users) >= 7, f"Expected >=7 staff-linked users, got {len(staff_users)}: {[u['email'] for u in staff_users]}"

    def test_sonu_kumar_present(self, state):
        sonu = next((u for u in state["staff_users"] if u.get("email") == "sonu.kumar@prarambhika.com"), None)
        assert sonu, f"Sonu Kumar staff-linked user missing. Emails: {[u['email'] for u in state['staff_users']]}"
        state["sonu"] = sonu
        assert sonu["role"] == "staff"
        assert sonu.get("organization") == "ALPHA", f"Expected ALPHA org, got {sonu.get('organization')}"
        assert sonu.get("status") in (None, "active"), sonu.get("status")
        assert sonu.get("must_change_password") is True
        assert sonu.get("person_id"), "person_id link missing"


# ---------- Staff login & permission PATCH ----------

class TestStaffLoginAndPerms:
    def test_sonu_can_login(self, state):
        r = _login("sonu.kumar@prarambhika.com", "Staff@123")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("must_change_password") is True
        assert d.get("access_token")

    def test_patch_permissions_persists(self, super_hdr, state):
        sonu_id = state["sonu"]["id"]
        # Snapshot original permissions for cleanup
        get_r = requests.get(f"{API}/users", headers=super_hdr).json()
        current = next((u for u in get_r if u["id"] == sonu_id), None)
        state["sonu_original_perms"] = current.get("permissions") if current else None
        new_perms = dict(state["sonu_original_perms"] or {})
        # toggle a valid known key (flip existing to force write)
        orig_val = bool(new_perms.get("view_players", False))
        new_perms["view_players"] = not orig_val
        state["sonu_toggled_val"] = not orig_val
        r = requests.patch(
            f"{API}/users/{sonu_id}/permissions",
            headers=super_hdr,
            json={"permissions": new_perms},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        # verify
        rl = requests.get(f"{API}/users", headers=super_hdr).json()
        after = next((u for u in rl if u["id"] == sonu_id), None)
        assert after["permissions"].get("view_players") is state["sonu_toggled_val"]


# ---------- Sync-on-create + patch + deactivate ----------

class TestSyncOnCreate:
    def test_create_staff_person_creates_linked_user(self, super_hdr, state):
        payload = {"name": "Perm Sync Test", "kind": "staff", "organization": "PWS", "group": "Cleaner"}
        r = requests.post(f"{API}/people", headers=super_hdr, json=payload, timeout=15)
        assert r.status_code == 200, r.text
        person = r.json()
        assert person["kind"] == "staff"
        state["test_person"] = person

        users = requests.get(f"{API}/users", headers=super_hdr).json()
        linked = [u for u in users if u.get("person_id") == person["id"]]
        assert len(linked) == 1, f"Expected 1 linked user, got {len(linked)}"
        u = linked[0]
        assert u["email"] == "perm.sync.test@prarambhika.com"
        assert u["role"] == "staff"
        assert u.get("department") == "Cleaner"
        assert u.get("organization") == "PWS"
        state["test_user"] = u

    def test_patch_person_updates_user(self, super_hdr, state):
        pid = state["test_person"]["id"]
        r = requests.patch(
            f"{API}/people/{pid}",
            headers=super_hdr,
            json={"name": "Perm Sync Test Renamed", "group": "Head Cleaner"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        users = requests.get(f"{API}/users", headers=super_hdr).json()
        u = next(x for x in users if x.get("person_id") == pid)
        assert u["name"] == "Perm Sync Test Renamed"
        assert u["department"] == "Head Cleaner"

    def test_deactivate_person_deactivates_user_and_blocks_login(self, super_hdr, state):
        pid = state["test_person"]["id"]
        r = requests.post(f"{API}/people/{pid}/deactivate", headers=super_hdr, timeout=15)
        assert r.status_code == 200, r.text
        users = requests.get(f"{API}/users", headers=super_hdr).json()
        u = next(x for x in users if x.get("person_id") == pid)
        assert u["status"] == "deactivated"
        # login blocked
        rl = _login("perm.sync.test@prarambhika.com", "Staff@123")
        assert rl.status_code == 403, f"Deactivated user login should be 403, got {rl.status_code} {rl.text}"

    def test_reactivate_person_reactivates_user(self, super_hdr, state):
        pid = state["test_person"]["id"]
        r = requests.post(f"{API}/people/{pid}/activate", headers=super_hdr, timeout=15)
        assert r.status_code == 200, r.text
        users = requests.get(f"{API}/users", headers=super_hdr).json()
        u = next(x for x in users if x.get("person_id") == pid)
        assert u["status"] == "active"
        rl = _login("perm.sync.test@prarambhika.com", "Staff@123")
        assert rl.status_code == 200, f"Reactivated user login failed: {rl.status_code} {rl.text}"


# ---------- Idempotency after restart ----------

class TestIdempotency:
    def test_restart_backend_and_no_duplicates(self, super_hdr, state):
        # Snapshot staff-linked users
        before = {u["id"]: u for u in requests.get(f"{API}/users", headers=super_hdr).json()
                  if u.get("role") == "staff" and u.get("person_id")}
        assert before, "no staff users before restart"
        try:
            subprocess.run(["sudo", "supervisorctl", "restart", "backend"], check=True, timeout=30)
        except Exception as e:
            pytest.skip(f"Cannot restart backend: {e}")
        # wait for restart
        for _ in range(20):
            time.sleep(1)
            try:
                pr = requests.post(f"{API}/auth/login", json={"email": SUPER[0], "password": SUPER[1]}, timeout=5)
                if pr.status_code == 200:
                    break
            except Exception:
                continue
        time.sleep(2)
        token = pr.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}
        after_users = requests.get(f"{API}/users", headers=hdr).json()
        after = {u["id"]: u for u in after_users if u.get("role") == "staff" and u.get("person_id")}
        # no purge
        missing = set(before) - set(after)
        assert not missing, f"Staff users purged after restart: {missing}"
        # no duplicates per person_id
        person_ids = [u["person_id"] for u in after.values()]
        assert len(person_ids) == len(set(person_ids)), "Duplicate staff-user rows for same person_id"

    def test_cleanup_test_person(self, super_hdr, state):
        pid = state.get("test_person", {}).get("id")
        uid = state.get("test_user", {}).get("id")
        if pid:
            requests.delete(f"{API}/people/{pid}", headers=super_hdr)
        if uid:
            requests.delete(f"{API}/users/{uid}", headers=super_hdr)
        # verify person gone
        if pid:
            users = requests.get(f"{API}/users", headers=super_hdr).json()
            assert not any(u.get("person_id") == pid for u in users), "linked user still present after cleanup"

    def test_revert_sonu_permissions(self, super_hdr, state):
        sonu_id = state.get("sonu", {}).get("id")
        original = state.get("sonu_original_perms")
        if not sonu_id:
            pytest.skip("no sonu id")
        # revert
        r = requests.patch(
            f"{API}/users/{sonu_id}/permissions",
            headers=super_hdr,
            json={"permissions": original or {}},
            timeout=15,
        )
        assert r.status_code == 200, r.text
