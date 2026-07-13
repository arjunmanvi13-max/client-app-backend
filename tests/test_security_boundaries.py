"""Pre-launch security boundary tests — role, entity, and isolation regressions.

These tests assert the *expected secure behaviour* for MVP launch.
Failures indicate launch blockers documented in SECURITY_LAUNCH_REPORT.md.
"""
import os
import time
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "https://client-app-backend-production.up.railway.app").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "super_admin": ("superadmin@prarambhika.com", "Super@123"),
    "principal": ("principal@prarambhika.com", "Principal@123"),
    "admin": ("admin@prarambhika.com", "Admin@123"),
    "teacher": ("teacher@prarambhika.com", "Teacher@123"),
    "coach": ("coach@prarambhika.com", "Coach@123"),
    "warden": ("warden@prarambhika.com", "Warden@123"),
    "parent_pws": ("parent_pws@prarambhika.com", "Parent@123"),
}
LEGACY_CREDS = {
    "super_admin": ("super@pws-alpha.com", "Super@123"),
    "principal": ("admin@pws-alpha.com", "Admin@123"),
    "admin": ("admin@pws-alpha.com", "Admin@123"),
    "teacher": ("teacher@pws-alpha.com", "Teacher@123"),
    "coach": ("coach@pws-alpha.com", "Coach@123"),
    "warden": ("warden@pws-alpha.com", "Warden@123"),
    "parent_pws": ("parent@pws-alpha.com", "Parent@123"),
}
TOKENS = {}


def _login(role):
    if role in TOKENS:
        return TOKENS[role]
    for creds in (CREDS, LEGACY_CREDS):
        if role not in creds:
            continue
        email, pwd = creds[role]
        r = requests.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=15)
        if r.status_code == 200:
            TOKENS[role] = r.json()["access_token"]
            return TOKENS[role]
    pytest.skip(f"Could not log in as {role}")


def _hdr(role):
    return {"Authorization": f"Bearer {_login(role)}"}


def _teacher_assigned_section():
    r = requests.get(f"{API}/academic/sections/for-attendance", headers=_hdr("teacher"), timeout=15)
    if r.status_code != 200:
        pytest.skip("Teacher sections unavailable")
    sections = r.json().get("sections", [])
    if not sections:
        pytest.skip("No teacher-assigned sections")
    return sections[0]


def _unassigned_section():
    pr = requests.get(f"{API}/academic/sections", headers=_hdr("principal"), timeout=15)
    if pr.status_code != 200:
        pytest.skip("Principal sections unavailable")
    tr = requests.get(f"{API}/academic/sections/for-attendance", headers=_hdr("teacher"), timeout=15)
    assigned = {s["id"] for s in (tr.json().get("sections", []) if tr.status_code == 200 else [])}
    for s in pr.json():
        if s["id"] not in assigned:
            return s
    pytest.skip("No unassigned section for teacher")


def _students_in_section(section_id, role="principal"):
    r = requests.get(
        f"{API}/people",
        headers=_hdr(role),
        params={"kind": "student", "section_id": section_id},
        timeout=15,
    )
    if r.status_code != 200 or not r.json():
        pytest.skip("No students in section")
    return r.json()


def _coach_players_list(body):
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def _coach_roster_player():
    r = requests.get(f"{API}/people", headers=_hdr("coach"), params={"kind": "player"}, timeout=15)
    players = _coach_players_list(r.json() if r.status_code == 200 else [])
    if r.status_code != 200 or not players:
        pytest.skip("Coach roster empty")
    return players[0]


def _player_outside_coach_roster():
    ar = requests.get(f"{API}/people", headers=_hdr("admin"), params={"kind": "player"}, timeout=15)
    cr = requests.get(f"{API}/people", headers=_hdr("coach"), params={"kind": "player"}, timeout=15)
    if ar.status_code != 200:
        pytest.skip("Cannot list players")
    coach_ids = {p["id"] for p in _coach_players_list(cr.json() if cr.status_code == 200 else [])}
    for p in ar.json():
        if p["id"] not in coach_ids:
            return p
    pytest.skip("All players visible to coach — cannot test out-of-roster")


