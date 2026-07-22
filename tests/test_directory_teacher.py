"""Directory teacher profile creation — Super Admin / Principal only."""
import os
import uuid

import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "https://unified-track.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "super_admin": ("super@pws-alpha.com", "Super@123"),
    "principal": ("principal@prarambhika.com", "Principal@123"),
    "teacher": ("teacher@prarambhika.com", "Teacher@123"),
}
FALLBACK = {
    "super_admin": ("super@pws-alpha.com", "Super@123"),
    "principal": ("admin@pws-alpha.com", "Admin@123"),
    "teacher": ("teacher@pws-alpha.com", "Teacher@123"),
}
TOKENS = {}


def _login(role: str) -> str:
    if role in TOKENS:
        return TOKENS[role]
    for email, pwd in (CREDS.get(role), FALLBACK.get(role)):
        if not email:
            continue
        r = requests.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=15)
        if r.status_code == 200:
            TOKENS[role] = r.json()["access_token"]
            return TOKENS[role]
    pytest.skip(f"Could not log in as {role}")


def _hdr(role: str) -> dict:
    return {"Authorization": f"Bearer {_login(role)}"}


def _unique_mobile() -> str:
    return f"9{uuid.uuid4().int % 10**9:09d}"


def _unique_aadhaar() -> str:
    return f"{uuid.uuid4().int % 10**12:012d}"


VALID_PAYLOAD = {
    "name": "Directory Teacher Test",
    "date_of_birth": "15/08/1990",
    "address": "12 Sample Street, Patna",
    "qualification": "B.Ed",
    "last_job": "Sample Public School",
    "guardian_name": "Ram Kumar",
    "guardian_mobile": "9876501234",
    "reference_name": "Suresh Singh",
    "reference_mobile": "9876512345",
}


class TestDirectoryTeacherCreate:
    def test_super_admin_can_create(self):
        payload = {
            **VALID_PAYLOAD,
            "personal_email": f"teacher-{uuid.uuid4().hex[:8]}@example.com",
            "mobile": _unique_mobile(),
            "aadhaar_number": _unique_aadhaar(),
        }
        r = requests.post(f"{API}/users/directory-teachers", json=payload, headers=_hdr("super_admin"), timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == payload["name"]
        assert body["role"] == "teacher"
        assert body["has_login_account"] is False
        assert body["personal_email"] == payload["personal_email"]
        assert body.get("aadhaar_number") is None
        assert body.get("aadhaar_number_masked") == f"XXXX-XXXX-{payload['aadhaar_number'][-4:]}"

    def test_principal_can_create(self):
        payload = {
            **VALID_PAYLOAD,
            "personal_email": f"teacher-{uuid.uuid4().hex[:8]}@example.com",
            "mobile": _unique_mobile(),
            "aadhaar_number": _unique_aadhaar(),
        }
        r = requests.post(f"{API}/users/directory-teachers", json=payload, headers=_hdr("principal"), timeout=20)
        assert r.status_code == 200, r.text

    def test_teacher_role_forbidden(self):
        payload = {
            **VALID_PAYLOAD,
            "personal_email": f"teacher-{uuid.uuid4().hex[:8]}@example.com",
            "mobile": _unique_mobile(),
            "aadhaar_number": _unique_aadhaar(),
        }
        r = requests.post(f"{API}/users/directory-teachers", json=payload, headers=_hdr("teacher"), timeout=20)
        assert r.status_code == 403

    def test_validation_requires_qualification_other(self):
        payload = {
            **VALID_PAYLOAD,
            "personal_email": f"teacher-{uuid.uuid4().hex[:8]}@example.com",
            "mobile": _unique_mobile(),
            "aadhaar_number": _unique_aadhaar(),
            "qualification": "Other",
        }
        r = requests.post(f"{API}/users/directory-teachers", json=payload, headers=_hdr("super_admin"), timeout=20)
        assert r.status_code == 422
