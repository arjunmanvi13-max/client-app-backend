"""Unit tests for Reports export filenames and PDF generation."""
import os

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017/test")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("JWT_SECRET", "test-secret")

import pytest

from reports_engine import export_download_filename, export_pdf


def test_players_filename_includes_date_and_filters():
    name = export_download_filename(
        "players",
        "Player List",
        {"centre": "Harding Park", "sport": "Cricket", "player_type": "Daily"},
        "pdf",
        as_of="2026-09-06",
    )
    assert name == "Players_2026-09-06_Centre-Harding-Park_Sport-Cricket_Category-Daily.pdf"


def test_students_filename_skips_all_filters():
    name = export_download_filename(
        "students",
        "Student List",
        {"status": "All", "grade": ""},
        "xlsx",
        as_of="2026-09-06",
    )
    assert name == "Students_2026-09-06.xlsx"


def test_export_pdf_is_attachment_with_full_player_columns():
    pytest.importorskip("reportlab")
    columns = ["Entity", "Name", "Player ID", "Centre", "Sport", "Category", "Slot", "Status"]
    rows = [[
        "ALPHA", "Aarav Kumar", "APL - 300", "Harding Park", "Cricket", "Daily", "Evening", "active",
    ]]
    resp = export_pdf("Player List", columns, rows, "Entity: ALPHA", "Players_2026-09-06.pdf")
    assert resp.media_type == "application/pdf"
    cd = resp.headers.get("content-disposition") or ""
    assert "attachment" in cd.lower()
    assert "Players_2026-09-06.pdf" in cd
