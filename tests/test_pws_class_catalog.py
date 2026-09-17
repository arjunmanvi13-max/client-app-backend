from pws_class_catalog import (
    CLASS_LIST,
    format_class_display,
    normalize_class_value,
    pws_class_mongo_filter,
    same_class,
)


def test_normalize_legacy_and_display_forms():
    assert normalize_class_value("1") == "Class I"
    assert normalize_class_value("Std 10") == "Class X"
    assert normalize_class_value("Class-3") == "Class III"
    assert normalize_class_value("iii") == "Class III"
    assert normalize_class_value("Nur") == "Nursery"
    assert normalize_class_value("LKG") == "LKG"
    assert normalize_class_value("Class IX") == "Class IX"
    assert normalize_class_value("") is None


def test_format_class_display_is_canonical():
    assert format_class_display("Std 2") == "Class II"
    assert format_class_display("10") == "Class X"
    assert format_class_display("UKG") == "UKG"


def test_same_class_across_modules():
    assert same_class("Class I", "1")
    assert same_class("Std 9", "Class IX")
    assert not same_class("Class I", "Class II")


def test_mongo_filter_includes_aliases():
    filt = pws_class_mongo_filter("Class III")
    values = filt["pws_class"]["$in"]
    assert "Class III" in values
    assert "3" in values
    assert "Std 3" in values


def test_class_list_order():
    assert CLASS_LIST[0] == "Nursery"
    assert CLASS_LIST[-1] == "Class X"
    assert "LKG" in CLASS_LIST
