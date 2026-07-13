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


# /api/users CRUD — Super Admin only for login user provisioning
class TestUserCRUD:
    created_ids: list = []

    def _suffix(self):
        return uuid.uuid4().hex[:8]

    def test_super_admin_creates_alpha_coach(self):
        sfx = self._suffix()
        payload = {
            "email": f"TEST_coach_{sfx}@prarambhika.com",
            "password": "Pass@123",
            "name": "TEST Coach",
            "user_type": "alpha_coach",
            "assigned_sports": ["Football"],
            "assigned_centres": ["Balua"],
        }
        r = requests.post(f"{API}/users", headers=_hdr("super_admin"), json=payload)
        assert r.status_code == 200, r.text
        u = r.json()
        assert u["user_type"] == "alpha_coach"
        assert u["role"] == "coach"
        assert u["assigned_sports"] == ["Football"]
        TestUserCRUD.created_ids.append(u["id"])

        rl = requests.get(f"{API}/users?user_type=alpha_coach", headers=_hdr("super_admin"))
        assert any(x["id"] == u["id"] for x in rl.json())

    def test_admin_cannot_create_login_user(self):
        payload = {
            "email": f"TEST_blocked_{self._suffix()}@prarambhika.com",
            "password": "Pass@123",
            "name": "x",
            "user_type": "alpha_coach",
            "assigned_sports": ["Cricket"],
        }
        r = requests.post(f"{API}/users", headers=_hdr("admin"), json=payload)
        assert r.status_code == 403

    def test_coach_cannot_create_login_user(self):
        payload = {
            "email": f"TEST_blocked_{self._suffix()}@prarambhika.com",
            "password": "Pass@123",
            "name": "x",
            "user_type": "alpha_coach",
            "assigned_sports": ["Cricket"],
        }
        r = requests.post(f"{API}/users", headers=_hdr("coach"), json=payload)
        assert r.status_code == 403

    def test_rejects_unapproved_user_type(self):
        payload = {
            "email": f"TEST_parent_{self._suffix()}@prarambhika.com",
            "password": "Pass@123",
            "name": "Parent",
            "user_type": "parent",
        }
        r = requests.post(f"{API}/users", headers=_hdr("super_admin"), json=payload)
        assert r.status_code == 422

    def test_classification_catalog_has_seven_types(self):
        r = requests.get(f"{API}/users/classification", headers=_hdr("super_admin"))
        assert r.status_code == 200
        data = r.json()
        assert len(data["userTypes"]) == 7
        assert len(data["approvedCodes"]) == 7

    def test_teacher_cannot_patch_login_user(self):
        assert TestUserCRUD.created_ids
        target = TestUserCRUD.created_ids[0]
        r = requests.patch(
            f"{API}/users/{target}",
            headers=_hdr("teacher"),
            json={"name": "Hacked", "user_type": "super_admin"},
        )
        assert r.status_code == 403

    def test_super_admin_patches_user_phone(self):
        target = TestUserCRUD.created_ids[0]
        r = requests.patch(
            f"{API}/users/{target}",
            headers=_hdr("super_admin"),
            json={"phone": "9999"},
        )
        assert r.status_code == 200
        assert r.json()["phone"] == "9999"

    def test_coach_self_delete_blocked(self):
        me = requests.get(f"{API}/auth/me", headers=_hdr("coach")).json()
        r = requests.delete(f"{API}/users/{me['id']}", headers=_hdr("coach"))
        assert r.status_code == 403

    @classmethod
    def teardown_class(cls):
        for uid in cls.created_ids:
            requests.delete(f"{API}/users/{uid}", headers=_hdr("super_admin"))


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
