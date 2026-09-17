from pws_class_catalog import (
    CLASS_LIST,
    CLASS_TO_CODE,
    format_class_display,
    normalize_class_value,
    pws_class_mongo_filter,
    same_class,
)


def test_normalize_legacy_and_display_forms():
    assert normalize_class_value("1") == "Std 1"
    assert normalize_class_value("Std 10") == "Std 10"
    assert normalize_class_value("Class-3") == "Std 3"
    assert normalize_class_value("iii") == "Std 3"
    assert normalize_class_value("Nur") == "Nursery"
    assert normalize_class_value("Std Nur") == "Nursery"
    assert normalize_class_value("LKG") == "LKG"
    assert normalize_class_value("Class IX") == "Std 9"
    assert normalize_class_value("Class X") == "Std 10"
    assert normalize_class_value("") is None


def test_format_class_display_never_uses_roman_or_nur():
    assert format_class_display("Class II") == "Std 2"
    assert format_class_display("10") == "Std 10"
    assert format_class_display("UKG") == "UKG"
    assert format_class_display("Nur") == "Nursery"
    assert format_class_display("Std Nur") == "Nursery"


def test_same_class_across_modules():
    assert same_class("Class I", "1")
    assert same_class("Std 9", "Class IX")
    assert same_class("STD_01", "Std 1")
    assert not same_class("Class I", "Class II")


def test_mongo_filter_includes_aliases():
    filt = pws_class_mongo_filter("Std 3")
    values = filt["pws_class"]["$in"]
    assert "Std 3" in values
    assert "Class III" in values
    assert "3" in values


def test_class_list_order_and_codes():
    assert CLASS_LIST[0] == "Nursery"
    assert CLASS_LIST[-1] == "Std 12"
    assert "LKG" in CLASS_LIST
    assert CLASS_TO_CODE["Std 10"] == "STD_10"
    assert CLASS_TO_CODE["Nursery"] == "NURSERY"
