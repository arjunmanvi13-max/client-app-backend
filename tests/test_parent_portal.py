"""Parent portal MVP — child isolation and read-only access."""
import os
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "parent_pws": ("parent_pws@prarambhika.com", "Parent@123"),
    "parent_alpha": ("parent_alpha@prarambhika.com", "Parent@123"),
    "principal": ("principal@prarambhika.com", "Principal@123"),
    "super_admin": ("superadmin@prarambhika.com", "Super@123"),
}
LEGACY_CREDS = {
    "parent_pws": ("parent@pws-alpha.com", "Parent@123"),
    "parent_alpha": ("parent@pws-alpha.com", "Parent@123"),
    "principal": ("admin@pws-alpha.com", "Admin@123"),
    "super_admin": ("super@pws-alpha.com", "Super@123"),
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


def _parent_wards(role="parent_pws"):
    r = requests.get(f"{API}/parent/wards", headers=_hdr(role), timeout=15)
    if r.status_code != 200:
        pytest.skip("Parent wards unavailable")
    return r.json()


def _unlinked_student_id(wards):
    students = requests.get(
        f"{API}/people",
        headers=_hdr("principal"),
        params={"kind": "student"},
        timeout=15,
    )
    if students.status_code != 200:
        pytest.skip("Cannot list students")
    linked = {w["id"] for w in wards}
    for s in students.json():
        if s["id"] not in linked:
            return s["id"]
    pytest.skip("No unlinked student for isolation test")


@pytest.mark.integration
class TestParentPortalMVP:
    def test_parent_profile(self):
        r = requests.get(f"{API}/parent/profile", headers=_hdr("parent_pws"), timeout=15)
        assert r.status_code == 200, r.text
        assert r.json().get("role") == "parent"
        assert "linked_person_ids" in r.json()

    def test_wards_only_linked_children(self):
        wards = _parent_wards("parent_pws")
        people = requests.get(f"{API}/people", headers=_hdr("parent_pws"), timeout=15)
        assert people.status_code == 200, people.text
        ward_ids = {w["id"] for w in wards}
        for p in people.json():
            assert p["id"] in ward_ids

    def test_cannot_access_unlinked_ward(self):
        wards = _parent_wards("parent_pws")
        other_id = _unlinked_student_id(wards)
        for path in (
            f"/parent/attendance/{other_id}",
            f"/parent/marks/{other_id}",
            f"/parent/fees/{other_id}",
            f"/parent/invoices/{other_id}",
            f"/parent/payments/{other_id}",
            f"/parent/receipts/{other_id}",
            f"/parent/coach-assessments/{other_id}",
            f"/parent/ward/{other_id}",
        ):
            r = requests.get(f"{API}{path}", headers=_hdr("parent_pws"), timeout=15)
            assert r.status_code == 404, f"{path} should be 404, got {r.status_code}"

    def test_cannot_access_staff_admin_apis(self):
        parent = _hdr("parent_pws")
        assert requests.get(f"{API}/fees", headers=parent, timeout=15).status_code == 403
        assert requests.get(f"{API}/academic/years", headers=parent, timeout=15).status_code == 403
        assert requests.get(f"{API}/marks/assessments", headers=parent, timeout=15).status_code == 403
        wards = _parent_wards("parent_pws")
        if wards:
            pid = wards[0]["id"]
            patch = requests.patch(
                f"{API}/people/{pid}",
                headers=parent,
                json={"name": "Hacked"},
                timeout=15,
            )
            assert patch.status_code in (403, 405, 422), patch.text

    def test_entity_labels_dual_participation(self):
        wards = _parent_wards("parent_pws")
        dual = next((w for w in wards if w.get("is_dual_participation") or w.get("organization") == "BOTH"), None)
        if not dual:
            pytest.skip("No dual-participation ward seeded")
        assert len(dual.get("entity_labels") or []) >= 2
        codes = {l["code"] for l in dual["entity_labels"]}
        assert "PWS" in codes and "ALPHA" in codes

    def test_parent_notifications(self):
        r = requests.get(f"{API}/parent/notifications", headers=_hdr("parent_pws"), timeout=15)
        assert r.status_code == 200, r.text
        assert "stored" in r.json() and "computed" in r.json()

    def test_parent_marks_published_only(self):
        wards = _parent_wards("parent_pws")
        if not wards:
            pytest.skip("No wards")
        student = next((w for w in wards if w.get("kind") == "student"), wards[0])
        r = requests.get(f"{API}/parent/marks/{student['id']}", headers=_hdr("parent_pws"), timeout=15)
        assert r.status_code == 200, r.text
        for rc in r.json().get("report_cards") or []:
            assert rc.get("status") == "published"

    def test_parent_receipts_and_payments_endpoints(self):
        wards = _parent_wards("parent_pws")
        if not wards:
            pytest.skip("No wards")
        wid = wards[0]["id"]
        pay = requests.get(f"{API}/parent/payments/{wid}", headers=_hdr("parent_pws"), timeout=15)
        assert pay.status_code == 200, pay.text
        rec = requests.get(f"{API}/parent/receipts/{wid}", headers=_hdr("parent_pws"), timeout=15)
        assert rec.status_code == 200, rec.text
        assert "receipts" in rec.json()

    def test_principal_cannot_use_parent_portal(self):
        r = requests.get(f"{API}/parent/wards", headers=_hdr("principal"), timeout=15)
        assert r.status_code == 403, r.text

    def test_parent_legacy_fees_visible(self):
        wards = _parent_wards("parent_pws")
        student = next((w for w in wards if w.get("kind") == "student"), None)
        if not student:
            pytest.skip("No student ward")
        r = requests.get(f"{API}/parent/fees/{student['id']}", headers=_hdr("parent_pws"), timeout=15)
        assert r.status_code == 200, r.text
        assert "fees" in r.json()
