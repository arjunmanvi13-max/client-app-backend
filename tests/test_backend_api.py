"""Backend API tests for PWS & ALPHA Tracker."""
import os
import pytest
import requests
from datetime import datetime, timezone, timedelta

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL", "https://unified-track.preview.emergentagent.com")).rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "admin": ("admin@pws-alpha.com", "Admin@123"),
    "warden": ("warden@pws-alpha.com", "Warden@123"),
    "teacher": ("teacher@pws-alpha.com", "Teacher@123"),
    "student": ("student@pws-alpha.com", "Student@123"),
}

TOKENS = {}


def _login(role):
    if role in TOKENS:
        return TOKENS[role]
    email, pwd = CREDS[role]
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=15)
    assert r.status_code == 200, f"login {role} failed: {r.status_code} {r.text}"
    data = r.json()
    assert "access_token" in data and "user" in data
    TOKENS[role] = data["access_token"]
    return data["access_token"]


def _hdr(role):
    return {"Authorization": f"Bearer {_login(role)}"}


# ---------- Auth ----------
class TestAuth:
    def test_login_admin(self):
        r = requests.post(f"{API}/auth/login", json={"email": CREDS["admin"][0], "password": CREDS["admin"][1]})
        assert r.status_code == 200
        d = r.json()
        assert d["user"]["role"] == "admin"
        assert d["user"]["email"] == CREDS["admin"][0]

    def test_login_invalid(self):
        r = requests.post(f"{API}/auth/login", json={"email": "admin@pws-alpha.com", "password": "wrong"})
        assert r.status_code == 401

    def test_me(self):
        r = requests.get(f"{API}/auth/me", headers=_hdr("admin"))
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_me_no_token(self):
        r = requests.get(f"{API}/auth/me")
        assert r.status_code == 401


# ---------- Dashboard ----------
class TestDashboard:
    def test_admin_dashboard(self):
        r = requests.get(f"{API}/dashboard", headers=_hdr("admin"))
        assert r.status_code == 200
        d = r.json()
        for k in ["my_tasks", "pending_tasks", "overdue_tasks", "unread_notifications", "total_users", "total_tasks", "pending_gate_passes"]:
            assert k in d, f"missing {k}"


# ---------- Tasks ----------
class TestTasks:
    def test_list_seed_tasks(self):
        r = requests.get(f"{API}/tasks", headers=_hdr("admin"))
        assert r.status_code == 200
        tasks = r.json()
        assert len(tasks) >= 4

    def test_list_mine_filter(self):
        r = requests.get(f"{API}/tasks?mine=true", headers=_hdr("teacher"))
        assert r.status_code == 200

    def test_list_priority_high(self):
        r = requests.get(f"{API}/tasks?priority=high", headers=_hdr("admin"))
        assert r.status_code == 200
        for t in r.json():
            assert t["priority"] == "high"

    def test_create_task_and_comment_and_status(self):
        # get teacher id
        r = requests.get(f"{API}/users?role=teacher", headers=_hdr("admin"))
        assert r.status_code == 200
        teacher_id = r.json()[0]["id"]
        payload = {"title": "TEST_pytest task", "description": "auto", "priority": "high", "assignee_ids": [teacher_id]}
        r = requests.post(f"{API}/tasks", headers=_hdr("admin"), json=payload)
        assert r.status_code == 200, r.text
        task = r.json()
        tid = task["id"]
        assert task["status"] in ("open", "assigned")

        # comment
        rc = requests.post(f"{API}/tasks/{tid}/comments", headers=_hdr("admin"), json={"text": "hi"})
        assert rc.status_code == 200

        # patch status
        rp = requests.patch(f"{API}/tasks/{tid}", headers=_hdr("admin"), json={"status": "in_progress"})
        assert rp.status_code == 200
        assert rp.json()["status"] == "in_progress"

        # GET verify persistence + comment
        rg = requests.get(f"{API}/tasks/{tid}", headers=_hdr("admin"))
        assert rg.status_code == 200
        d = rg.json()
        assert d["status"] == "in_progress"
        assert any(c["text"] == "hi" for c in d.get("comments", []))


