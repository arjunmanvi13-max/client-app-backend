from reports_fee_setup import build_fee_setup_row


def test_pws_class_v_fee_setup():
    row = build_fee_setup_row({
        "id": "s1",
        "name": "Asha",
        "kind": "student",
        "organization": "PWS",
        "admission_number": "PWS-100",
        "pws_class": "Class V",
        "pws_student_type": "Day School",
        "status": "active",
    })
    assert row["name"] == "Asha"
    assert row["unique_id"] == "PWS-100"
    assert row["entity_label"] == "PWS"
    assert row["base_fee"] == 2000
    assert row["registration_fee"] == 1000
    assert row["net_payable"] == 3000
    assert row["billing_frequency"] == "Monthly"
    assert row["skill_level"] == "—"


def test_pws_override_is_discount():
    row = build_fee_setup_row({
        "kind": "student",
        "name": "Neha",
        "pws_class": "Class V",
        "pws_student_type": "Day School",
        "pws_fee_overrides": {"Tuition": 1500, "Registration": 1000},
    })
    assert row["base_fee"] == 1500
    assert row["discounts"] == 500


def test_alpha_daily_cricket_harding_park():
    row = build_fee_setup_row({
        "id": "p1",
        "name": "Ravi",
        "kind": "player",
        "organization": "ALPHA",
        "player_id": "ALP-9",
        "player_type": "Daily",
        "sport": "Cricket",
        "centre": "Harding Park",
        "skill_level": "Beginner",
        "status": "active",
    })
    assert row["entity_label"] == "ALPHA"
    assert row["unique_id"] == "ALP-9"
    assert row["campus"] == "Harding Park"
    assert row["sport_or_grade"] == "Cricket"
    assert row["base_fee"] == 2500
    assert row["registration_fee"] == 3000
    assert row["skill_level"] == "Beginner"


def test_alpha_monthly_override_discount():
    row = build_fee_setup_row({
        "kind": "player",
        "name": "Dev",
        "player_type": "Daily",
        "sport": "Cricket",
        "centre": "Harding Park",
        "monthly_fee_override": 2000,
    })
    assert row["base_fee"] == 2000
    assert row["discounts"] == 500
    assert row["net_payable"] == 2000 + 3000
