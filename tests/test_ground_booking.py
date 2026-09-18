from ground_booking import compute_pricing, inclusive_days, list_ground_rate


def test_inclusive_days():
    assert inclusive_days("2026-09-16", "2026-09-16") == 1
    assert inclusive_days("2026-09-16", "2026-09-18") == 3


def test_list_rates():
    assert list_ground_rate("half_day", 1) == 6000
    assert list_ground_rate("full_day", 2) == 20000
    assert list_ground_rate("custom", 3, 15000) == 15000


def test_addons_and_discount():
    pricing = compute_pricing(
        time_slot="half_day",
        start_date="2026-09-16",
        end_date="2026-09-17",
        ground_rate=10000,
        people=20,
        food_enabled=True,
        food_rate_per_plate=250,
        transport_enabled=True,
        transport_rate_per_person=50,
        umpire_enabled=True,
        umpire_rate_per_day=1500,
        umpire_people=1,
        balls_enabled=True,
        ball_qty=4,
        ball_rate=80,
    )
    assert pricing["days"] == 2
    assert pricing["listGroundRate"] == 12000
    assert pricing["discountRequested"] is True
    assert pricing["discountAmount"] == 2000
    assert pricing["foodCost"] == 5000
    assert pricing["transportCost"] == 1000
    assert pricing["umpireCost"] == 3000
    assert pricing["ballCost"] == 320
    assert pricing["addOnTotal"] == 9320
    assert pricing["totalRevenue"] == 19320


def test_no_discount_at_list_rate():
    pricing = compute_pricing(
        time_slot="full_day",
        start_date="2026-09-16",
        end_date="2026-09-16",
        ground_rate=10000,
        people=1,
        food_enabled=False,
        transport_enabled=False,
        umpire_enabled=False,
        balls_enabled=False,
    )
    assert pricing["discountRequested"] is False
    assert pricing["totalRevenue"] == 10000


def test_independent_person_counts():
    pricing = compute_pricing(
        time_slot="half_day",
        start_date="2026-09-16",
        end_date="2026-09-16",
        ground_rate=6000,
        people=30,
        food_enabled=True,
        food_rate_per_plate=200,
        food_people=8,
        transport_enabled=True,
        transport_rate_per_person=100,
        transport_people=4,
        umpire_enabled=True,
        umpire_rate_per_day=1500,
        umpire_people=2,
        balls_enabled=False,
    )
    assert pricing["foodCost"] == 1600
    assert pricing["transportCost"] == 400
    assert pricing["umpireCost"] == 3000
    assert pricing["totalRevenue"] == 11000


def test_ground_booking_access_from_override():
    from routers.ground_booking import can_access_ground_bookings, can_manage_ground_bookings
    ops = {
        "role": "staff",
        "user_type": "alpha_admin",
        "designation": "OPERATIONS_ADMIN",
        "permission_set": "operations_admin",
    }
    assert not can_access_ground_bookings(ops)
    assert can_manage_ground_bookings({**ops, "permissions": {"manage_ground_bookings": True}})
    assert can_access_ground_bookings({**ops, "permissions": {"view_ground_bookings": True}})
    assert not can_manage_ground_bookings({**ops, "permissions": {"view_ground_bookings": True}})
    assert can_manage_ground_bookings({"role": "alpha_accounts"})