# ---------- People ----------
class TestPeople:
    def test_list_students(self):
        r = requests.get(f"{API}/people?kind=student", headers=_hdr("admin"))
        assert r.status_code == 200
        ppl = r.json()
        assert len(ppl) >= 10
        assert all(p["kind"] == "student" for p in ppl)

    def test_player_groups(self):
        r = requests.get(f"{API}/people/groups?kind=player", headers=_hdr("admin"))
        assert r.status_code == 200
        groups = r.json()["groups"]
        assert len(groups) >= 3


# ---------- Attendance ----------
class TestAttendance:
    def test_batch_and_list_and_summary(self):
        r = requests.get(f"{API}/people?kind=student&group=9-A", headers=_hdr("admin"))
        students = r.json()
        assert len(students) >= 2
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        marks = [{"person_id": students[0]["id"], "status": "present"},
                 {"person_id": students[1]["id"], "status": "absent"}]
        rb = requests.post(f"{API}/attendance/batch", headers=_hdr("admin"),
                           json={"date": today, "kind": "student", "group": "9-A", "marks": marks})
        assert rb.status_code == 200, rb.text
        assert rb.json()["count"] == 2

        # idempotent (upsert)
        rb2 = requests.post(f"{API}/attendance/batch", headers=_hdr("admin"),
                            json={"date": today, "kind": "student", "group": "9-A", "marks": marks})
        assert rb2.status_code == 200

        rl = requests.get(f"{API}/attendance?date={today}&kind=student&group=9-A", headers=_hdr("admin"))
        assert rl.status_code == 200
        assert len(rl.json()) >= 2

        rs = requests.get(f"{API}/attendance/summary", headers=_hdr("admin"))
        assert rs.status_code == 200
        s = rs.json()
        assert "summary" in s
        assert "student" in s["summary"]


# ---------- Hostel ----------
class TestHostel:
    def test_gate_pass_flow_and_rbac(self):
        # warden creates pass
        r = requests.get(f"{API}/people?kind=student", headers=_hdr("warden"))
        resident_id = r.json()[0]["id"]
        out = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        ret = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        rc = requests.post(f"{API}/hostel/gate-pass", headers=_hdr("warden"),
                           json={"resident_id": resident_id, "reason": "TEST_pytest", "out_time": out, "expected_return": ret})
        assert rc.status_code == 200, rc.text
        gp = rc.json()
        assert gp["status"] == "pending"

        # non-warden (teacher) cannot decide
        rd_no = requests.post(f"{API}/hostel/gate-pass/{gp['id']}/decision", headers=_hdr("teacher"),
                              json={"decision": "approved"})
        assert rd_no.status_code == 403

        # warden approves
        rd = requests.post(f"{API}/hostel/gate-pass/{gp['id']}/decision", headers=_hdr("warden"),
                           json={"decision": "approved", "note": "ok"})
        assert rd.status_code == 200
        assert rd.json()["status"] == "approved"

    def test_roll_call(self):
        r = requests.get(f"{API}/people?kind=student", headers=_hdr("warden"))
        people = r.json()[:2]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rc = requests.post(f"{API}/hostel/roll-call", headers=_hdr("warden"),
                           json={"date": today, "session": "morning",
                                 "entries": [{"resident_id": p["id"], "present": True} for p in people]})
        assert rc.status_code == 200
        assert rc.json()["count"] == 2

    def test_hostel_dashboard(self):
        r = requests.get(f"{API}/hostel/dashboard", headers=_hdr("warden"))
        assert r.status_code == 200
        for k in ["residents_count", "pending_passes", "morning_present", "night_present"]:
            assert k in r.json()


# ---------- Notifications ----------
class TestNotifications:
    def test_list_and_read(self):
        r = requests.get(f"{API}/notifications", headers=_hdr("teacher"))
        assert r.status_code == 200
        data = r.json()
        notifs = data.get("items", data) if isinstance(data, dict) else data
        if notifs:
            nid = notifs[0]["id"]
            rr = requests.post(f"{API}/notifications/{nid}/read", headers=_hdr("teacher"))
            assert rr.status_code == 200


# ---------- RBAC ----------
class TestRBAC:
    def test_non_admin_cannot_create_user(self):
        r = requests.post(f"{API}/users", headers=_hdr("teacher"),
                          json={"email": "TEST_block@x.com", "password": "x", "name": "x", "role": "staff"})
        assert r.status_code == 403
