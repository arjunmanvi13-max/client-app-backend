"""Milestone 1 — academic structure, section scoping, teacher attendance gates."""
import os
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "https://unified-track.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

# Prefer prarambhika seed accounts; fall back to legacy preview creds.
CREDS = {
    "principal": ("principal@prarambhika.com", "Principal@123"),
    "teacher": ("teacher@prarambhika.com", "Teacher@123"),
    "super_admin": ("superadmin@prarambhika.com", "Super@123"),
}
LEGACY_CREDS = {
    "principal": ("admin@prarambhika.com", "Admin@123"),
    "teacher": ("teacher@prarambhika.com", "Teacher@123"),
    "super_admin": ("super@prarambhika.com", "Super@123"),
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
class TestAcademicStructureAPI:
    def test_principal_lists_sections(self):
        r = requests.get(f"{API}/academic/sections", headers=_hdr("principal"), timeout=15)
        assert r.status_code == 200, r.text
        sections = r.json()
        assert isinstance(sections, list)
        if sections:
            assert "label" in sections[0]
            assert "id" in sections[0]

    def test_teacher_attendance_sections_scoped(self):
        r = requests.get(f"{API}/academic/sections/for-attendance", headers=_hdr("teacher"), timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        labels = [s["label"] for s in data.get("sections", [])]
        assert "10-B" not in labels, "Teacher should not see unassigned section 10-B"
        if labels:
            assert "9-A" in labels, "Seeded teacher should be assigned to 9-A"

    def test_principal_sees_all_attendance_sections(self):
        tr = requests.get(f"{API}/academic/sections/for-attendance", headers=_hdr("teacher"), timeout=15)
        pr = requests.get(f"{API}/academic/sections/for-attendance", headers=_hdr("principal"), timeout=15)
        assert pr.status_code == 200, pr.text
        teacher_labels = {s["label"] for s in tr.json().get("sections", [])}
        principal_labels = {s["label"] for s in pr.json().get("sections", [])}
        if teacher_labels:
            assert teacher_labels.issubset(principal_labels)

    def test_teacher_cannot_query_unassigned_section_students(self):
        pr = requests.get(f"{API}/academic/sections", headers=_hdr("principal"), timeout=15)
        if pr.status_code != 200:
            pytest.skip("Principal cannot list sections on this environment")
        ten_b = next((s for s in pr.json() if s.get("label") == "10-B"), None)
        if not ten_b:
            pytest.skip("Section 10-B not seeded")
        r = requests.get(
            f"{API}/people",
            headers=_hdr("teacher"),
            params={"kind": "student", "section_id": ten_b["id"]},
            timeout=15,
        )
        assert r.status_code == 403, r.text

    def test_teacher_lists_assigned_section_students(self):
        ar = requests.get(f"{API}/academic/sections/for-attendance", headers=_hdr("teacher"), timeout=15)
        sections = ar.json().get("sections", [])
        if not sections:
            pytest.skip("No sections assigned to teacher")
        sid = sections[0]["id"]
        r = requests.get(
            f"{API}/people",
            headers=_hdr("teacher"),
            params={"kind": "student", "section_id": sid},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)

    def test_teacher_batch_attendance_rejects_unassigned_section(self):
        pr = requests.get(f"{API}/academic/sections", headers=_hdr("principal"), timeout=15)
        ten_b = next((s for s in pr.json() if s.get("label") == "10-B"), None) if pr.status_code == 200 else None
        if not ten_b:
            pytest.skip("Section 10-B not seeded")
        r = requests.post(
            f"{API}/attendance/batch",
            headers=_hdr("teacher"),
            json={
                "date": "2026-07-11",
                "kind": "student",
                "group": "10-B",
                "section_id": ten_b["id"],
                "session": None,
                "sport": None,
                "marks": [],
            },
            timeout=15,
        )
        assert r.status_code == 403, r.text

    def test_teacher_batch_rejects_student_from_wrong_section(self):
        assigned = requests.get(f"{API}/academic/sections/for-attendance", headers=_hdr("teacher"), timeout=15)
        if assigned.status_code != 200 or not assigned.json().get("sections"):
            pytest.skip("No teacher-assigned section")
        my_section = assigned.json()["sections"][0]
        pr = requests.get(f"{API}/academic/sections", headers=_hdr("principal"), timeout=15)
        if pr.status_code != 200:
            pytest.skip("Cannot list sections")
        other = next((s for s in pr.json() if s["id"] != my_section["id"]), None)
        if not other:
            pytest.skip("No alternate section")
        wrong_students = requests.get(
            f"{API}/people",
            headers=_hdr("principal"),
            params={"kind": "student", "section_id": other["id"]},
            timeout=15,
        )
        if wrong_students.status_code != 200 or not wrong_students.json():
            pytest.skip("No students in other section")
        wrong_id = wrong_students.json()[0]["id"]
        r = requests.post(
            f"{API}/attendance/batch",
            headers=_hdr("teacher"),
            json={
                "date": "2026-07-11",
                "kind": "student",
                "group": my_section.get("label", "9-A"),
                "section_id": my_section["id"],
                "session": None,
                "sport": None,
                "marks": [{"person_id": wrong_id, "status": "present"}],
            },
            timeout=15,
        )
        assert r.status_code == 403, r.text

    def test_academic_years_list(self):
        r = requests.get(f"{API}/academic/years", headers=_hdr("principal"), timeout=15)
        assert r.status_code == 200, r.text
        years = r.json()
        assert isinstance(years, list)
        if years:
            assert "status" in years[0]
            assert years[0]["status"] in ("open", "closed", "archived")

    def test_archived_year_blocks_grade_create(self):
        """Archive a throwaway year, never the shared open one.

        Archiving is a one-way transition for non-super-admins, so archiving the
        live year here used to leave the whole database without an open academic
        year — which silently disables student attendance and section pickers.
        """
        import uuid as _uuid

        created = requests.post(
            f"{API}/academic/years",
            headers=_hdr("principal"),
            json={
                "name": f"ZZ-test-{_uuid.uuid4().hex[:6]}",
                "entity_id": "pws",
                "start_date": "2019-04-01",
                "end_date": "2020-03-31",
            },
            timeout=15,
        )
        if created.status_code not in (200, 201):
            pytest.skip(f"Could not create a scratch academic year: {created.text}")
        year_id = created.json()["id"]

        archived = requests.patch(
            f"{API}/academic/years/{year_id}/status",
            headers=_hdr("principal"),
            json={"status": "archived"},
            timeout=15,
        )
        assert archived.status_code == 200, archived.text

        r = requests.post(
            f"{API}/academic/grades",
            headers=_hdr("principal"),
            json={"academic_year_id": year_id, "name": "11", "entity_id": "pws"},
            timeout=15,
        )
        assert r.status_code == 403, r.text

    def test_open_academic_year_exists(self):
        """Guards the invariant the suite used to destroy."""
        r = requests.get(f"{API}/academic/sections/for-attendance",
                         headers=_hdr("super_admin"), timeout=15)
        assert r.status_code == 200, r.text
        assert r.json().get("academic_year"), (
            "No open PWS academic year — student attendance and section pickers "
            "will be empty across the app."
        )

    def test_teacher_subjects_scoped_in_section(self):
        sec_r = requests.get(f"{API}/academic/sections/for-attendance", headers=_hdr("teacher"), timeout=15)
        if sec_r.status_code != 200 or not sec_r.json().get("sections"):
            pytest.skip("No teacher sections")
        sid = sec_r.json()["sections"][0]["id"]
        year_id = sec_r.json().get("academic_year", {}).get("id")
        sub_r = requests.get(
            f"{API}/academic/subjects",
            headers=_hdr("teacher"),
            params={"section_id": sid, "academic_year_id": year_id},
            timeout=15,
        )
        assert sub_r.status_code == 200, sub_r.text
        pr_subs = requests.get(
            f"{API}/academic/subjects",
            headers=_hdr("principal"),
            params={"academic_year_id": year_id},
            timeout=15,
        )
        if pr_subs.status_code == 200 and sub_r.json() and pr_subs.json():
            teacher_ids = {s["id"] for s in sub_r.json()}
            principal_ids = {s["id"] for s in pr_subs.json()}
            assert teacher_ids.issubset(principal_ids)

    def test_teacher_my_assignments(self):
        r = requests.get(f"{API}/academic/my-assignments", headers=_hdr("teacher"), timeout=15)
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)

    def test_class_assignments_list(self):
        r = requests.get(f"{API}/academic/class-assignments", headers=_hdr("principal"), timeout=15)
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)
