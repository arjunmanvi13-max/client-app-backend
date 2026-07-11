"""Backend tests for the new permission system & people CRUD (iteration 2)."""
import os
import uuid
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "https://unified-track.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "super_admin": ("super@pws-alpha.com", "Super@123"),
    "admin": ("admin@pws-alpha.com", "Admin@123"),
    "teacher": ("teacher@pws-alpha.com", "Teacher@123"),
    "coach": ("coach@pws-alpha.com", "Coach@123"),
    "warden": ("warden@pws-alpha.com", "Warden@123"),
    "student": ("student@pws-alpha.com", "Student@123"),
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


# /api/auth/me must return can_manage
class TestAuthMeCanManage:
    def test_admin_has_all_kinds(self):
        r = requests.get(f"{API}/auth/me", headers=_hdr("admin"))
        assert r.status_code == 200
        d = r.json()
        assert "can_manage" in d
        assert set(d["can_manage"]) == {"student", "player", "teacher", "coach"}

    def test_coach_default_player_only(self):
        r = requests.get(f"{API}/auth/me", headers=_hdr("coach"))
        d = r.json()
        assert d["can_manage"] == ["player"]

    def test_teacher_default_no_manage(self):
        r = requests.get(f"{API}/auth/me", headers=_hdr("teacher"))
        assert r.json()["can_manage"] == []

    def test_warden_no_default(self):
        r = requests.get(f"{API}/auth/me", headers=_hdr("warden"))
        assert r.json().get("can_manage", []) == []


# /api/users CRUD with permission gating
class TestUserCRUD:
    created_ids: list = []

    def _suffix(self):
        return uuid.uuid4().hex[:8]

    def test_admin_creates_coach_with_can_manage(self):
        sfx = self._suffix()
        payload = {
            "email": f"TEST_coach_{sfx}@x.com", "password": "Pass@123", "name": "TEST Coach",
            "role": "coach", "organization": "ALPHA", "department": "Football",
            "can_manage": ["player", "coach"],
        }
        r = requests.post(f"{API}/users", headers=_hdr("admin"), json=payload)
        assert r.status_code == 200, r.text
        u = r.json()
        assert u["role"] == "coach"
        assert set(u["can_manage"]) == {"player", "coach"}
        assert u["department"] == "Football"
        TestUserCRUD.created_ids.append(u["id"])

        # GET via list to verify persistence
        rl = requests.get(f"{API}/users?role=coach", headers=_hdr("admin"))
        assert any(x["id"] == u["id"] for x in rl.json())

    def test_coach_cannot_create_coach(self):
        # coach default can_manage=['player'] => 403 for role=coach
        payload = {"email": f"TEST_blocked_{self._suffix()}@x.com", "password": "x", "name": "x", "role": "coach"}
        r = requests.post(f"{API}/users", headers=_hdr("coach"), json=payload)
        assert r.status_code == 403

    def test_teacher_can_create_student_user_but_can_manage_dropped(self):
        sfx = self._suffix()
        payload = {
            "email": f"TEST_stud_{sfx}@x.com", "password": "Pass@123", "name": "TEST Stu",
            "role": "student", "can_manage": ["coach"],  # should be dropped (non-admin)
        }
        r = requests.post(f"{API}/users", headers=_hdr("teacher"), json=payload)
        assert r.status_code == 200, r.text
        u = r.json()
        assert u["can_manage"] == []
        TestUserCRUD.created_ids.append(u["id"])

    def test_non_admin_cannot_create_warden(self):
        # warden not in MANAGE_KINDS so requires admin
        payload = {"email": f"TEST_w_{self._suffix()}@x.com", "password": "x", "name": "x", "role": "warden"}
        r = requests.post(f"{API}/users", headers=_hdr("teacher"), json=payload)
        assert r.status_code == 403

    def test_patch_drops_role_and_can_manage_for_non_admin(self):
        # use the coach-created earlier
        assert TestUserCRUD.created_ids
        target = TestUserCRUD.created_ids[0]
        # teacher cannot edit coaches (no can_manage rights)
        r = requests.patch(f"{API}/users/{target}", headers=_hdr("teacher"),
                            json={"name": "Hacked", "role": "admin"})
        assert r.status_code == 403

    def test_admin_patches_user_password_and_phone(self):
        target = TestUserCRUD.created_ids[0]
        r = requests.patch(f"{API}/users/{target}", headers=_hdr("admin"),
                            json={"phone": "9999", "password": "NewPass@1"})
        assert r.status_code == 200
        assert r.json()["phone"] == "9999"
        # verify password change works
        email = r.json()["email"]
        rl = requests.post(f"{API}/auth/login", json={"email": email, "password": "NewPass@1"})
        assert rl.status_code == 200

    def test_coach_self_delete_blocked(self):
        # Need coach's id
        me = requests.get(f"{API}/auth/me", headers=_hdr("coach")).json()
        r = requests.delete(f"{API}/users/{me['id']}", headers=_hdr("coach"))
        assert r.status_code == 400

    def test_admin_delete_user(self):
        # delete the teacher-created student user (role=student in MANAGE_KINDS)
        if len(TestUserCRUD.created_ids) > 1:
            tid = TestUserCRUD.created_ids[1]
            r = requests.delete(f"{API}/users/{tid}", headers=_hdr("admin"))
            assert r.status_code == 200
            TestUserCRUD.created_ids.remove(tid)

    @classmethod
    def teardown_class(cls):
        # cleanup remaining
        for uid in cls.created_ids:
            requests.delete(f"{API}/users/{uid}", headers=_hdr("admin"))


