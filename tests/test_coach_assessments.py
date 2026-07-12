"""Player assessment — v4 deep technical sub-parameters, 4-term layout."""
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


def _cricket_detail():
    from assessment_schema import empty_technical_detail
    detail = empty_technical_detail("Cricket")
    for area in detail:
        for k in detail[area]:
            detail[area][k] = 7
    return detail


@pytest.mark.integration
class TestPlayerAssessment:
    def test_metadata_includes_four_stages_and_na_scale(self):
        r = requests.get(f"{API}/coach-assessments/metadata", headers=_hdr("coach"), timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "Daily" in data.get("player_types", [])
        assert len(data.get("cricket_technical", [])) == 6
        assert data.get("cricket_technical", [])[0].get("sub_params")
        assert data.get("score_scale", [])[0]["label"] == "N/A"
        stage_ids = [s["id"] for s in data.get("stages", [])]
        assert "assessment_1" in stage_ids
        assert "assessment_4" in stage_ids

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
                "assessment_stage": "assessment_1",
                "date": today,
            },
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("schema_version") == 4

    def test_daily_requires_session(self):
        today = datetime.date.today().isoformat()
        r = requests.get(
            f"{API}/coach-assessments/grid",
            headers=_hdr("coach"),
            params={
                "centre": "Balua",
                "sport": "Cricket",
                "player_type": "Daily",
                "assessment_stage": "assessment_1",
                "date": today,
            },
            timeout=15,
        )
        assert r.status_code == 400, r.text

    def test_save_draft_with_technical_detail(self):
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
                "assessment_stage": "assessment_2",
                "date": today,
                "status": "draft",
                "entries": [{
                    "player_id": player["id"],
                    "technical_detail": _cricket_detail(),
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

    def test_single_player_auto_save(self):
        players = requests.get(f"{API}/coach/players", headers=_hdr("coach"), params={"centre": "Balua", "sport": "Cricket", "slot": "Morning"}, timeout=15)
        if not players.json().get("players"):
            pytest.skip("No players")
        player = players.json()["players"][0]
        today = datetime.date.today().isoformat()
        r = requests.post(
            f"{API}/coach-assessments/player",
            headers=_hdr("coach"),
            json={
                "centre": "Balua",
                "sport": "Cricket",
                "player_type": "Daily",
                "session": "Morning",
                "assessment_stage": "assessment_3",
                "date": today,
                "entry": {
                    "player_id": player["id"],
                    "technical_detail": _cricket_detail(),
                    "strength_conditioning": 6,
                    "game_awareness": 6,
                    "mental_attributes": 6,
                    "training_attitude": 6,
                },
            },
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("complete") is True

    def test_finalize_requires_all_scores(self):
        players = requests.get(f"{API}/coach/players", headers=_hdr("coach"), params={"centre": "Balua", "sport": "Cricket", "slot": "Morning"}, timeout=15)
        if not players.json().get("players"):
            pytest.skip("No players")
        player = players.json()["players"][0]
        today = datetime.date.today().isoformat()
        detail = _cricket_detail()
        detail["batting"]["technique"] = None
        r = requests.post(
            f"{API}/coach-assessments/batch",
            headers=_hdr("coach"),
            json={
                "centre": "Balua",
                "sport": "Cricket",
                "player_type": "Daily",
                "session": "Morning",
                "assessment_stage": "assessment_4",
                "date": today,
                "status": "final",
                "entries": [{"player_id": player["id"], "technical_detail": detail, "coach_remark": "Incomplete"}],
            },
            timeout=15,
        )
        assert r.status_code == 400, r.text

    def test_year_summary_endpoint(self):
        players = requests.get(f"{API}/coach/players", headers=_hdr("coach"), params={"centre": "Balua", "sport": "Cricket", "slot": "Morning"}, timeout=15)
        if not players.json().get("players"):
            pytest.skip("No players")
        pid = players.json()["players"][0]["id"]
        r = requests.get(f"{API}/coach-assessments/year-summary/{pid}", headers=_hdr("coach"), timeout=15)
        assert r.status_code == 200, r.text
        assert "comparison_rows" in r.json()

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
                "assessment_stage": "assessment_1",
                "date": today,
            },
            timeout=15,
        )
        assert r.status_code == 200, r.text
