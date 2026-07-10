"""Backend tests for the June 2026 Auth Redesign.

Covers:
- Email+password login (domain-restricted, wrong pwd, deactivated, superadmin)
- Removed OTP endpoints return 404
- POST /users creation (email/password validations, permissions map)
- Forced password change flow (must_change_password lifecycle)
- Super admin reset-password
- PATCH /users email/password rules
- RBAC regression
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"

SUPER_ADMIN_EMAIL = "superadmin@prarambhika.com"
SUPER_ADMIN_PWD = "Super@123"


# ----------------- helpers -----------------
def _post(path, json=None, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return requests.post(f"{API}{path}", json=json, headers=h, timeout=20)


def _get(path, token=None):
    h = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return requests.get(f"{API}{path}", headers=h, timeout=20)


def _patch(path, json=None, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return requests.patch(f"{API}{path}", json=json, headers=h, timeout=20)


def _delete(path, token=None):
    h = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return requests.delete(f"{API}{path}", headers=h, timeout=20)


def _login(email, password):
    return _post("/auth/login", {"email": email, "password": password})


@pytest.fixture(scope="module")
def super_token():
    r = _login(SUPER_ADMIN_EMAIL, SUPER_ADMIN_PWD)
    assert r.status_code == 200, f"Super admin login failed: {r.status_code} {r.text}"
    data = r.json()
    assert data["user"]["role"] == "super_admin"
    assert data["must_change_password"] is False
    return data["access_token"]


# ----------------- 1. Login validation -----------------
class TestLoginValidation:
    def test_non_domain_email_rejected(self):
        r = _login("x@gmail.com", "whatever")
        assert r.status_code == 400
        assert "prarambhika.com" in r.json().get("detail", "").lower() or "@prarambhika.com" in r.json().get("detail", "")

    def test_wrong_password_401(self):
        r = _login(SUPER_ADMIN_EMAIL, "WRONG_PASSWORD_XYZ")
        assert r.status_code == 401

    def test_super_admin_login_ok(self, super_token):
        # covered by fixture but assert token usable
        r = _get("/auth/me", token=super_token)
        assert r.status_code == 200
        assert r.json()["email"] == SUPER_ADMIN_EMAIL
        assert r.json()["role"] == "super_admin"


# ----------------- 2. Removed OTP endpoints -----------------
class TestOtpEndpointsRemoved:
    def test_otp_send_removed(self):
        r = _post("/auth/otp/send", {"mobile": "9631252241"})
        assert r.status_code == 404

    def test_otp_verify_removed(self):
        r = _post("/auth/otp/verify", {"mobile": "9631252241", "code": "123456"})
        assert r.status_code == 404

    def test_mobile_login_removed(self):
        r = _post("/auth/login/mobile", {"mobile": "9631252241", "password": "x"})
        assert r.status_code == 404


# ----------------- 3. User creation validation -----------------
class TestUserCreate:
    def test_missing_email(self, super_token):
        r = _post("/users", {"name": "TEST_x", "password": "Temp@123", "role": "teacher"}, token=super_token)
        assert r.status_code == 400

    def test_missing_password(self, super_token):
        r = _post("/users", {"name": "TEST_x", "email": "test_x@prarambhika.com", "role": "teacher"}, token=super_token)
        assert r.status_code == 400

    def test_non_domain_email(self, super_token):
        r = _post(
            "/users",
            {"name": "TEST_x", "email": "test_x@gmail.com", "password": "Temp@123", "role": "teacher"},
            token=super_token,
        )
        assert r.status_code == 400


@pytest.fixture(scope="module")
def created_teacher(super_token):
    """Create a fresh throwaway teacher for password-change tests. Cleanup after."""
    email = f"testteacher_{uuid.uuid4().hex[:8]}@prarambhika.com"
    payload = {
        "name": "TEST Teacher",
        "email": email,
        "password": "Temp@123",
        "role": "teacher",
        "organization": "PWS",
        "permissions": {"view_students": True, "mark_student_attendance": True, "dashboard_access": True},
    }
    r = _post("/users", payload, token=super_token)
    assert r.status_code in (200, 201), f"create failed: {r.status_code} {r.text}"
    doc = r.json()
    assert doc.get("must_change_password") is True
    perms = doc.get("permissions") or {}
    assert perms.get("view_students") is True
    yield {"id": doc["id"], "email": email}
    # Cleanup
    _delete(f"/users/{doc['id']}", token=super_token)


# ----------------- 4. Force password change flow -----------------
class TestForcePasswordChange:
    def test_new_user_must_change_password_on_login(self, created_teacher):
        r = _login(created_teacher["email"], "Temp@123")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["must_change_password"] is True
        assert data["user"]["role"] == "teacher"

    def test_change_password_then_relogin(self, created_teacher):
        # login to get token
        r = _login(created_teacher["email"], "Temp@123")
        assert r.status_code == 200
        token = r.json()["access_token"]
        # change password
        rc = _post(
            "/auth/password/change",
            {"current_password": "Temp@123", "new_password": "MyOwn@456"},
            token=token,
        )
        assert rc.status_code == 200, rc.text
        # relogin with new pwd; must_change_password should now be false
        r2 = _login(created_teacher["email"], "MyOwn@456")
        assert r2.status_code == 200
        assert r2.json()["must_change_password"] is False

    def test_teacher_forbidden_admin_route(self, created_teacher):
        r = _login(created_teacher["email"], "MyOwn@456")
        assert r.status_code == 200
        token = r.json()["access_token"]
        r2 = _get("/reports/financial/summary", token=token)
        # RBAC — teacher should not access admin-only reports; accept 403 or 404 if endpoint uses different guard
        assert r2.status_code in (403, 401), f"expected 401/403 got {r2.status_code} {r2.text[:200]}"


# ----------------- 5. Super Admin reset password -----------------
class TestSuperAdminReset:
    def test_reset_password_flow(self, super_token, created_teacher):
        r = _post(
            f"/users/{created_teacher['id']}/reset-password",
            {"new_password": "Reset@789"},
            token=super_token,
        )
        assert r.status_code == 200, r.text
        # login with new password; must_change_password flips back true
        lr = _login(created_teacher["email"], "Reset@789")
        assert lr.status_code == 200
        assert lr.json()["must_change_password"] is True

    def test_reset_too_short(self, super_token, created_teacher):
        r = _post(
            f"/users/{created_teacher['id']}/reset-password",
            {"new_password": "abc"},
            token=super_token,
        )
        assert r.status_code == 400

    def test_non_super_admin_forbidden(self, created_teacher):
        # login as teacher (must_change_password may be true after previous reset)
        r = _login(created_teacher["email"], "Reset@789")
        assert r.status_code == 200
        teacher_token = r.json()["access_token"]
        # attempt reset another user
        # need some other user id — use super admin's own to just probe permission gate
        me = _get("/auth/me", token=teacher_token).json()
        # pick a different user via directory
        dr = _get("/users/directory", token=teacher_token)
        other_id = None
        if dr.status_code == 200:
            for u in dr.json():
                if u["id"] != me["id"]:
                    other_id = u["id"]
                    break
        assert other_id, "could not find another user to test"
        r2 = _post(f"/users/{other_id}/reset-password", {"new_password": "Whatever@1"}, token=teacher_token)
        assert r2.status_code == 403


# ----------------- 6. PATCH /users email + password -----------------
class TestUpdateUser:
    def test_non_domain_email_rejected(self, super_token, created_teacher):
        r = _patch(
            f"/users/{created_teacher['id']}",
            {"email": "hacker@gmail.com"},
            token=super_token,
        )
        assert r.status_code == 400

    def test_valid_domain_email_update(self, super_token, created_teacher):
        new_email = f"renamed_{uuid.uuid4().hex[:6]}@prarambhika.com"
        r = _patch(f"/users/{created_teacher['id']}", {"email": new_email}, token=super_token)
        assert r.status_code == 200
        assert r.json()["email"] == new_email
        # update the fixture email so cleanup still finds user
        created_teacher["email"] = new_email

    def test_password_patch_sets_must_change(self, super_token, created_teacher):
        r = _patch(
            f"/users/{created_teacher['id']}",
            {"password": "PatchPwd@1"},
            token=super_token,
        )
        assert r.status_code == 200
        assert r.json().get("must_change_password") is True
        # verify login flags forced change
        lr = _login(created_teacher["email"], "PatchPwd@1")
        assert lr.status_code == 200
        assert lr.json()["must_change_password"] is True


# ----------------- 7. Deactivated account -> 403 -----------------
class TestDeactivatedAccount:
    def test_deactivated_login_forbidden(self, super_token):
        # create throwaway user, deactivate, then attempt login
        email = f"deact_{uuid.uuid4().hex[:8]}@prarambhika.com"
        r = _post(
            "/users",
            {"name": "TEST Deact", "email": email, "password": "Temp@123", "role": "teacher"},
            token=super_token,
        )
        assert r.status_code in (200, 201)
        uid = r.json()["id"]
        try:
            dr = _post(f"/users/{uid}/deactivate", token=super_token)
            assert dr.status_code == 200
            lr = _login(email, "Temp@123")
            assert lr.status_code == 403
        finally:
            _delete(f"/users/{uid}", token=super_token)


# ----------------- 8. Demo account regression -----------------
class TestDemoAccounts:
    @pytest.mark.parametrize("email,pwd,role", [
        ("teacher@prarambhika.com", "Teacher@123", "teacher"),
        ("admin@prarambhika.com", "Admin@123", "admin"),
        ("parent_pws@prarambhika.com", "Parent@123", "parent"),
        ("coach@prarambhika.com", "Coach@123", "coach"),
    ])
    def test_demo_login(self, email, pwd, role):
        r = _login(email, pwd)
        assert r.status_code == 200, f"{email}: {r.status_code} {r.text}"
        assert r.json()["user"]["role"] == role
