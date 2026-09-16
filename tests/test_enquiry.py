from routers.enquiry import ACTIVE_STATUSES, can_manage_enquiries, SOURCES, STATUSES


def test_office_roles_can_manage():
    assert can_manage_enquiries({"role": "super_admin"})
    assert can_manage_enquiries({"role": "principal"})
    assert can_manage_enquiries({"role": "pws_admin"})
    assert can_manage_enquiries({"role": "alpha_admin"})
    assert can_manage_enquiries({"role": "admin"})
    assert can_manage_enquiries({"role": "pws_accounts"})
    assert can_manage_enquiries({"role": "alpha_accounts"})
    assert can_manage_enquiries({"role": "staff"})
    assert not can_manage_enquiries({"role": "teacher"})
    assert not can_manage_enquiries({"role": "coach"})


def test_catalogues():
    assert "Phone Call" in SOURCES
    assert "New" in STATUSES
    assert "Admitted" in STATUSES
    assert "Pending Close" in STATUSES
    assert "New" in ACTIVE_STATUSES
    assert "Admitted" not in ACTIVE_STATUSES
