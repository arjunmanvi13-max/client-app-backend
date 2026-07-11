"""Reports MVP — catalog, filters, entity labels, Excel/PDF export."""
import os
import pytest
import requests

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE}/api"

CREDS = {
    "super_admin": ("superadmin@prarambhika.com", "Super@123"),
    "admin": ("admin@prarambhika.com", "Admin@123"),
    "principal": ("principal@prarambhika.com", "Principal@123"),
}
LEGACY_CREDS = {
    "super_admin": ("super@pws-alpha.com", "Super@123"),
    "admin": ("admin@pws-alpha.com", "Admin@123"),
    "principal": ("principal@pws-alpha.com", "Principal@123"),
}
TOKENS = {}

REPORT_IDS = [
    "students", "players", "staff", "attendance-summary", "attendance-detail",
    "fee-collection", "outstanding-invoices", "payment-receipts",
    "marks-summary", "report-card-status",
]


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


@pytest.fixture(scope="module", autouse=True)
def _api_up():
    try:
        r = requests.get(f"{API}/", timeout=5)
        if r.status_code != 200:
            pytest.skip("API not reachable")
    except Exception:
        pytest.skip("API not reachable")


class TestReportsCatalog:
    def test_catalog_lists_all_reports(self):
        r = requests.get(f"{API}/reports/catalog", headers=_hdr("super_admin"), timeout=15)
        assert r.status_code == 200
        data = r.json()
        ids = {rep["id"] for rep in data["reports"]}
        for rid in REPORT_IDS:
            assert rid in ids
        assert "Combined" in data["entity_options"]
        assert "xlsx" in data["export_formats"]
        assert "pdf" in data["export_formats"]


class TestReportsRun:
    @pytest.mark.parametrize("report_id", REPORT_IDS)
    def test_super_admin_combined_report(self, report_id):
        r = requests.get(
            f"{API}/reports/{report_id}",
            headers=_hdr("super_admin"),
            params={"entity": "both"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["report_id"] == report_id
        assert "columns" in body
        assert "rows" in body
        assert body["entity_scope"] == "BOTH"
        assert body["entity_scope_label"] == "Combined"
        if body["rows"]:
            assert "entity_label" in body["rows"][0]

    def test_principal_scoped_to_pws(self):
        r = requests.get(f"{API}/reports/students", headers=_hdr("principal"), timeout=20)
        assert r.status_code == 200
        assert r.json()["entity_scope"] == "PWS"

    def test_unknown_report_404(self):
        r = requests.get(f"{API}/reports/not-a-report", headers=_hdr("super_admin"), timeout=10)
        assert r.status_code == 404


class TestReportsExport:
    def test_excel_export_students(self):
        r = requests.get(
            f"{API}/reports/students/export",
            headers=_hdr("super_admin"),
            params={"entity": "both", "format": "xlsx"},
            timeout=30,
        )
        assert r.status_code == 200
        assert "spreadsheetml" in r.headers.get("content-type", "")
        assert len(r.content) > 200

    def test_pdf_export_students(self):
        r = requests.get(
            f"{API}/reports/students/export",
            headers=_hdr("super_admin"),
            params={"entity": "pws", "format": "pdf"},
            timeout=30,
        )
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert len(r.content) > 100

    def test_invalid_format_rejected(self):
        r = requests.get(
            f"{API}/reports/students/export",
            headers=_hdr("super_admin"),
            params={"format": "csv"},
            timeout=10,
        )
        assert r.status_code == 400
