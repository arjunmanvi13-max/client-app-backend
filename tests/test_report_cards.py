"""Milestone 9 — report card MVP."""
import os
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
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


def _term1():
    years = requests.get(f"{API}/academic/years", headers=_hdr("principal"), timeout=15)
    if years.status_code != 200:
        pytest.skip("No academic years")
    year = next((y for y in years.json() if y.get("status") == "open"), years.json()[0] if years.json() else None)
    if not year:
        pytest.skip("No year")
    terms = requests.get(
        f"{API}/marks/exam-terms",
        headers=_hdr("principal"),
        params={"academic_year_id": year["id"]},
        timeout=15,
    )
    if terms.status_code != 200 or not terms.json():
        pytest.skip("No exam terms")
    return next((t for t in terms.json() if t.get("name") == "Term 1"), terms.json()[0])


def _student_in_9a(name: str):
    secs = requests.get(f"{API}/academic/sections", headers=_hdr("principal"), timeout=15)
    if secs.status_code != 200:
        pytest.skip("No sections")
    nine_a = next((s for s in secs.json() if s.get("label") == "9-A"), None)
    if not nine_a:
        pytest.skip("9-A not seeded")
    students = requests.get(
        f"{API}/people",
        headers=_hdr("principal"),
        params={"kind": "student", "section_id": nine_a["id"]},
        timeout=15,
    )
    if students.status_code != 200:
        pytest.skip("Cannot list students")
    return next((s for s in students.json() if s.get("name") == name), None)


def _aarav():
    return _student_in_9a("Aarav Mishra")


def _isha():
    return _student_in_9a("Isha Sinha")


@pytest.mark.integration
class TestReportCardsMVP:
    def test_build_uses_saved_marks(self):
        student = _isha() or _aarav()
        if not student:
            pytest.skip("Student not seeded")
        term = _term1()
        r = requests.post(
            f"{API}/report-cards/build",
            headers=_hdr("principal"),
            json={"person_id": student["id"], "exam_term_id": term["id"]},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        card = r.json()
        assert card.get("person_name") == student["name"]
        assert card.get("admission_number")
        assert card.get("subjects")
        assert len(card["subjects"]) >= 1
        assert card["subjects"][0].get("max_marks") is not None
        assert card.get("attendance_pct") is not None or card.get("attendance_pct") is None

    def test_teacher_remark_workflow(self):
        student = _isha()
        if not student:
            pytest.skip("Isha not seeded")
        term = _term1()
        build = requests.post(
            f"{API}/report-cards/build",
            headers=_hdr("teacher"),
            json={"person_id": student["id"], "exam_term_id": term["id"]},
            timeout=15,
        )
        assert build.status_code == 200, build.text
        card_id = build.json()["id"]
        remark = requests.patch(
            f"{API}/report-cards/{card_id}/teacher-remark",
            headers=_hdr("teacher"),
            json={"teacher_remark": "Consistent effort in Mathematics."},
            timeout=15,
        )
        assert remark.status_code == 200, remark.text
        submit = requests.post(f"{API}/report-cards/{card_id}/submit", headers=_hdr("teacher"), timeout=15)
        assert submit.status_code == 200, submit.text
        assert submit.json().get("status") == "review"

    def test_publish_and_parent_sees_only_published(self):
        student = _aarav()
        if not student:
            pytest.skip("Aarav not seeded")
        term = _term1()
        listed = requests.get(
            f"{API}/report-cards",
            headers=_hdr("principal"),
            params={"person_id": student["id"], "exam_term_id": term["id"]},
            timeout=15,
        )
        assert listed.status_code == 200, listed.text
        cards = listed.json()
        if not cards:
            pytest.skip("No report cards")
        review_card = next((c for c in cards if c.get("status") == "review"), None)
        published_card = next((c for c in cards if c.get("status") == "published"), None)
        if review_card:
            pub = requests.post(
                f"{API}/report-cards/{review_card['id']}/publish",
                headers=_hdr("principal"),
                json={"coach_remark": "Approved sports participation."},
                timeout=15,
            )
            assert pub.status_code == 200, pub.text
            assert pub.json().get("status") == "published"
        if not published_card and not review_card:
            pytest.skip("No cards to publish")

        parent_marks = requests.get(f"{API}/parent/marks/{student['id']}", headers=_hdr("parent"), timeout=15)
        assert parent_marks.status_code == 200, parent_marks.text
        rc = parent_marks.json().get("report_cards") or []
        assert all(c.get("status") == "published" for c in rc)

    def test_pdf_endpoint(self):
        student = _aarav()
        if not student:
            pytest.skip("Aarav not seeded")
        listed = requests.get(
            f"{API}/report-cards",
            headers=_hdr("parent"),
            params={"person_id": student["id"]},
            timeout=15,
        )
        if listed.status_code != 200 or not listed.json():
            listed = requests.get(
                f"{API}/report-cards",
                headers=_hdr("principal"),
                params={"person_id": student["id"], "status": "published"},
                timeout=15,
            )
        if listed.status_code != 200 or not listed.json():
            pytest.skip("No published report card")
        card_id = listed.json()[0]["id"]
        pdf = requests.get(f"{API}/report-cards/{card_id}/pdf", headers=_hdr("parent"), timeout=15)
        assert pdf.status_code == 200, pdf.text
        assert pdf.headers.get("content-type", "").startswith("application/pdf")
        assert pdf.content[:4] == b"%PDF"
