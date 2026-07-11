"""Milestone 7 — academic marks & assessment MVP."""
import os
import datetime
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "https://unified-track.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "principal": ("principal@prarambhika.com", "Principal@123"),
    "teacher": ("teacher@prarambhika.com", "Teacher@123"),
    "parent": ("parent_pws@prarambhika.com", "Parent@123"),
    "super_admin": ("superadmin@prarambhika.com", "Super@123"),
}
LEGACY_CREDS = {
    "principal": ("admin@pws-alpha.com", "Admin@123"),
    "teacher": ("teacher@pws-alpha.com", "Teacher@123"),
    "parent": ("parent@pws-alpha.com", "Parent@123"),
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


def _open_year(role="principal"):
    r = requests.get(f"{API}/academic/years", headers=_hdr(role), timeout=15)
    if r.status_code != 200:
        pytest.skip("Cannot list academic years")
    years = r.json()
    return next((y for y in years if y.get("status") == "open"), years[0] if years else None)


def _first_assessment(role="principal"):
    year = _open_year(role)
    if not year:
        pytest.skip("No academic year")
    r = requests.get(
        f"{API}/marks/assessments",
        headers=_hdr(role),
        params={"academic_year_id": year["id"]},
        timeout=15,
    )
    if r.status_code != 200 or not r.json():
        pytest.skip("No assessments seeded")
    return r.json()[0]


@pytest.mark.integration
class TestAcademicMarksAPI:
    def test_teacher_my_combinations_scoped(self):
        r = requests.get(f"{API}/marks/my-combinations", headers=_hdr("teacher"), timeout=15)
        assert r.status_code == 200, r.text
        combos = r.json().get("combinations", [])
        labels = [c.get("section_label") for c in combos]
        assert "10-B" not in labels
        if labels:
            assert "9-A" in labels

    def test_teacher_cannot_load_unassigned_grid(self):
        pr = requests.get(f"{API}/academic/sections", headers=_hdr("principal"), timeout=15)
        ten_b = next((s for s in pr.json() if s.get("label") == "10-B"), None) if pr.status_code == 200 else None
        if not ten_b:
            pytest.skip("Section 10-B not seeded")
        year = _open_year()
        asm = requests.get(
            f"{API}/marks/assessments",
            headers=_hdr("principal"),
            params={"academic_year_id": year["id"], "section_id": ten_b["id"]},
            timeout=15,
        )
        if asm.status_code != 200 or not asm.json():
            pytest.skip("No assessment for 10-B")
        r = requests.get(
            f"{API}/marks/grid",
            headers=_hdr("teacher"),
            params={"assessment_id": asm.json()[0]["id"]},
            timeout=15,
        )
        assert r.status_code == 403, r.text

    def test_marks_exceed_max_rejected(self):
        asm = _first_assessment("principal")
        grid = requests.get(
            f"{API}/marks/grid",
            headers=_hdr("principal"),
            params={"assessment_id": asm["id"]},
            timeout=15,
        )
        if grid.status_code != 200 or not grid.json().get("students"):
            pytest.skip("No students in grid")
        target = grid.json()["students"][0]
        max_m = asm.get("max_marks", 100)
        batch = requests.post(
            f"{API}/marks/batch",
            headers=_hdr("principal"),
            json={
                "assessment_id": asm["id"],
                "status": "draft",
                "entries": [{"person_id": target["person_id"], "marks_obtained": max_m + 10}],
            },
            timeout=15,
        )
        assert batch.status_code == 400, batch.text

    def test_teacher_batch_save_draft_has_entered_fields(self):
        asm = _first_assessment("teacher")
        grid = requests.get(
            f"{API}/marks/grid",
            headers=_hdr("teacher"),
            params={"assessment_id": asm["id"]},
            timeout=15,
        )
        if grid.status_code != 200 or not grid.json().get("students"):
            pytest.skip("No students for teacher")
        target = grid.json()["students"][0]
        score = 42.0
        batch = requests.post(
            f"{API}/marks/batch",
            headers=_hdr("teacher"),
            json={
                "assessment_id": asm["id"],
                "status": "draft",
                "entries": [{"person_id": target["person_id"], "marks_obtained": score}],
            },
            timeout=15,
        )
        assert batch.status_code == 200, batch.text
        grid2 = requests.get(
            f"{API}/marks/grid",
            headers=_hdr("teacher"),
            params={"assessment_id": asm["id"]},
            timeout=15,
        )
        row = next((s for s in grid2.json().get("students", []) if s["person_id"] == target["person_id"]), None)
        assert row and row.get("marks_obtained") == score
        assert row.get("entered_at") or row.get("status") == "draft"

    def test_parent_sees_only_published_marks(self):
        wards = requests.get(f"{API}/parent/wards", headers=_hdr("parent"), timeout=15)
        if wards.status_code != 200 or not wards.json():
            pytest.skip("No parent wards")
        ward_id = wards.json()[0]["id"]
        r = requests.get(f"{API}/parent/marks/{ward_id}", headers=_hdr("parent"), timeout=15)
        assert r.status_code == 200, r.text
        for m in r.json().get("marks", []):
            assert m.get("status") == "published"

    def test_principal_can_create_exam_term(self):
        year = _open_year()
        if not year:
            pytest.skip("No year")
        r = requests.post(
            f"{API}/marks/exam-terms",
            headers=_hdr("principal"),
            json={"academic_year_id": year["id"], "name": f"Test Term {datetime.date.today().isoformat()}"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert "id" in r.json()

    def test_legacy_academic_exam_terms_alias(self):
        year = _open_year()
        if not year:
            pytest.skip("No year")
        r = requests.get(
            f"{API}/academic/exam-terms",
            headers=_hdr("principal"),
            params={"academic_year_id": year["id"]},
            timeout=15,
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_correction_requires_reason(self):
        asm = _first_assessment("principal")
        grid = requests.get(f"{API}/marks/grid", headers=_hdr("principal"), params={"assessment_id": asm["id"]}, timeout=15)
        if not grid.json().get("students"):
            pytest.skip("No students")
        pid = grid.json()["students"][0]["person_id"]
        requests.post(
            f"{API}/marks/batch",
            headers=_hdr("principal"),
            json={"assessment_id": asm["id"], "status": "final", "entries": [{"person_id": pid, "marks_obtained": 55}]},
            timeout=15,
        )
        staff = requests.get(f"{API}/marks/student/{pid}", headers=_hdr("principal"), timeout=15)
        mark = next((m for m in staff.json().get("marks", []) if m.get("assessment_id") == asm["id"]), None)
        if not mark:
            pytest.skip("Mark not saved")
        r = requests.post(
            f"{API}/marks/correct",
            headers=_hdr("principal"),
            json={"mark_id": mark["id"], "marks_obtained": 60, "reason": ""},
            timeout=15,
        )
        assert r.status_code == 400
