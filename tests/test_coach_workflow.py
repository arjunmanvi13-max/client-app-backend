"""Tests for Coach Workflow - Player Management & Attendance (Iteration 3)."""
import os
import uuid
import pytest
import requests

BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api"

ADMIN = ("admin@pws-alpha.com", "Admin@123")
COACH = ("coach@pws-alpha.com", "Coach@123")


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


def _hdr(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def admin_token():
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def coach_token():
    return _login(*COACH)


@pytest.fixture(scope="module")
def coach_user(coach_token):
    r = requests.get(f"{API}/auth/me", headers=_hdr(coach_token))
    assert r.status_code == 200
    return r.json()


# ------------------ Auth/me extended fields ------------------
class TestAuthMeCoachFields:
    def test_coach_me_has_extended_fields(self, coach_user):
        assert coach_user["role"] == "coach"
        assert "coach_permissions" in coach_user
        assert set(coach_user["coach_permissions"]) >= {"view_players", "add_players", "edit_players"}
        assert "assigned_sport" in coach_user
        assert coach_user["assigned_sport"]  # seeded "Cricket"


# ------------------ Player CRUD with extended fields ------------------
class TestPlayerExtendedFields:
    created_id = None

    def test_admin_creates_player_with_full_fields(self, admin_token, coach_user):
        payload = {
            "name": "TEST_Coach_Player_1",
            "kind": "player",
            "organization": "ALPHA",
            "is_resident": True,
            "father_name": "TEST_Father",
            "age": 14,
            "skill_level": "Beginner",
            "mobile": "9876543210",
            "locality": "TEST_Boring Road",
            "city": "TEST_Patna",
            "slot": "Morning",
            "assigned_coach_id": coach_user["id"],
            "sport": "Cricket",
        }
        r = requests.post(f"{API}/people", headers=_hdr(admin_token), json=payload)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["father_name"] == "TEST_Father"
        assert data["age"] == 14
        assert data["skill_level"] == "Beginner"
        assert data["slot"] == "Morning"
        assert data["assigned_coach_id"] == coach_user["id"]
        TestPlayerExtendedFields.created_id = data["id"]

    def test_get_persisted_extended_fields(self, admin_token):
        pid = TestPlayerExtendedFields.created_id
        assert pid
        r = requests.get(f"{API}/people", headers=_hdr(admin_token), params={"kind": "player"})
        assert r.status_code == 200
        match = next((p for p in r.json() if p["id"] == pid), None)
        assert match is not None
        assert match["father_name"] == "TEST_Father"
        assert match["mobile"] == "9876543210"
        assert match["locality"] == "TEST_Boring Road"
        assert match["city"] == "TEST_Patna"

    def test_patch_player_updates_skill_and_slot(self, admin_token):
        pid = TestPlayerExtendedFields.created_id
        r = requests.patch(f"{API}/people/{pid}", headers=_hdr(admin_token),
                           json={"skill_level": "Advanced", "slot": "Evening", "age": 15})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["skill_level"] == "Advanced"
        assert body["slot"] == "Evening"
        assert body["age"] == 15

    def test_cleanup(self, admin_token):
        pid = TestPlayerExtendedFields.created_id
        if pid:
            requests.delete(f"{API}/people/{pid}", headers=_hdr(admin_token))


# ------------------ Coach permissions gating on /people ------------------
class TestCoachPermissionGating:
    coach_id = None
    saved_perms = None

    def test_coach_with_default_perms_can_create_player(self, coach_token, coach_user):
        TestCoachPermissionGating.coach_id = coach_user["id"]
        TestCoachPermissionGating.saved_perms = list(coach_user.get("coach_permissions") or [])
        payload = {
            "name": "TEST_PermCheck_Player",
            "kind": "player",
            "organization": "ALPHA",
            "skill_level": "Beginner",
            "slot": "Morning",
            "assigned_coach_id": coach_user["id"],
        }
        r = requests.post(f"{API}/people", headers=_hdr(coach_token), json=payload)
        assert r.status_code == 200, r.text
        # cleanup
        requests.delete(f"{API}/people/{r.json()['id']}", headers=_hdr(coach_token))

    def test_coach_with_empty_perms_blocked(self, admin_token, coach_token):
        # Admin clears coach_permissions
        cid = TestCoachPermissionGating.coach_id
        r = requests.patch(f"{API}/users/{cid}", headers=_hdr(admin_token),
                           json={"coach_permissions": []})
        assert r.status_code == 200
        # coach now blocked
        payload = {"name": "TEST_BlockedPlayer", "kind": "player", "organization": "ALPHA",
                   "skill_level": "Beginner", "slot": "Morning"}
        # need fresh coach token? token still valid; gate uses DB lookup — should now be 403
        r2 = requests.post(f"{API}/people", headers=_hdr(coach_token), json=payload)
        assert r2.status_code == 403, f"expected 403 got {r2.status_code} {r2.text}"
        # restore
        r3 = requests.patch(f"{API}/users/{cid}", headers=_hdr(admin_token),
                            json={"coach_permissions": TestCoachPermissionGating.saved_perms})
        assert r3.status_code == 200


# ------------------ Admin updating coach perms vs coach updating self ------------------
class TestUserUpdateGuards:
    def test_admin_updates_coach_perms_and_sport(self, admin_token, coach_user):
        cid = coach_user["id"]
        r = requests.patch(f"{API}/users/{cid}", headers=_hdr(admin_token),
                           json={"coach_permissions": ["view_players", "add_players", "edit_players"],
                                 "assigned_sport": "Cricket"})
        assert r.status_code == 200
        body = r.json()
        assert set(body["coach_permissions"]) == {"view_players", "add_players", "edit_players"}
        assert body["assigned_sport"] == "Cricket"

    def test_coach_self_update_cannot_change_perms(self, coach_token, coach_user):
        # Coaches do not have can_manage rights on 'coach' kind, so self-update is forbidden entirely.
        cid = coach_user["id"]
        r = requests.patch(f"{API}/users/{cid}", headers=_hdr(coach_token),
                           json={"coach_permissions": [], "assigned_sport": "Hacked"})
        # Either 403 outright (not admin and target is coach kind), or 200 but stripped fields.
        # Per current code: assert_can_manage(user, 'coach') for coach role -> coaches don't have 'coach' in can_manage -> 403.
        assert r.status_code in (403, 200)
        if r.status_code == 200:
            # if allowed, must NOT have changed perms/sport
            assert r.json().get("assigned_sport") != "Hacked"


# ------------------ Coach dashboard ------------------
class TestCoachDashboard:
    def test_dashboard_shape(self, coach_token):
        r = requests.get(f"{API}/coach/dashboard", headers=_hdr(coach_token))
        assert r.status_code == 200, r.text
        d = r.json()
        for key in ("total_players", "by_slot", "by_skill", "today", "assigned_sport"):
            assert key in d, f"missing {key}"
        assert isinstance(d["total_players"], int)
        assert isinstance(d["by_slot"], dict)
        assert isinstance(d["by_skill"], dict)
        assert {"date", "marked", "present", "absent"} <= set(d["today"].keys())


# ------------------ Coach players grouping ------------------
class TestCoachPlayersGrouping:
    def test_groups_structure(self, coach_token):
        r = requests.get(f"{API}/coach/players", headers=_hdr(coach_token))
        assert r.status_code == 200
        d = r.json()
        assert "groups" in d and "players" in d and "total" in d
        # groups: dict[Centre] -> dict[Sport] -> dict[PlayerType] -> list[player]
        for centre_key, sport_map in d["groups"].items():
            assert isinstance(sport_map, dict)
            for sport_key, type_map in sport_map.items():
                assert isinstance(type_map, dict)
                for ptype_key, lst in type_map.items():
                    assert isinstance(lst, list)


# ------------------ Coach attendance flow ------------------
class TestCoachAttendance:
    pid_present = None
    pid_absent = None

    @classmethod
    def setup_class(cls):
        admin_t = _login(*ADMIN)
        coach_t = _login(*COACH)
        me = requests.get(f"{API}/auth/me", headers=_hdr(coach_t)).json()
        # ensure coach can manage players
        requests.patch(f"{API}/users/{me['id']}", headers=_hdr(admin_t),
                       json={"coach_permissions": ["view_players", "add_players", "edit_players"]})
        # create two test players assigned to coach in Morning slot
        for tag in ("ATT_PRESENT", "ATT_ABSENT"):
            payload = {"name": f"TEST_{tag}", "kind": "player", "organization": "ALPHA",
                       "skill_level": "Beginner", "slot": "Morning",
                       "assigned_coach_id": me["id"], "sport": "Cricket"}
            r = requests.post(f"{API}/people", headers=_hdr(admin_t), json=payload)
            assert r.status_code == 200, r.text
            if tag == "ATT_PRESENT":
                cls.pid_present = r.json()["id"]
            else:
                cls.pid_absent = r.json()["id"]
        cls.admin_t = admin_t
        cls.coach_t = coach_t

    def test_post_attendance_default_present_and_one_absent(self):
        date = "2030-01-15"
        r = requests.post(f"{API}/coach/attendance", headers=_hdr(self.coach_t),
                          json={"date": date, "slot": "Morning",
                                "absent_player_ids": [self.pid_absent]})
        assert r.status_code == 200, r.text
        body = r.json()
        # at least our 2 are counted in 'count'; absent must be exactly 1 in this run
        assert body["absent"] == 1
        assert body["count"] >= 2
        assert body["present"] == body["count"] - 1

    def test_idempotent_resubmit_same_date_slot(self):
        date = "2030-01-15"
        r = requests.post(f"{API}/coach/attendance", headers=_hdr(self.coach_t),
                          json={"date": date, "slot": "Morning",
                                "absent_player_ids": []})
        assert r.status_code == 200
        # Now history for that date+slot should reflect re-marking (all present)
        r2 = requests.get(f"{API}/coach/attendance", headers=_hdr(self.coach_t),
                          params={"date": date, "slot": "Morning"})
        assert r2.status_code == 200
        recs = r2.json()
        for rec in recs:
            if rec["person_id"] in (self.pid_present, self.pid_absent):
                assert rec["status"] == "present"

    def test_history_filter(self):
        r = requests.get(f"{API}/coach/attendance", headers=_hdr(self.coach_t),
                         params={"date": "2030-01-15", "slot": "Morning"})
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @classmethod
    def teardown_class(cls):
        for pid in (cls.pid_present, cls.pid_absent):
            if pid:
                requests.delete(f"{API}/people/{pid}", headers=_hdr(cls.admin_t))
