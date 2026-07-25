import os

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017/test")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("JWT_SECRET", "test-secret")

from student_academic import (
    class_display_name,
    grade_matches_pws_class,
    section_letter_from_label,
    grade_aliases_for_pws_class,
)


def test_section_letter_from_label():
    assert section_letter_from_label("9-A") == "A"
    assert section_letter_from_label("3-B") == "B"
    assert section_letter_from_label("Nur-C") == "C"
    assert section_letter_from_label("") == ""


def test_class_display_name():
    assert class_display_name("Class III") == "Std 3"
    assert class_display_name("Nursery") == "Nur"


def test_grade_matches_pws_class():
    assert grade_matches_pws_class("3", "Class III")
    assert grade_matches_pws_class("9", "Class IX")
    assert grade_matches_pws_class("Nur", "Nursery")
    assert not grade_matches_pws_class("9", "Class I")


def test_grade_aliases_include_numeric_and_std():
    aliases = grade_aliases_for_pws_class("Class II")
    assert "2" in aliases
    assert "ii" in aliases or "class ii" in aliases
