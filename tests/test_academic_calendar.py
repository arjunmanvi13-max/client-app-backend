"""Unit tests for academic calendar rules linked to attendance."""
from datetime import date

import pytest

from academic_calendar import calendar_day_info, is_holiday_for_kind, parse_iso_date


def test_parse_iso_date():
    assert parse_iso_date("2026-07-19") == date(2026, 7, 19)


def test_sunday_holiday_for_students_and_teachers_only():
    # 2026-07-19 is a Sunday
    info = calendar_day_info("2026-07-19")
    assert info["is_sunday"] is True
    assert info["holiday_for"]["student"] is True
    assert info["holiday_for"]["teacher"] is True
    assert info["holiday_for"]["player"] is False
    assert info["holiday_for"]["staff"] is False
    assert info["holiday_for"]["coach"] is False


def test_weekday_not_holiday():
    # 2026-07-20 is a Monday
    info = calendar_day_info("2026-07-20")
    assert info["is_sunday"] is False
    assert not any(info["holiday_for"].values())


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("student", True),
        ("teacher", True),
        ("player", False),
        ("staff", False),
        ("coach", False),
    ],
)
def test_is_holiday_for_kind_sunday(kind, expected):
    assert is_holiday_for_kind("2026-07-19", kind) is expected
