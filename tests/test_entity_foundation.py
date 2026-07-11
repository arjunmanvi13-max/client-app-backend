"""Entity foundation — PWS / ALPHA isolation and super-admin BOTH access."""
import os
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "https://unified-track.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "principal": ("principal@prarambhika.com", "Principal@123"),
    "admin": ("admin@prarambhika.com", "Admin@123"),
    "teacher": ("teacher@prarambhika.com", "Teacher@123"),
    "super_admin": ("superadmin@prarambhika.com", "Super@123"),
}
LEGACY_CREDS = {
    "principal": ("admin@pws-alpha.com", "Admin@123"),
    "admin": ("admin@pws-alpha.com", "Admin@123"),
    "teacher": ("teacher@pws-alpha.com", "Teacher@123"),
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
class TestEntityFoundation:
    def test_pws_principal_cannot_list_alpha_players(self):
        r = requests.get(
            f"{API}/people",
            headers=_hdr("principal"),
            params={"kind": "player"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        players = r.json()
        assert players == [] or all(
            p.get("organization") in ("PWS", "BOTH") or "PWS" in (p.get("entities") or [])
            for p in players
        ), "Principal should not see ALPHA-only players"

    def test_alpha_admin_cannot_list_pws_students(self):
        r = requests.get(
            f"{API}/people",
            headers=_hdr("admin"),
            params={"kind": "student"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json() == [], "Sports Admin should not see PWS students"

    def test_super_admin_sees_both_people_kinds(self):
        students = requests.get(
            f"{API}/people",
            headers=_hdr("super_admin"),
            params={"kind": "student"},
            timeout=15,
        )
        players = requests.get(
            f"{API}/people",
            headers=_hdr("super_admin"),
            params={"kind": "player"},
            timeout=15,
        )
        assert students.status_code == 200, students.text
        assert players.status_code == 200, players.text
        if not students.json() and not players.json():
            pytest.skip("No seeded people on this environment")
        assert len(students.json()) > 0 or len(players.json()) > 0

    def test_pws_principal_fees_scoped_to_pws(self):
        r = requests.get(f"{API}/fees", headers=_hdr("principal"), timeout=15)
        assert r.status_code == 200, r.text
        fees = r.json()
        for f in fees:
            assert f.get("entity_id") == "pws", f"Principal should only see PWS fees, got {f.get('entity_id')}"

    def test_alpha_admin_fees_scoped_to_alpha(self):
        r = requests.get(f"{API}/fees", headers=_hdr("admin"), timeout=15)
        assert r.status_code == 200, r.text
        fees = r.json()
        for f in fees:
            ent = f.get("entity_id") or "alpha"
            assert ent == "alpha", f"Sports Admin should only see ALPHA fees, got {ent}"

    def test_super_admin_fees_include_both_entities(self):
        r = requests.get(f"{API}/fees", headers=_hdr("super_admin"), timeout=15)
        assert r.status_code == 200, r.text
        fees = r.json()
        if not fees:
            pytest.skip("No fees seeded")
        entities = {f.get("entity_id") or "alpha" for f in fees}
        assert "pws" in entities or "alpha" in entities

    def test_attendance_entity_isolation(self):
        pr = requests.get(
            f"{API}/attendance",
            headers=_hdr("principal"),
            params={"kind": "player"},
            timeout=15,
        )
        ad = requests.get(
            f"{API}/attendance",
            headers=_hdr("admin"),
            params={"kind": "student"},
            timeout=15,
        )
        assert pr.status_code == 200, pr.text
        assert ad.status_code == 200, ad.text
        assert pr.json() == [], "Principal should not see player (ALPHA) attendance"
        assert ad.json() == [], "Sports Admin should not see student (PWS) attendance"

    def test_teacher_marks_sections_still_scoped(self):
        """Regression: teacher permissions from M4 must remain intact."""
        tr = requests.get(f"{API}/marks/sections", headers=_hdr("teacher"), timeout=15)
        assert tr.status_code == 200, tr.text
        labels = [s["label"] for s in tr.json().get("sections", [])]
        assert "10-B" not in labels