# ---------------------------------------------------------------------------
# Auth, token, login/logout
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestAuthBoundaries:
    def test_unauthenticated_me_rejected(self):
        r = requests.get(f"{API}/auth/me", timeout=15)
        assert r.status_code in (401, 403), r.text

    def test_invalid_token_rejected(self):
        r = requests.get(f"{API}/auth/me", headers={"Authorization": "Bearer invalid.token"}, timeout=15)
        assert r.status_code == 401, r.text

    def test_wrong_domain_email_rejected(self):
        r = requests.post(
            f"{API}/auth/login",
            json={"email": "hacker@gmail.com", "password": "x"},
            timeout=15,
        )
        assert r.status_code in (400, 401), r.text

    def test_logout_requires_auth(self):
        r = requests.post(f"{API}/auth/logout", timeout=15)
        assert r.status_code in (401, 403), r.text

    def test_token_still_valid_after_logout_until_expiry(self):
        """JWT is stateless — logout is advisory; token works until exp (known limitation)."""
        login = requests.post(
            f"{API}/auth/login",
            json={"email": CREDS["teacher"][0], "password": CREDS["teacher"][1]},
            timeout=15,
        )
        if login.status_code != 200:
            pytest.skip("Teacher login unavailable")
        token = login.json()["access_token"]
        lo = requests.post(f"{API}/auth/logout", headers={"Authorization": f"Bearer {token}"}, timeout=15)
        assert lo.status_code == 200, lo.text
        me = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=15)
        assert me.status_code == 200, "Stateless JWT remains valid after logout until expiry"


