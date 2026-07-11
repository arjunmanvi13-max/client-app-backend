"""People enrollment workflow — permissions, duplicates, search."""
import os
import uuid
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "https://unified-track.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "principal": ("principal@prarambhika.com", "Principal@123"),
    "teacher": ("teacher@prarambhika.com", "Teacher@123"),
    "coach": ("coach@prarambhika.com", "Coach@123"),
    "parent": ("parent_pws@prarambhika.com", "Parent@123"),
    "super_admin": ("superadmin@prarambhika.com", "Super@123"),
}
LEGACY_CREDS = {
    "principal": ("admin@pws-alpha.com", "Admin@123"),
    "teacher": ("teacher@pws-alpha.com", "Teacher@123"),
    "coach": ("coach@pws-alpha.com", "Coach@123"),
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


@pytest.mark.integration
class TestPeopleWorkflow:
    def test_teacher_cannot_create_student(self):
        r = requests.post(
            f"{API}/people",
            headers=_hdr("teacher"),
            json={
                "name": "Blocked Student",
                "kind": "student",
                "organization": "PWS",
            },
            timeout=15,
        )
        assert r.status_code == 403, r.text

    def test_duplicate_admission_number_rejected(self):
        students = requests.get(
            f"{API}/people",
            headers=_hdr("principal"),
            params={"kind": "student"},
            timeout=15,
        )
        if students.status_code != 200 or not students.json():
            pytest.skip("No students seeded")
        existing = next((s for s in students.json() if s.get("admission_number")), None)
        if not existing:
            pytest.skip("No admission numbers seeded")
        r = requests.post(
            f"{API}/people",
            headers=_hdr("principal"),
            json={
                "name": "Duplicate Test",
                "kind": "student",
                "admission_number": existing["admission_number"],
                "organization": "PWS",
            },
            timeout=15,
        )
        assert r.status_code == 409, r.text

    def test_search_students_by_name(self):
        r = requests.get(
            f"{API}/people",
            headers=_hdr("principal"),
            params={"kind": "student", "q": "Aarav"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        names = [p["name"] for p in r.json()]
        assert any("Aarav" in n for n in names)

    def test_get_person_by_id(self):
        lst = requests.get(
            f"{API}/people",
            headers=_hdr("principal"),
            params={"kind": "student"},
            timeout=15,
        )
        if lst.status_code != 200 or not lst.json():
            pytest.skip("No students")
        pid = lst.json()[0]["id"]
        r = requests.get(f"{API}/people/{pid}", headers=_hdr("principal"), timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()["id"] == pid

    def test_parent_sees_only_linked_children(self):
        wards = requests.get(f"{API}/parent/wards", headers=_hdr("parent"), timeout=15)
        if wards.status_code != 200:
            pytest.skip("Parent wards unavailable")
        r = requests.get(f"{API}/people", headers=_hdr("parent"), timeout=15)
        assert r.status_code == 200, r.text
        ward_ids = {w["id"] for w in wards.json()}
        for p in r.json():
            assert p["id"] in ward_ids

    def test_coach_player_list_scoped(self):
        all_players = requests.get(
            f"{API}/people",
            headers=_hdr("super_admin"),
            params={"kind": "player"},
            timeout=15,
        )
        coach_players = requests.get(
            f"{API}/people",
            headers=_hdr("coach"),
            params={"kind": "player"},
            timeout=15,
        )
        assert coach_players.status_code == 200, coach_players.text
        if all_players.status_code != 200:
            pytest.skip("Cannot compare player lists")
        coach_ids = {p["id"] for p in coach_players.json()}
        all_ids = {p["id"] for p in all_players.json()}
        assert coach_ids.issubset(all_ids)
        if coach_players.json():
            for p in coach_players.json():
                assert p.get("centre") in ("Balua", None) or p.get("centre") == "Balua"

    def test_principal_deactivate_student_via_approval(self):
        lst = requests.get(
            f"{API}/people",
            headers=_hdr("principal"),
            params={"kind": "student", "q": "Rohit"},
            timeout=15,
        )
        if lst.status_code != 200 or not lst.json():
            pytest.skip("No student Rohit")
        pid = lst.json()[0]["id"]
        d = requests.post(f"{API}/people/{pid}/deactivate", headers=_hdr("principal"), timeout=15)
        assert d.status_code == 200, d.text
        body = d.json()
        if body.get("approval_required"):
            aid = body["approval"]["id"]
            ar = requests.post(f"{API}/approval-requests/{aid}/approve", headers=_hdr("super_admin"), json={}, timeout=15)
            assert ar.status_code == 200, ar.text
            requests.post(f"{API}/people/{pid}/activate", headers=_hdr("super_admin"), timeout=15)
        else:
            a = requests.post(f"{API}/people/{pid}/activate", headers=_hdr("principal"), timeout=15)
            assert a.status_code == 200, a.text
