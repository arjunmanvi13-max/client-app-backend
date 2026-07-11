"""Seed demo data on app startup."""
import uuid
from datetime import timedelta
from core import db, hash_password, verify_password, now_utc

DEMO_USERS = [
    {"email": "admin@prarambhika.com", "password": "Admin@123", "mobile": "9000000001", "name": "Rohan Sharma", "role": "admin", "organization": "ALPHA", "department": "ALPHA Operations"},
    {"email": "super@prarambhika.com", "password": "Super@123", "mobile": None, "name": "Anita Verma", "role": "super_admin", "organization": "BOTH", "department": "Trustee"},
    {"email": "principal@prarambhika.com", "password": "Principal@123", "mobile": "9000000002", "name": "Meera Nair", "role": "principal", "organization": "PWS", "department": "Administration"},
    {"email": "vp@prarambhika.com", "password": "Vp@123", "mobile": "9000000003", "name": "Arun Pandey", "role": "vice_principal", "organization": "PWS", "department": "Administration"},
    {"email": "teacher@prarambhika.com", "password": "Teacher@123", "mobile": "9000000004", "name": "Priya Kumari", "role": "teacher", "organization": "PWS", "department": "Mathematics"},
    {"email": "coach@prarambhika.com", "password": "Coach@123", "mobile": "9000000005", "name": "Vikram Singh", "role": "coach", "organization": "ALPHA", "department": "Cricket", "coach_type": "head"},
    {"email": "asst_coach@prarambhika.com", "password": "Asst@123", "mobile": "9000000006", "name": "Ravi Kumar", "role": "coach", "organization": "ALPHA", "department": "Cricket", "coach_type": "assistant"},
    {"email": "warden@prarambhika.com", "password": "Warden@123", "mobile": "9000000007", "name": "Suresh Yadav", "role": "warden", "organization": "BOTH", "department": "Boys Hostel"},
    {"email": "student@prarambhika.com", "password": "Student@123", "mobile": "9000000008", "name": "Aarav Mishra", "role": "student", "organization": "PWS", "department": "Class 9-A"},
    {"email": "player@prarambhika.com", "password": "Player@123", "mobile": "9000000009", "name": "Karan Raj", "role": "player", "organization": "ALPHA", "department": "U-15 Cricket"},
    {"email": "parent_pws@prarambhika.com", "password": "Parent@123", "mobile": "9000000010", "name": "Sunil Mishra", "role": "parent", "organization": "PWS", "department": "Parent"},
    {"email": "parent_alpha@prarambhika.com", "password": "Parent@123", "mobile": "9000000011", "name": "Anita Verma (Parent)", "role": "parent", "organization": "ALPHA", "department": "Parent"},
]

# Super Admins — email + password login (domain-restricted like everyone else).
SUPER_ADMIN_SEEDS = [
    {"email": "superadmin@prarambhika.com", "password": "Super@123", "mobile": "9631252241", "name": "Super Admin 1", "organization": "BOTH", "department": "Trustee"},
    {"email": "superadmin2@prarambhika.com", "password": "Super@123", "mobile": "9801772660", "name": "Super Admin 2", "organization": "BOTH", "department": "Trustee"},
]

ROLE_DEFAULT_CAN_MANAGE = {
    "super_admin": ["student", "player", "teacher", "coach", "staff"],
    "admin": ["student", "player", "teacher", "coach", "staff"],
    "principal": ["student", "teacher", "staff"],
    "vice_principal": ["student", "teacher", "staff"],
    "coach": ["player"],
    "teacher": [],
}

ROLE_DEFAULT_COACH_PERMS = {
    "coach": ["view_players", "add_players", "edit_players"],
}