# /api/people CRUD with kind permission gating
class TestPeopleCRUD:
    created_ids: list = []

    def test_coach_create_player_ok(self):
        payload = {"name": "TEST Player A", "kind": "player", "group": "U-15", "sport": "Cricket", "organization": "ALPHA", "is_resident": True}
        r = requests.post(f"{API}/people", headers=_hdr("coach"), json=payload)
        assert r.status_code == 200, r.text
        p = r.json()
        assert p["kind"] == "player" and p["is_resident"] is True
        TestPeopleCRUD.created_ids.append(p["id"])

    def test_coach_create_student_forbidden(self):
        payload = {"name": "TEST Stu B", "kind": "student", "group": "9-A"}
        r = requests.post(f"{API}/people", headers=_hdr("coach"), json=payload)
        assert r.status_code == 403

    def test_teacher_create_student_forbidden(self):
        payload = {"name": "TEST Stu C", "kind": "student", "group": "10-X", "organization": "PWS"}
        r = requests.post(f"{API}/people", headers=_hdr("teacher"), json=payload)
        assert r.status_code == 403

    def test_admin_patch_player(self):
        pid = TestPeopleCRUD.created_ids[0]
        r = requests.patch(f"{API}/people/{pid}", headers=_hdr("admin"),
                            json={"name": "TEST Player A Renamed", "is_resident": False})
        assert r.status_code == 200
        assert r.json()["name"] == "TEST Player A Renamed"
        assert r.json()["is_resident"] is False

    def test_teacher_patch_player_forbidden(self):
        pid = TestPeopleCRUD.created_ids[0]
        r = requests.patch(f"{API}/people/{pid}", headers=_hdr("teacher"),
                            json={"name": "x"})
        assert r.status_code == 403

    def test_delete_people(self):
        for pid in list(TestPeopleCRUD.created_ids):
            r = requests.delete(f"{API}/people/{pid}", headers=_hdr("admin"))
            assert r.status_code == 200
            TestPeopleCRUD.created_ids.remove(pid)
            # verify gone
            rl = requests.get(f"{API}/people", headers=_hdr("admin"))
            assert all(x["id"] != pid for x in rl.json())

    @classmethod
    def teardown_class(cls):
        for pid in cls.created_ids:
            requests.delete(f"{API}/people/{pid}", headers=_hdr("admin"))
