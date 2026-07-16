"""Unit tests for APL ID allocation helpers."""
from apl_id_format import (
    APL_ID_START,
    format_apl_id,
    normalize_dob,
    normalize_person_name,
    parse_apl_number,
)


def test_parse_apl_number_spaced_format():
    assert parse_apl_number("APL - 150") == 150
    assert parse_apl_number("apl - 151") == 151


def test_parse_apl_number_legacy_format():
    assert parse_apl_number("APL-0001") == 1
    assert parse_apl_number("APL-0150") == 150


def test_parse_apl_number_invalid():
    assert parse_apl_number("") is None
    assert parse_apl_number("PWS-20250001") is None


def test_format_apl_id():
    assert format_apl_id(APL_ID_START) == "APL - 150"
    assert format_apl_id(999) == "APL - 999"


def test_normalize_person_name():
    assert normalize_person_name("  Rahul Kumar  ") == "Rahul Kumar"
    assert normalize_person_name(None) == ""


def test_normalize_dob():
    assert normalize_dob("2010-05-15") == "2010-05-15"
    assert normalize_dob("2010-05-15T00:00:00") == "2010-05-15"
    assert normalize_dob(None) is None