async def seed_data():
    # One-time migration: move any legacy @pws-alpha.com accounts to the new org domain.
    legacy = await db.users.find({"email": {"$regex": "@pws-alpha\\.com$"}}).to_list(200)
    for lu in legacy:
        new_email = lu["email"].split("@")[0] + "@prarambhika.com"
        if not await db.users.find_one({"email": new_email}):
            await db.users.update_one({"id": lu["id"]}, {"$set": {"email": new_email}})

    # Backfill: every STAFF person must have a linked user account (Permissions module sync)
    from routers.people import ensure_staff_user_account
    async for sp in db.people.find({"kind": "staff"}, {"_id": 0}):
        try:
            await ensure_staff_user_account(sp)
        except Exception:
            pass

    # Ensure email/mobile indices are SPARSE (allow nulls — needed for OTP-only Super Admins).
    # If a pre-existing non-sparse index exists we drop and recreate.
    try:
        idx_info = await db.users.index_information()
        if "email_1" in idx_info and not idx_info["email_1"].get("sparse"):
            await db.users.drop_index("email_1")
        await db.users.create_index("email", unique=True, sparse=True)
    except Exception:
        pass
    try:
        await db.users.create_index("mobile", unique=True, sparse=True)
    except Exception:
        pass
    # OTP collection — TTL on expires_at for automatic cleanup; lookup index
    try:
        await db.otps.create_index("expires_at", expireAfterSeconds=0)
        await db.otps.create_index([("mobile", 1), ("purpose", 1), ("created_at", -1)])
    except Exception:
        pass
    # NOTE: staff users are no longer purged — every staff person gets a synced
    # user account so they appear in the Permissions module (see backfill below).
    for u in DEMO_USERS:
        existing = await db.users.find_one({"email": u["email"]})
        defaults = ROLE_DEFAULT_CAN_MANAGE.get(u["role"], [])
        coach_defaults = ROLE_DEFAULT_COACH_PERMS.get(u["role"], [])
        if not existing:
            doc = {
                "id": str(uuid.uuid4()),
                "email": u["email"],
                "password_hash": hash_password(u["password"]),
                "is_password_set": True,
                "mobile": u.get("mobile"),
                "name": u["name"],
                "role": u["role"],
                "organization": u["organization"],
                "department": u["department"],
                "phone": None,
                "can_manage": defaults,
                "coach_permissions": coach_defaults,
                "coach_type": u.get("coach_type"),
                "assigned_sport": "Cricket" if u["role"] == "coach" else None,
                "assigned_centres": ["Balua"] if u["role"] == "coach" else [],
                "assigned_sports": ["Cricket", "Football"] if u["role"] == "coach" else [],
                "created_at": now_utc().isoformat(),
            }
            await db.users.insert_one(doc)
        else:
            patch = {}
            if not verify_password(u["password"], existing["password_hash"]):
                patch["password_hash"] = hash_password(u["password"])
            if "is_password_set" not in existing:
                patch["is_password_set"] = True
            if u.get("mobile") and existing.get("mobile") != u["mobile"]:
                patch["mobile"] = u["mobile"]
            if "can_manage" not in existing:
                patch["can_manage"] = defaults
            if "coach_permissions" not in existing and u["role"] == "coach":
                patch["coach_permissions"] = coach_defaults
            if u["role"] == "coach" and not existing.get("assigned_sport"):
                patch["assigned_sport"] = "Cricket"
            if u["role"] == "coach" and not existing.get("assigned_centres"):
                patch["assigned_centres"] = ["Balua"]
            if u["role"] == "coach" and not existing.get("assigned_sports"):
                patch["assigned_sports"] = ["Cricket", "Football"]
            if u["role"] == "coach" and "coach_type" not in existing:
                patch["coach_type"] = u.get("coach_type", "head")
            # Force admin -> Sports Admin scope (ALPHA-only) per latest spec
            if u["role"] == "admin":
                if existing.get("organization") != "ALPHA":
                    patch["organization"] = "ALPHA"
                if existing.get("department") != "ALPHA Operations":
                    patch["department"] = "ALPHA Operations"
            if u["role"] == "teacher" and existing.get("can_manage") != []:
                patch["can_manage"] = []
            if patch:
                await db.users.update_one({"email": u["email"]}, {"$set": patch})

    # ----- Super Admins (email + password login) -----
    for sa in SUPER_ADMIN_SEEDS:
        existing = await db.users.find_one({"$or": [{"email": sa["email"]}, {"mobile": sa["mobile"], "role": "super_admin"}]})
        if not existing:
            await db.users.insert_one({
                "id": str(uuid.uuid4()),
                "email": sa["email"],
                "password_hash": hash_password(sa["password"]),
                "is_password_set": True,
                "must_change_password": False,
                "mobile": sa["mobile"],
                "name": sa["name"],
                "role": "super_admin",
                "organization": sa["organization"],
                "department": sa["department"],
                "phone": None,
                "can_manage": ["student", "player", "teacher", "coach", "staff"],
                "coach_permissions": [],
                "coach_type": None,
                "assigned_sport": None,
                "assigned_centres": [],
                "assigned_sports": [],
                "created_at": now_utc().isoformat(),
            })
        else:
            patch = {}
            if not existing.get("email"):
                patch["email"] = sa["email"]
            if not existing.get("password_hash"):
                patch["password_hash"] = hash_password(sa["password"])
                patch["is_password_set"] = True
                patch["must_change_password"] = False
            if patch:
                await db.users.update_one({"id": existing["id"]}, {"$set": patch})

    sample_students = [
        ("Aarav Mishra", "9-A"), ("Isha Sinha", "9-A"), ("Rohit Kumar", "9-A"),
        ("Sneha Singh", "9-A"), ("Aman Raj", "9-A"), ("Kavya Patel", "9-A"),
        ("Dev Ranjan", "10-B"), ("Pooja Devi", "10-B"), ("Manish Roy", "10-B"),
        ("Tanvi Jha", "10-B"),
    ]
    for name, cls in sample_students:
        if not await db.people.find_one({"name": name, "kind": "student"}):
            await db.people.insert_one({
                "id": str(uuid.uuid4()),
                "kind": "student",
                "name": name,
                "group": cls,
                "organization": "PWS",
                "is_resident": cls == "9-A",
            })

    await _seed_academic_structure()

    sample_players = [
        # (name, batch/group, sport, centre, player_type, skill, slot)
        ("Karan Raj", "U-15 Cricket", "Cricket", "Balua", "Hostel", "Intermediate", "Morning"),
        ("Riya Singh", "U-15 Cricket", "Cricket", "Balua", "Daily", "Beginner", "Morning"),
        ("Aditya Verma", "U-17 Football", "Football", "Balua", "Hostel", "Advanced", "Evening"),
        ("Neha Sharma", "U-17 Football", "Football", "Balua", "Day Boarding", "Intermediate", "Evening"),
        ("Rahul Kumar", "U-19 Cricket", "Cricket", "Harding Park", "Daily", "Advanced", "Morning"),
        ("Simran Gupta", "U-19 Football", "Football", "Harding Park", "Daily", "Intermediate", "Evening"),
    ]
    six_months_ago = (now_utc() - timedelta(days=183)).strftime("%Y-%m-%d")
    for name, batch, sport, centre, ptype, skill, slot in sample_players:
        existing = await db.people.find_one({"name": name, "kind": "player"})
        base = {
            "kind": "player",
            "name": name,
            "group": batch,
            "sport": sport,
            "centre": centre,
            "player_type": ptype,
            "skill_level": skill,
            "slot": slot,
            "organization": "ALPHA",
            "is_resident": ptype == "Hostel",
            "date_of_admission": six_months_ago,
            "status": "active",
            "assigned_coach_id": None,  # players are centre-based now
        }
        if not existing:
            await db.people.insert_one({"id": str(uuid.uuid4()), **base})
        else:
            patch = {k: v for k, v in base.items() if k not in existing or existing.get(k) is None}
            # Always backfill new fields
            if "date_of_admission" not in existing or not existing.get("date_of_admission"):
                patch["date_of_admission"] = six_months_ago
            if "status" not in existing or not existing.get("status"):
                patch["status"] = "active"
            # Force player_type fix for Neha if she still has old value
            if existing.get("name") == "Neha Sharma" and existing.get("player_type") == "Daily":
                patch["player_type"] = "Day Boarding"
            if patch:
                await db.people.update_one({"id": existing["id"]}, {"$set": patch})

    # Auto-create fees for existing seeded players (idempotent — ALPHA only)
    try:
        from routers.fees import auto_create_fees_for_player
        all_alpha_players = await db.people.find({"kind": "player", "organization": "ALPHA"}, {"_id": 0}).to_list(1000)
        for p in all_alpha_players:
            await auto_create_fees_for_player(p)
    except Exception:
        pass

    # Sample staff — stored as non-login Person records
    sample_staff = [
        # (name, role, org, centre)
        ("Reena Devi", "Canteen Supervisor", "PWS", None),
        ("Manoj Pandey", "Lab Assistant", "PWS", None),
        ("Geeta Kumari", "Librarian", "PWS", None),
        ("Alok Singh", "Groundsman", "ALPHA", "Balua"),
        ("Neeraj Raj", "Kit Manager", "ALPHA", "Balua"),
        ("Sunita Das", "Physio", "ALPHA", "Harding Park"),
    ]
    for name, role_title, org, centre in sample_staff:
        existing = await db.people.find_one({"name": name, "kind": "staff"})
        base = {
            "kind": "staff",
            "name": name,
            "group": role_title,
            "organization": org,
            "centre": centre,
            "is_resident": False,
        }
        if not existing:
            await db.people.insert_one({"id": str(uuid.uuid4()), **base})
        else:
            patch = {k: v for k, v in base.items() if existing.get(k) is None}
            if patch:
                await db.people.update_one({"id": existing["id"]}, {"$set": patch})

    # Link demo parent accounts to wards (idempotent two-way link)
    parent_links = [
        ("parent_pws@prarambhika.com", {"name": "Aarav Mishra", "kind": "student"}),
        ("parent_alpha@prarambhika.com", {"name": "Aditya Verma", "kind": "player"}),
    ]
    for parent_email, ward_filter in parent_links:
        parent = await db.users.find_one({"email": parent_email})
        ward = await db.people.find_one(ward_filter)
        if parent and ward:
            await db.users.update_one(
                {"email": parent_email},
                {"$addToSet": {"linked_person_ids": ward["id"]}},
            )
            await db.people.update_one(
                {"id": ward["id"]},
                {"$addToSet": {"parent_user_ids": parent["id"]}},
            )

    if await db.tasks.count_documents({}) == 0:
        admin = await db.users.find_one({"email": "admin@prarambhika.com"})
        teacher = await db.users.find_one({"email": "teacher@prarambhika.com"})
        coach = await db.users.find_one({"email": "coach@prarambhika.com"})
        warden = await db.users.find_one({"email": "warden@prarambhika.com"})
        for t in [
            {"title": "Submit weekly lesson plan", "desc": "Share Class 9-A maths lesson plan", "p": "high", "ass": [teacher["id"]]},
            {"title": "Cricket fitness drill report", "desc": "Compile U-15 fitness data", "p": "medium", "ass": [coach["id"]]},
            {"title": "Hostel inspection round", "desc": "Verify cleanliness in Boys Hostel B1", "p": "high", "ass": [warden["id"]]},
            {"title": "Canteen hygiene audit", "desc": "Run Friday canteen checklist", "p": "low", "ass": []},
        ]:
            await db.tasks.insert_one({
                "id": str(uuid.uuid4()),
                "title": t["title"],
                "description": t["desc"],
                "priority": t["p"],
                "deadline": (now_utc() + timedelta(days=3)).isoformat(),
                "assignee_ids": t["ass"],
                "department": None,
                "follow_up_required": False,
                "status": "assigned",
                "created_by": admin["id"],
                "created_by_name": admin["name"],
                "created_at": now_utc().isoformat(),
                "updated_at": now_utc().isoformat(),
                "completion_remark": None,
                "proof_url": None,
                "comments": [],
            })


