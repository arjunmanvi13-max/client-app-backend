"""Time Table unit tests."""
import os
from datetime import date

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017/test")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("JWT_SECRET", "test-secret")

from timetable.constants import schedule_group_for_grade, is_teaching_period, day_of_week_for_date
from timetable.permissions import (
    can_timetable_create,
    can_timetable_view_all,
    can_timetable_view_own,
    is_academic_head,
)


def test_schedule_group_pre_primary():
    assert schedule_group_for_grade("Nur") == "PRE_PRIMARY"
    assert schedule_group_for_grade("LKG") == "PRE_PRIMARY"
    assert schedule_group_for_grade("UKG") == "PRE_PRIMARY"


def test_schedule_group_primary():
    assert schedule_group_for_grade("5") == "PRIMARY_SECONDARY"
    assert schedule_group_for_grade("10") == "PRIMARY_SECONDARY"


def test_teaching_period_type():
    assert is_teaching_period("TEACHING") is True
    assert is_teaching_period("LUNCH") is False


def test_academic_head_designation():
    assert is_academic_head({"role": "principal", "designation": "ACADEMIC_HEAD"}) is True
    assert is_academic_head({"role": "principal", "designation": "PRINCIPAL"}) is False
    assert is_academic_head({"role": "super_admin"}) is True


def test_academic_head_can_create():
    assert can_timetable_create({"role": "principal", "designation": "ACADEMIC_HEAD", "permissions": {}}) is True
    assert can_timetable_create({"role": "principal", "designation": "PRINCIPAL", "permissions": {}}) is False


def test_principal_can_view_all():
    assert can_timetable_view_all({"role": "principal", "designation": "PRINCIPAL", "permissions": {}}) is True


def test_teacher_view_own_only():
    assert can_timetable_view_own({"role": "teacher", "permissions": {}}) is True
    assert can_timetable_view_all({"role": "teacher", "permissions": {}}) is False


def test_sunday_has_no_day():
    assert day_of_week_for_date("2026-07-19") is None  # Sunday
    assert day_of_week_for_date("2026-08-04") == "TUE"
