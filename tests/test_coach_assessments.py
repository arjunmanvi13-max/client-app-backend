"""Player assessment — sport-specific technical sub-scores, player-type setup."""
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

CRICKET_TECH = {
    "batting": 7, "bowling": 6, "fielding": 8,
    "wicket_keeping": 6, "running_between_wickets": 7, "cricket_iq": 8,
}


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
class TestPlayerAssessment:
    def test_metadata_includes_player_types_and_technical(self):
        r = requests.get(f"{API}/coach-assessments/metadata", headers=_hdr("coach"), timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "Daily" in data.get("player_types", [])
        assert len(data.get("cricket_technical", [])) == 6
        assert data.get("score_scale", [])[0]["label"] == "Beginner"

    def test_grid_requires_player_type(self):
        today = datetime.date.today().isoformat()
        r = requests.get(
            f"{API}/coach-assessments/grid",
            headers=_hdr("coach"),
            params={
                "centre": "Balua",
                "sport": "Cricket",
                "player_type": "Daily",
                "session": "Morning",
                "assessment_stage": "week_1_baseline",
                "date": today,
            },
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("schema_version") == 3

    def test_daily_requires_session(self):
        today = datetime.date.today().isoformat()
        r = requests.get(
            f"{API}/coach-assessments/grid",
            headers=_hdr("coach"),
            params={
                "centre": "Balua",
                "sport": "Cricket",
                "player_type": "Daily",
                "assessment_stage": "week_1_baseline",
                "date": today,
            },
            timeout=15,
        )
        assert r.status_code == 400, r.text

    def test_save_draft_with_technical_sub_scores(self):
        players = requests.get(
            f"{API}/coach/players",
            headers=_hdr("coach"),
            params={"centre": "Balua", "sport": "Cricket", "slot": "Morning"},
            timeout=15,
        )
        if players.status_code != 200 or not players.json().get("players"):
            pytest.skip("No players")
        player = players.json()["players"][0]
        today = datetime.date.today().isoformat()
        r = requests.post(
            f"{API}/coach-assessments/batch",
            headers=_hdr("coach"),
            json={
                "centre": "Balua",
                "sport": "Cricket",
                "player_type": "Daily",
                "session": "Morning",
                "assessment_stage": "week_4_progress",
                "date": today,
                "status": "draft",
                "entries": [{
                    "player_id": player["id"],
                    "technical_sub": CRICKET_TECH,
                    "strength_conditioning": 7,
                    "game_awareness": 8,
                    "mental_attributes": 7,
                    "training_attitude": 9,
                    "coach_remark": "Solid session",
                }],
            },
            timeout=15,
        )
        assert r.status_code == 200, r.text

    def test_finalize_requires_all_scores(self):
        players = requests.get(f"{API}/coach/players", headers=_hdr("coach"), params={"centre": "Balua", "sport": "Cricket", "slot": "Morning"}, timeout=15)
        if not players.json().get("players"):
            pytest.skip("No players")
        player = players.json()["players"][0]
        today = datetime.date.today().isoformat()
        r = requests.post(
            f"{API}/coach-assessments/batch",
            headers=_hdr("coach"),
            json={
                "centre": "Balua",
                "sport": "Cricket",
                "player_type": "Daily",
                "session": "Morning",
                "assessment_stage": "week_8_12_final",
                "date": today,
                "status": "final",
                "entries": [{"player_id": player["id"], "technical_sub": {"batting": 5}, "coach_remark": "Incomplete"}],
            },
            timeout=15,
        )
        assert r.status_code == 400, r.text

    def test_parent_published_assessments(self):
        wards = requests.get(f"{API}/parent/wards", headers=_hdr("parent_alpha"), timeout=15)
        if wards.status_code != 200 or not wards.json():
            pytest.skip("No ALPHA parent wards")
        player_wards = [w for w in wards.json() if w.get("kind") == "player"]
        if not player_wards:
            pytest.skip("No player wards")
        wid = player_wards[0]["id"]
        r = requests.get(f"{API}/parent/coach-assessments/{wid}", headers=_hdr("parent_alpha"), timeout=15)
        assert r.status_code == 200, r.text

    def test_admin_publish_batch(self):
        today = datetime.date.today().isoformat()
        r = requests.post(
            f"{API}/coach-assessments/publish",
            headers=_hdr("admin"),
            json={
                "centre": "Balua",
                "sport": "Cricket",
                "player_type": "Daily",
                "session": "Morning",
                "assessment_stage": "week_1_baseline",
                "date": today,
            },
            timeout=15,
        )
        assert r.status_code == 200, r.text