async def _seed_academic_structure():
    """Academic year, grades, sections, teacher assignment, student section_id backfill."""
    year = await db.academic_years.find_one({"name": "2025-26", "entity_id": "pws"})
    if not year:
        year = {
            "id": str(uuid.uuid4()),
            "name": "2025-26",
            "entity_id": "pws",
            "status": "open",
            "start_date": "2025-04-01",
            "end_date": "2026-03-31",
            "created_at": now_utc().isoformat(),
        }
        await db.academic_years.insert_one(year)
    year_id = year["id"]

    grade_ids: dict[str, str] = {}
    for gname, sort in [("9", 9), ("10", 10)]:
        existing = await db.grades.find_one({"academic_year_id": year_id, "name": gname})
        if existing:
            grade_ids[gname] = existing["id"]
        else:
            gdoc = {
                "id": str(uuid.uuid4()),
                "academic_year_id": year_id,
                "name": gname,
                "entity_id": "pws",
                "sort_order": sort,
                "created_at": now_utc().isoformat(),
            }
            await db.grades.insert_one(gdoc)
            grade_ids[gname] = gdoc["id"]

    section_ids: dict[str, str] = {}
    for gname, sname, label in [("9", "A", "9-A"), ("10", "B", "10-B")]:
        existing = await db.sections.find_one({"academic_year_id": year_id, "label": label})
        if existing:
            section_ids[label] = existing["id"]
        else:
            sdoc = {
                "id": str(uuid.uuid4()),
                "academic_year_id": year_id,
                "grade_id": grade_ids[gname],
                "grade_name": gname,
                "name": sname,
                "label": label,
                "entity_id": "pws",
                "created_at": now_utc().isoformat(),
            }
            await db.sections.insert_one(sdoc)
            section_ids[label] = sdoc["id"]

    for label, sid in section_ids.items():
        await db.people.update_many(
            {"kind": "student", "group": label},
            {"$set": {"section_id": sid}},
        )

    teacher = await db.users.find_one({"email": "teacher@prarambhika.com", "role": "teacher"})
    nine_a = section_ids.get("9-A")
    if teacher and nine_a:
        if not await db.teacher_section_assignments.find_one({
            "teacher_user_id": teacher["id"],
            "section_id": nine_a,
            "academic_year_id": year_id,
        }):
            await db.teacher_section_assignments.insert_one({
                "id": str(uuid.uuid4()),
                "teacher_user_id": teacher["id"],
                "section_id": nine_a,
                "academic_year_id": year_id,
                "created_at": now_utc().isoformat(),
                "created_by": "seed",
            })
