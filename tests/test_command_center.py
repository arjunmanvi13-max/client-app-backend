"""Iteration 4: Layered Visibility — Command Centre (Layer 1) + Department drill-downs (Layer 2)."""
import os
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://unified-track.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

CREDS = {
    "super_admin": ("super@pws-alpha.com", "Super@123"),
    "admin": ("admin@pws-alpha.com", "Admin@123"),
    "teacher": ("teacher@pws-alpha.com", "Teacher@123"),
    "coach": ("coach@pws-alpha.com", "Coach@123"),
    "warden": ("warden@pws-alpha.com", "Warden@123"),
    "staff": ("staff@pws-alpha.com", "Staff@123"),
    "student": ("student@pws-alpha.com", "Student@123"),
    "player": ("player@pws-alpha.com", "Player@123"),
}


def _login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def tokens():
    return {role: _login(*c) for role, c in CREDS.items()}


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


# ------------------- Layer 1: Command Centre -------------------
class TestCommandCenter:
    def test_admin_command_center_shape(self, tokens):
        r = requests.get(f"{API}/command-center", headers=_hdr(tokens["admin"]), timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        # required top-level keys
        for k in ["date", "roster_counts", "attendance_by_kind", "tasks", "alerts", "kpis", "departments"]:
            assert k in d, f"missing {k}"
        # roster_counts
        for k in ["teachers", "coaches", "staff", "students", "players"]:
            assert k in d["roster_counts"], f"roster missing {k}"
            assert isinstance(d["roster_counts"][k], int)
        # tasks
        for k in ["total", "by_status", "by_department", "completion_pct"]:
            assert k in d["tasks"], f"tasks missing {k}"
        for st in ["open", "in_progress", "blocked", "completed", "cancelled"]:
            assert st in d["tasks"]["by_status"]
        # kpis
        assert "attendance_pct_today" in d["kpis"]
        assert "task_completion_pct" in d["kpis"]
        # departments
        for dept in ["school", "sports", "hostel", "canteen"]:
            assert dept in d["departments"], f"missing dept {dept}"
        for k in ["residents", "morning_present", "morning_absent", "night_present", "night_absent",
                  "out_on_pass", "pending_pass"]:
            assert k in d["departments"]["hostel"], f"hostel missing {k}"
        assert isinstance(d["alerts"], list)

    def test_super_admin_can_access(self, tokens):
        r = requests.get(f"{API}/command-center", headers=_hdr(tokens["super_admin"]), timeout=15)
        assert r.status_code == 200

    @pytest.mark.parametrize("role", ["coach", "teacher", "warden", "staff", "student", "player"])
    def test_non_admin_forbidden(self, tokens, role):
        r = requests.get(f"{API}/command-center", headers=_hdr(tokens[role]), timeout=15)
        assert r.status_code == 403, f"{role} should be 403, got {r.status_code}"

    def test_unauth(self):
        r = requests.get(f"{API}/command-center", timeout=15)
        assert r.status_code in (401, 403)


# ------------------- Layer 2: Department drill-downs -------------------
class TestDepartmentSchool:
    def test_admin_ok(self, tokens):
        r = requests.get(f"{API}/departments/school", headers=_hdr(tokens["admin"]), timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["date", "students", "teachers_count", "teachers", "by_class"]:
            assert k in d
        assert isinstance(d["teachers"], list)
        assert d["teachers_count"] == len(d["teachers"])
        if d["teachers"]:
            t0 = d["teachers"][0]
            assert "_id" not in t0
            assert "password_hash" not in t0
            assert "email" in t0 and "name" in t0

    @pytest.mark.parametrize("role", ["coach", "teacher", "warden", "staff", "student", "player"])
    def test_non_admin_forbidden(self, tokens, role):
        r = requests.get(f"{API}/departments/school", headers=_hdr(tokens[role]), timeout=15)
        assert r.status_code == 403


class TestDepartmentSports:
    def test_admin_ok(self, tokens):
        r = requests.get(f"{API}/departments/sports", headers=_hdr(tokens["admin"]), timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["date", "coaches_count", "coaches", "players", "by_slot", "by_sport"]:
            assert k in d
        assert isinstance(d["coaches"], list)
        assert d["coaches_count"] == len(d["coaches"])
        if d["coaches"]:
            c0 = d["coaches"][0]
            assert "_id" not in c0 and "password_hash" not in c0
        # by_sport should include Cricket since coach is seeded with Cricket players
        assert isinstance(d["by_sport"], dict)

    @pytest.mark.parametrize("role", ["coach", "teacher", "warden", "staff", "student", "player"])
    def test_non_admin_forbidden(self, tokens, role):
        r = requests.get(f"{API}/departments/sports", headers=_hdr(tokens[role]), timeout=15)
        assert r.status_code == 403


class TestDepartmentHostel:
    def test_admin_ok(self, tokens):
        r = requests.get(f"{API}/departments/hostel", headers=_hdr(tokens["admin"]), timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["residents", "morning_present", "morning_absent", "night_present", "night_absent",
                  "out_on_pass", "pending_pass"]:
            assert k in d
            assert isinstance(d[k], int)

    @pytest.mark.parametrize("role", ["coach", "teacher", "warden", "staff", "student", "player"])
    def test_non_admin_forbidden(self, tokens, role):
        r = requests.get(f"{API}/departments/hostel", headers=_hdr(tokens[role]), timeout=15)
        assert r.status_code == 403


class TestDepartmentCanteen:
    def test_admin_ok(self, tokens):
        r = requests.get(f"{API}/departments/canteen", headers=_hdr(tokens["admin"]), timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["date", "staff_count", "staff", "tasks"]:
            assert k in d
        assert isinstance(d["staff"], list)
        assert d["staff_count"] == len(d["staff"])
        assert isinstance(d["tasks"], list)
        if d["staff"]:
            s0 = d["staff"][0]
            assert "_id" not in s0 and "password_hash" not in s0
        for t in d["tasks"]:
            assert "_id" not in t

    @pytest.mark.parametrize("role", ["coach", "teacher", "warden", "staff", "student", "player"])
    def test_non_admin_forbidden(self, tokens, role):
        r = requests.get(f"{API}/departments/canteen", headers=_hdr(tokens[role]), timeout=15)
        assert r.status_code == 403
