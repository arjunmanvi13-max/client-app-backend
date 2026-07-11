"""Coach assessment MVP — role access and published parent view."""
import os
import datetime
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "https://unified-track.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "coach": ("coach@prarambhika.com", "Coach@123"),
    "parent_alpha": ("parent_alpha@prarambhika.com", "Parent@123"),
    "admin": ("admin@prarambhika.com", "Admin@123"),
}
LEGACY_CREDS = {
    "coach": ("coach@pws-alpha.com", "Coach@123"),
    "parent_alpha": ("parent_alpha@pws-alpha.com", "Parent@123"),
    "admin": ("admin@pws-alpha.com", "Admin@123"),
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


@pytest.mark.integration
class TestCoachAssessmentMVP:
    def test_coach_lists_definitions(self):
        r = requests.get(
            f"{API}/coach-assessments/definitions",
            headers=_hdr("coach"),
            params={"sport": "Cricket", "centre": "Balua"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)

    def test_coach_cannot_access_academic_marks(self):
        r = requests.get(f"{API}/marks/sections", headers=_hdr("coach"), timeout=15)
        assert r.status_code == 403, r.text

    def test_coach_grid_scoped_players(self):
        defs = requests.get(f"{API}/coach-assessments/definitions", headers=_hdr("coach"), timeout=15)
        if defs.status_code != 200 or not defs.json():
            pytest.skip("No assessment definitions")
        def_id = defs.json()[0]["id"]
        today = datetime.date.today().isoformat()
        r = requests.get(
            f"{API}/coach-assessments/grid",
            headers=_hdr("coach"),
            params={"definition_id": def_id, "date": today, "centre": "Balua", "sport": "Cricket", "slot": "Morning"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert "players" in r.json()

    def test_coach_save_with_required_fields(self):
        defs = requests.get(f"{API}/coach-assessments/definitions", headers=_hdr("coach"), timeout=15)
        players = requests.get(
            f"{API}/coach/players",
            headers=_hdr("coach"),
            params={"centre": "Balua", "sport": "Cricket", "slot": "Morning"},
            timeout=15,
        )
        if defs.status_code != 200 or not defs.json() or players.status_code != 200 or not players.json().get("players"):
            pytest.skip("No definitions or players")
        defn = next((d for d in defs.json() if d.get("assessment_type") == "score"), defs.json()[0])
        player = players.json()["players"][0]
        today = datetime.date.today().isoformat()
        r = requests.post(
            f"{API}/coach-assessments/batch",
            headers=_hdr("coach"),
            json={
                "definition_id": defn["id"],
                "date": today,
                "centre": "Balua",
                "sport": "Cricket",
                "slot": "Morning",
                "status": "draft",
                "entries": [{"player_id": player["id"], "score": 65, "coach_remark": "Good effort"}],
            },
            timeout=15,
        )
        assert r.status_code == 200, r.text
        grid = requests.get(
            f"{API}/coach-assessments/grid",
            headers=_hdr("coach"),
            params={"definition_id": defn["id"], "date": today, "centre": "Balua", "sport": "Cricket", "slot": "Morning"},
            timeout=15,
        )
        row = next((p for p in grid.json().get("players", []) if p["player_id"] == player["id"]), None)
        assert row and row.get("score") == 65
        assert row.get("coach_remark") == "Good effort"

    def test_score_exceeds_max_rejected(self):
        defs = requests.get(f"{API}/coach-assessments/definitions", headers=_hdr("coach"), timeout=15)
        players = requests.get(f"{API}/coach/players", headers=_hdr("coach"), params={"centre": "Balua", "sport": "Cricket", "slot": "Morning"}, timeout=15)
        if not defs.json() or not players.json().get("players"):
            pytest.skip("No data")
        defn = next((d for d in defs.json() if d.get("max_score")), None)
        if not defn:
            pytest.skip("No scored definition")
        player = players.json()["players"][0]
        r = requests.post(
            f"{API}/coach-assessments/batch",
            headers=_hdr("coach"),
            json={
                "definition_id": defn["id"],
                "date": datetime.date.today().isoformat(),
                "centre": "Balua",
                "sport": "Cricket",
                "slot": "Morning",
                "status": "draft",
                "entries": [{"player_id": player["id"], "score": defn["max_score"] + 50}],
            },
            timeout=15,
        )
        assert r.status_code == 400

    def test_parent_published_coach_assessments(self):
        wards = requests.get(f"{API}/parent/wards", headers=_hdr("parent_alpha"), timeout=15)
        if wards.status_code != 200 or not wards.json():
            pytest.skip("No ALPHA parent wards")
        player_wards = [w for w in wards.json() if w.get("kind") == "player"]
        if not player_wards:
            pytest.skip("No player wards")
        wid = player_wards[0]["id"]
        r = requests.get(f"{API}/parent/coach-assessments/{wid}", headers=_hdr("parent_alpha"), timeout=15)
        assert r.status_code == 200, r.text
        for a in r.json().get("assessments", []):
            assert a.get("status") == "published"

    def test_admin_create_definition(self):
        r = requests.post(
            f"{API}/coach-assessments/definitions",
            headers=_hdr("admin"),
            json={
                "name": f"Test Def {datetime.date.today().isoformat()}",
                "assessment_type": "rating",
                "sport": "Cricket",
                "centre": "Balua",
                "slot": "Morning",
            },
            timeout=15,
        )
        assert r.status_code == 200, r.text