# ---------------------------------------------------------------------------
# Entity isolation
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestEntityIsolationBoundaries:
    def test_principal_cannot_list_alpha_players(self):
        r = requests.get(f"{API}/people", headers=_hdr("principal"), params={"kind": "player"}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json() == [], "PWS principal must not see ALPHA players"

    def test_admin_cannot_list_pws_students(self):
        r = requests.get(f"{API}/people", headers=_hdr("admin"), params={"kind": "student"}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json() == [], "Sports admin must not see PWS students"

    def test_principal_cannot_collect_alpha_fees(self):
        r = requests.get(f"{API}/fees", headers=_hdr("principal"), timeout=15)
        assert r.status_code == 200, r.text
        for f in r.json():
            assert f.get("entity_id") == "pws", "Principal fees must be PWS-scoped"

    def test_cross_entity_attendance_list_blocked(self):
        pr = requests.get(f"{API}/attendance", headers=_hdr("principal"), params={"kind": "player"}, timeout=15)
        ad = requests.get(f"{API}/attendance", headers=_hdr("admin"), params={"kind": "student"}, timeout=15)
        assert pr.status_code == 200 and pr.json() == []
        assert ad.status_code == 200 and ad.json() == []


# ---------------------------------------------------------------------------
# Parent-child isolation
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestParentIsolationBoundaries:
    def test_parent_people_limited_to_linked_wards(self):
        wards = requests.get(f"{API}/parent/wards", headers=_hdr("parent_pws"), timeout=15)
        if wards.status_code != 200:
            pytest.skip("Parent wards unavailable")
        ward_ids = {w["id"] for w in wards.json()}
        people = requests.get(f"{API}/people", headers=_hdr("parent_pws"), timeout=15)
        assert people.status_code == 200, people.text
        for p in people.json():
            assert p["id"] in ward_ids, "Parent must only see linked wards via /people"

    def test_parent_cannot_access_staff_apis(self):
        parent = _hdr("parent_pws")
        assert requests.get(f"{API}/fees", headers=parent, timeout=15).status_code == 403
        assert requests.get(f"{API}/marks/assessments", headers=parent, timeout=15).status_code == 403
        assert requests.get(f"{API}/parent/wards", headers=_hdr("principal"), timeout=15).status_code == 403

    def test_parent_cannot_read_unlinked_ward_attendance(self):
        wards = requests.get(f"{API}/parent/wards", headers=_hdr("parent_pws"), timeout=15)
        if wards.status_code != 200 or not wards.json():
            pytest.skip("No parent wards")
        linked = {w["id"] for w in wards.json()}
        students = requests.get(f"{API}/people", headers=_hdr("principal"), params={"kind": "student"}, timeout=15)
        if students.status_code != 200:
            pytest.skip("Cannot list students")
        other = next((s["id"] for s in students.json() if s["id"] not in linked), None)
        if not other:
            pytest.skip("No unlinked student")
        r = requests.get(f"{API}/parent/attendance/{other}", headers=_hdr("parent_pws"), timeout=15)
        assert r.status_code == 404, "Parent must not read unlinked ward attendance"


# ---------------------------------------------------------------------------
# Teacher assignment restrictions
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestTeacherAssignmentBoundaries:
    def test_teacher_list_students_requires_kind_and_section(self):
        """Without kind, teachers must not enumerate all PWS people."""
        r = requests.get(f"{API}/people", headers=_hdr("teacher"), timeout=15)
        assert r.status_code == 400, f"Teacher GET /people without kind should be denied, got {r.status_code}"

    def test_teacher_cannot_query_unassigned_section(self):
        ten_b = _unassigned_section()
        r = requests.get(
            f"{API}/people",
            headers=_hdr("teacher"),
            params={"kind": "student", "section_id": ten_b["id"]},
            timeout=15,
        )
        assert r.status_code == 403, r.text

    def test_teacher_cannot_batch_mark_unassigned_section(self):
        ten_b = _unassigned_section()
        r = requests.post(
            f"{API}/attendance/batch",
            headers=_hdr("teacher"),
            json={
                "date": "2026-07-11",
                "kind": "student",
                "group": ten_b.get("label", "10-B"),
                "section_id": ten_b["id"],
                "session": None,
                "sport": None,
                "marks": [],
            },
            timeout=15,
        )
        assert r.status_code == 403, r.text

    def test_teacher_cannot_mark_student_from_wrong_section(self):
        """Each person_id in batch must belong to the declared section."""
        assigned = _teacher_assigned_section()
        unassigned = _unassigned_section()
        wrong_students = _students_in_section(unassigned["id"])
        if not wrong_students:
            pytest.skip("No students in unassigned section")
        wrong_id = wrong_students[0]["id"]
        r = requests.post(
            f"{API}/attendance/batch",
            headers=_hdr("teacher"),
            json={
                "date": "2026-07-11",
                "kind": "student",
                "group": assigned.get("label", "9-A"),
                "section_id": assigned["id"],
                "session": None,
                "sport": None,
                "marks": [{"person_id": wrong_id, "status": "present"}],
            },
            timeout=15,
        )
        assert r.status_code in (403, 404), (
            f"Teacher must not mark attendance for student outside assigned section, got {r.status_code}"
        )

    def test_teacher_marks_grid_unassigned_section_forbidden(self):
        ten_b = _unassigned_section()
        r = requests.get(
            f"{API}/marks/grid",
            headers=_hdr("teacher"),
            params={"section_id": ten_b["id"]},
            timeout=15,
        )
        assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Coach assignment restrictions
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestCoachAssignmentBoundaries:
    def test_coach_list_players_requires_kind(self):
        """Without kind, coaches must not enumerate all ALPHA people."""
        r = requests.get(f"{API}/people", headers=_hdr("coach"), timeout=15)
        assert r.status_code == 400, f"Coach GET /people without kind should be denied, got {r.status_code}"

    def test_coach_roster_scoped_with_kind(self):
        r = requests.get(f"{API}/people", headers=_hdr("coach"), params={"kind": "player"}, timeout=15)
        assert r.status_code == 200, r.text
        admin_players = requests.get(f"{API}/people", headers=_hdr("admin"), params={"kind": "player"}, timeout=15)
        if admin_players.status_code != 200:
            pytest.skip("Admin player list unavailable")
        coach_count = len(_coach_players_list(r.json()))
        admin_count = len(admin_players.json())
        if admin_count > coach_count:
            assert coach_count > 0, "Coach should see a subset of players when admin sees more"

    def test_coach_cannot_batch_mark_out_of_roster_player(self):
        player = _player_outside_coach_roster()
        r = requests.post(
            f"{API}/attendance/batch",
            headers=_hdr("coach"),
            json={
                "date": "2026-07-11",
                "kind": "player",
                "group": None,
                "section_id": None,
                "session": "morning",
                "sport": player.get("sport"),
                "marks": [{"person_id": player["id"], "status": "present"}],
            },
            timeout=15,
        )
        assert r.status_code in (403, 404), (
            f"Coach must not mark attendance for out-of-roster player, got {r.status_code}"
        )

    def test_coach_cannot_read_published_assessments_out_of_roster(self):
        player = _player_outside_coach_roster()
        r = requests.get(
            f"{API}/coach-assessments/published/{player['id']}",
            headers=_hdr("coach"),
            timeout=15,
        )
        assert r.status_code in (403, 404), (
            f"Coach must not read assessments for out-of-roster player, got {r.status_code}"
        )


# ---------------------------------------------------------------------------
# Hostel / warden boundaries
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestHostelBoundaries:
    def test_teacher_cannot_list_all_gate_passes(self):
        r = requests.get(f"{API}/hostel/gate-pass", headers=_hdr("teacher"), timeout=15)
        assert r.status_code in (403, 404), f"Teacher must not list gate passes, got {r.status_code}"

    def test_teacher_cannot_decide_gate_pass(self):
        warden_list = requests.get(f"{API}/hostel/gate-pass", headers=_hdr("warden"), timeout=15)
        if warden_list.status_code != 200 or not warden_list.json():
            pytest.skip("No gate passes to test")
        gp_id = warden_list.json()[0]["id"]
        r = requests.post(
            f"{API}/hostel/gate-pass/{gp_id}/decision",
            headers=_hdr("teacher"),
            json={"decision": "approved", "note": "test"},
            timeout=15,
        )
        assert r.status_code == 403, r.text

    def test_warden_can_list_gate_passes(self):
        r = requests.get(f"{API}/hostel/gate-pass", headers=_hdr("warden"), timeout=15)
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Approvals audit & duplicate protection
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestApprovalsAuditBoundaries:
    def test_pending_approval_has_history_on_create(self):
        players = requests.get(f"{API}/people", headers=_hdr("admin"), params={"kind": "player"}, timeout=15)
        if players.status_code != 200 or not players.json():
            pytest.skip("No players")
        pid = players.json()[0]["id"]
        r = requests.post(
            f"{API}/approval-requests",
            headers=_hdr("admin"),
            json={"type": "player_deactivation", "subject_id": pid, "reason": "security-test"},
            timeout=15,
        )
        if r.status_code not in (200, 201, 409):
            pytest.skip(f"Cannot create approval: {r.status_code}")
        if r.status_code == 409:
            pytest.skip("Duplicate pending approval exists")
        data = r.json()
        history = data.get("history") or []
        assert any(h.get("action") == "submitted" for h in history), "Approval create must append submitted history"

    def test_parent_cannot_list_approval_requests(self):
        r = requests.get(f"{API}/approval-requests", headers=_hdr("parent_pws"), timeout=15)
        assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# File upload restrictions
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestUploadBoundaries:
    def test_teacher_cannot_bulk_upload(self):
        r = requests.post(
            f"{API}/bulk-upload/players",
            headers=_hdr("teacher"),
            files={"file": ("test.csv", "Name,Father\nX,Y\n", "text/csv")},
            timeout=15,
        )
        assert r.status_code == 403, r.text

    def test_bulk_upload_rejects_oversized_row_count(self):
        header = "Name,Father Name,Age,Mobile,Address,City,Centre,Sport,Category,Slot,Skill,DOJ\n"
        rows = "".join(f"P{i},F{i},14,9000000{i:03d},A,B,Balua,Cricket,Daily,Morning,Beginner,2026-01-01\n" for i in range(501))
        r = requests.post(
            f"{API}/bulk-upload/players",
            headers=_hdr("admin"),
            files={"file": ("big.csv", header + rows, "text/csv")},
            timeout=30,
        )
        assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# Public PDF / capability-token endpoints
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestPublicPdfBoundaries:
    def test_receipt_pdf_requires_valid_batch_or_auth(self):
        r = requests.get(f"{API}/fees/receipt/not-a-real-uuid/pdf", timeout=15)
        assert r.status_code == 404, r.text

    def test_receipt_pdf_known_uuid_without_auth_is_accessible(self):
        """Documents current behaviour: UUID acts as capability token (launch risk if shared)."""
        wards = requests.get(f"{API}/parent/wards", headers=_hdr("parent_pws"), timeout=15)
        if wards.status_code != 200 or not wards.json():
            pytest.skip("No parent wards for receipt test")
        wid = wards.json()[0]["id"]
        rec = requests.get(f"{API}/parent/receipts/{wid}", headers=_hdr("parent_pws"), timeout=15)
        if rec.status_code != 200:
            pytest.skip("Parent receipts unavailable")
        receipts = rec.json().get("receipts") or []
        batch_id = next((r.get("batch_id") for r in receipts if r.get("batch_id")), None)
        if not batch_id:
            pytest.skip("No fee receipt batch_id")
        pdf = requests.get(f"{API}/fees/receipt/{batch_id}/pdf", timeout=15)
        assert pdf.status_code == 200, pdf.text
        assert pdf.headers.get("content-type", "").startswith("application/pdf")
