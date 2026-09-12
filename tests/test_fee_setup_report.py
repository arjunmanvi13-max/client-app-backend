from reports_fee_setup import build_fee_setup_row, fee_setup_columns_and_keys, fee_setup_total_row


def test_pws_class_v_includes_all_fee_components():
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
    assert row["registration"] == 1000
    assert row["admission"] == 10000
    assert row["security"] == 3000
    assert row["annual"] == 6000
    assert row["tuition"] == 2000
    assert row["physical_education"] == 1000
    assert row["exam"] == 1500
    assert row["transport"] == 0
    assert row["net_payable"] == 24500
    assert row["discounts"] == 0
    assert row["billing_frequency"] == "Mixed"
    assert row["skill_level"] == "—"


def test_pws_transport_and_tuition_override():
    row = build_fee_setup_row({
        "kind": "student",
        "name": "Neha",
        "pws_class": "Class V",
        "pws_student_type": "Day School",
        "transport_enabled": True,
        "transport_distance": "Up to 5 km",
        "pws_fee_overrides": {"Tuition": 1500, "Registration": 1000},
    })
    assert row["tuition"] == 1500
    assert row["transport"] == 2500
    assert row["registration"] == 1000
    assert row["discounts"] == 500
    assert row["net_payable"] == 24500 - 500 + 2500


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
    assert row["monthly"] == 2500
    assert row["registration"] == 3000
    assert row["transport"] == 0
    assert row["skill_level"] == "Beginner"
    assert row["net_payable"] == 5500


def test_alpha_monthly_override_and_transport():
    row = build_fee_setup_row({
        "kind": "player",
        "name": "Dev",
        "player_type": "Daily",
        "sport": "Cricket",
        "centre": "Harding Park",
        "monthly_fee_override": 2000,
        "transport_fee_monthly": 800,
    })
    assert row["monthly"] == 2000
    assert row["transport"] == 800
    assert row["registration"] == 3000
    assert row["discounts"] == 500
    assert row["net_payable"] == 2000 + 3000 + 800


def test_alpha_catalog_extra_components():
    row = build_fee_setup_row(
        {
            "kind": "player",
            "name": "Ira",
            "player_type": "Daily",
            "sport": "Football",
            "centre": "Harding Park",
        },
        catalog_rates={"registration": 3000, "monthly": 2000, "exam": 500},
        resolved_items=[{"fee_type": "kit", "effective_amount": 1500}],
    )
    assert row["exam"] == 500
    assert row["kit"] == 1500
    assert row["monthly"] == 2000
    assert row["net_payable"] == 3000 + 2000 + 500 + 1500


def test_fee_setup_total_row_aligns_to_keys():
    columns, keys = fee_setup_columns_and_keys("PWS", ["registration", "tuition", "transport"])
    meta = {
        "columns": columns,
        "row_keys": keys,
        "summary": {
            "total_discounts": 100,
            "total_net_payable": 900,
            "money_keys": ["registration", "tuition", "transport", "discounts", "net_payable"],
            "component_totals": {"registration": 200, "tuition": 600, "transport": 100},
        },
    }
    total = fee_setup_total_row(meta)
    assert total[keys.index("name")] == "TOTAL"
    assert total[keys.index("registration")] == 200
    assert total[keys.index("tuition")] == 600
    assert total[keys.index("discounts")] == 100
    assert total[keys.index("net_payable")] == 900
    assert total[keys.index("billing_frequency")] == ""
