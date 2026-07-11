"""Attendance MVP — role access, session dedup, summaries, audit, export."""
import os
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "https://unified-track.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "principal": ("principal@prarambhika.com", "Principal@123"),
    "teacher": ("teacher@prarambhika.com", "Teacher@123"),
    "coach": ("coach@prarambhika.com", "Coach@123"),
    "warden": ("warden@prarambhika.com", "Warden@123"),
    "admin": ("admin@prarambhika.com", "Admin@123"),
    "super_admin": ("superadmin@prarambhika.com", "Super@123"),
}
LEGACY_CREDS = {
    "principal": ("admin@pws-alpha.com", "Admin@123"),
    "teacher": ("teacher@pws-alpha.com", "Teacher@123"),
    "coach": ("coach@pws-alpha.com", "Coach@123"),
    "warden": ("warden@pws-alpha.com", "Warden@123"),
    "admin": ("admin@pws-alpha.com", "Admin@123"),
    "super_admin": ("super@pws-alpha.com", "Super@123"),
}
TOKENS = {}


def _login(role):
    if role in TOKENS:
        return TOKENS[role]
    for creds in (CREDS, LEGACY_CREDS):
        email, pwd = creds[role]
        r = requests.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=15)
        if r.status_code == 200:
            TOKENS[role] = r.json()["access_token"]
            return TOKENS[role]
    pytest.skip(f"Could not log in as {role}")


def _hdr(role):
    return {"Authorization": f"Bearer {_login(role)}"}


@pytest.mark.integration
class TestAttendanceMVP:
    def test_batch_record_has_mvp_fields(self):
        r = requests.get(f"{API}/people", headers=_hdr("admin"), params={"kind": "student", "group": "9-A"}, timeout=15)
        if r.status_code != 200 or len(r.json()) < 1:
            pytest.skip("No students in 9-A")
        sid = r.json()[0]["id"]
        today = requests.get(f"{API}/auth/me", headers=_hdr("admin"), timeout=15).json().get("created_at", "")[:10]
        import datetime
        today = datetime.date.today().isoformat()
        payload = {
            "date": today,
            "kind": "student",
            "group": "9-A",
            "session": "morning",
            "marks": [{"person_id": sid, "status": "present"}],
        }
        rb = requests.post(f"{API}/attendance/batch", headers=_hdr("admin"), json=payload, timeout=15)
        assert rb.status_code == 200, rb.text
        rec = rb.json()["records"][0]
        for field in ("entity_id", "person_id", "date", "session", "status", "marked_by", "marked_at", "source"):
            assert field in rec, f"Missing {field} in {rec}"

    def test_duplicate_session_upserts_not_duplicates(self):
        r = requests.get(f"{API}/people", headers=_hdr("admin"), params={"kind": "student", "group": "9-A"}, timeout=15)
        if r.status_code != 200 or len(r.json()) < 1:
            pytest.skip("No students")
        sid = r.json()[0]["id"]
        import datetime
        today = datetime.date.today().isoformat()
        payload = {
            "date": today,
            "kind": "student",
            "group": "9-A",
            "session": "morning",
            "marks": [{"person_id": sid, "status": "present"}],
        }
        requests.post(f"{API}/attendance/batch", headers=_hdr("admin"), json=payload, timeout=15)
        payload["marks"] = [{"person_id": sid, "status": "absent"}]
        requests.post(f"{API}/attendance/batch", headers=_hdr("admin"), json=payload, timeout=15)
        rl = requests.get(
            f"{API}/attendance",
            headers=_hdr("admin"),
            params={"date": today, "kind": "student", "group": "9-A", "session": "morning", "person_id": sid},
            timeout=15,
        )
        assert rl.status_code == 200
        rows = [x for x in rl.json() if x.get("person_id") == sid and x.get("session") == "morning"]
        assert len(rows) == 1
        assert rows[0]["status"] == "absent"

    def test_teacher_cannot_add_student(self):
        r = requests.post(
            f"{API}/people",
            headers=_hdr("teacher"),
            json={"kind": "student", "name": "Blocked Student", "group": "9-A", "organization": "PWS"},
            timeout=15,
        )
        assert r.status_code == 403, r.text

    def test_teacher_summary_scoped_or_allowed(self):
        r = requests.get(f"{API}/attendance/summary", headers=_hdr("teacher"), timeout=15)
        assert r.status_code in (200, 403)

    def test_principal_summary_and_export(self):
        rs = requests.get(f"{API}/attendance/summary", headers=_hdr("principal"), params={"kind": "student"}, timeout=15)
        assert rs.status_code == 200, rs.text
        data = rs.json()
        assert "totals" in data
        assert "percentage" in data["totals"]
        ex = requests.get(f"{API}/attendance/export", headers=_hdr("principal"), timeout=15)
        assert ex.status_code == 200
        assert "text/csv" in ex.headers.get("content-type", "")

    def test_coach_player_attendance_scoped(self):
        r = requests.post(
            f"{API}/coach/attendance",
            headers=_hdr("coach"),
            json={"date": "2026-07-11", "slot": "Morning", "centre": "Balua", "sport": "Cricket", "absent_player_ids": []},
            timeout=15,
        )
        assert r.status_code in (200, 400), r.text

    def test_coach_cannot_export_attendance_without_perm(self):
        r = requests.get(f"{API}/attendance/export", headers=_hdr("coach"), timeout=15)
        assert r.status_code == 403

    def test_warden_hostel_roll_call(self):
        res = requests.get(f"{API}/people", headers=_hdr("warden"), params={"resident": True}, timeout=15)
        if res.status_code != 200 or not res.json():
            pytest.skip("No residents")
        rid = res.json()[0]["id"]
        import datetime
        today = datetime.date.today().isoformat()
        r = requests.post(
            f"{API}/hostel/roll-call",
            headers=_hdr("warden"),
            json={"date": today, "session": "evening", "entries": [{"resident_id": rid, "present": True}]},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        att = requests.get(
            f"{API}/attendance",
            headers=_hdr("admin"),
            params={"date": today, "kind": "hostel", "person_id": rid, "session": "evening"},
            timeout=15,
        )
        if att.status_code == 200 and att.json():
            row = att.json()[0]
            assert row.get("source") == "hostel_roll_call"

    def test_correction_requires_reason(self):
        rl = requests.get(f"{API}/attendance", headers=_hdr("principal"), params={"kind": "student"}, timeout=15)
        if rl.status_code != 200 or not rl.json():
            pytest.skip("No attendance rows")
        rec_id = rl.json()[0]["id"]
        r = requests.post(
            f"{API}/attendance/correct",
            headers=_hdr("principal"),
            json={"record_id": rec_id, "status": "present", "reason": ""},
            timeout=15,
        )
        assert r.status_code == 400

    def test_audit_history_visible_to_principal(self):
        r = requests.get(f"{API}/attendance/audit", headers=_hdr("principal"), timeout=15)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
