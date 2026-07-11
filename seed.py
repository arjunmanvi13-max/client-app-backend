"""Seed demo data on app startup (idempotent — safe for production restarts)."""
import uuid
from datetime import timedelta
from pymongo.errors import DuplicateKeyError
from core import db, hash_password, now_utc, logger

DEMO_USERS = [
    {"email": "admin@prarambhika.com", "password": "Admin@123", "name": "Rohan Sharma", "role": "admin", "organization": "ALPHA", "department": "ALPHA Operations"},
    {"email": "super@prarambhika.com", "password": "Super@123", "name": "Anita Verma", "role": "super_admin", "organization": "BOTH", "department": "Trustee"},
    {"email": "principal@prarambhika.com", "password": "Principal@123", "name": "Meera Nair", "role": "principal", "organization": "PWS", "department": "Administration"},
    {"email": "vp@prarambhika.com", "password": "Vp@123", "name": "Arun Pandey", "role": "vice_principal", "organization": "PWS", "department": "Administration"},
    {"email": "teacher@prarambhika.com", "password": "Teacher@123", "name": "Priya Kumari", "role": "teacher", "organization": "PWS", "department": "Mathematics"},
    {"email": "coach@prarambhika.com", "password": "Coach@123", "name": "Vikram Singh", "role": "coach", "organization": "ALPHA", "department": "Cricket", "coach_type": "head"},
    {"email": "asst_coach@prarambhika.com", "password": "Asst@123", "name": "Ravi Kumar", "role": "coach", "organization": "ALPHA", "department": "Cricket", "coach_type": "assistant"},
    {"email": "warden@prarambhika.com", "password": "Warden@123", "name": "Suresh Yadav", "role": "warden", "organization": "BOTH", "department": "Boys Hostel"},
    {"email": "student@prarambhika.com", "password": "Student@123", "name": "Aarav Mishra", "role": "student", "organization": "PWS", "department": "Class 9-A"},
    {"email": "player@prarambhika.com", "password": "Player@123", "name": "Karan Raj", "role": "player", "organization": "ALPHA", "department": "U-15 Cricket"},
    {"email": "parent_pws@prarambhika.com", "password": "Parent@123", "name": "Sunil Mishra", "role": "parent", "organization": "PWS", "department": "Parent"},
    {"email": "parent_alpha@prarambhika.com", "password": "Parent@123", "name": "Anita Verma (Parent)", "role": "parent", "organization": "ALPHA", "department": "Parent"},
]

# Super Admins — email + password login (domain-restricted like everyone else).
SUPER_ADMIN_SEEDS = [
    {"email": "superadmin@prarambhika.com", "password": "Super@123", "name": "Super Admin 1", "organization": "BOTH", "department": "Trustee"},
    {"email": "superadmin2@prarambhika.com", "password": "Super@123", "name": "Super Admin 2", "organization": "BOTH", "department": "Trustee"},
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


async def _insert_if_absent(collection, query: dict, doc: dict) -> bool:
    """Insert a document only when no row matches query. Never updates existing rows."""
    if await collection.find_one(query, {"_id": 1}):
        return False
    try:
        await collection.insert_one(doc)
        return True
    except DuplicateKeyError:
        logger.warning("Seed insert skipped — duplicate key for %s on %s", query, collection.name)
        return False


def _strip_sparse_user_fields(doc: dict) -> dict:
    """Omit mobile/phone when unset — avoids sparse unique index collisions on null."""
    out = dict(doc)
    for key in ("mobile", "phone"):
        if key in out and not out[key]:
            out.pop(key)
    return out


async def _sanitize_null_mobile_fields() -> None:
    """Unset null/empty mobile and phone on users.

    MongoDB sparse unique indexes treat explicit null as a value; only one document
    may have mobile=null. Removing the field is safe and does not delete users.
    """
    res = await db.users.update_many(
        {"$or": [{"mobile": None}, {"mobile": ""}]},
        {"$unset": {"mobile": ""}},
    )
    if res.modified_count:
        logger.info("Unset empty mobile on %s user(s)", res.modified_count)
    res2 = await db.users.update_many(
        {"$or": [{"phone": None}, {"phone": ""}]},
        {"$unset": {"phone": ""}},
    )
    if res2.modified_count:
        logger.info("Unset empty phone on %s user(s)", res2.modified_count)


async def _seed_user_if_absent(email: str, doc: dict) -> bool:
    """Insert a user only when email is absent. Never updates existing production users."""
    if await db.users.find_one({"email": email}, {"_id": 1}):
        return False
    payload = _strip_sparse_user_fields(doc)
    payload["email"] = email
    try:
        result = await db.users.update_one(
            {"email": email},
            {"$setOnInsert": payload},
            upsert=True,
        )
        return result.upserted_id is not None
    except DuplicateKeyError:
        logger.warning("User seed skipped — duplicate key for email=%s", email)
        return False


async def _ensure_indexes() -> None:
    """Ensure required indexes exist. Preserves existing unique indexes when already correct."""
    try:
        idx_info = await db.users.index_information()
        email_idx = idx_info.get("email_1")
        if email_idx is None:
            await db.users.create_index("email", unique=True, sparse=True)
        elif not email_idx.get("sparse"):
            await db.users.drop_index("email_1")
            await db.users.create_index("email", unique=True, sparse=True)
    except Exception as exc:
        logger.warning("Could not ensure users.email index: %s", exc)
    try:
        idx_info = await db.users.index_information()
        if "mobile_1" not in idx_info:
            await db.users.create_index("mobile", unique=True, sparse=True)
    except Exception as exc:
        logger.warning("Could not ensure users.mobile index: %s", exc)
    try:
        otp_idx = await db.otps.index_information()
        if "expires_at_1" not in otp_idx:
            await db.otps.create_index("expires_at", expireAfterSeconds=0)
        if "mobile_1_purpose_1_created_at_-1" not in otp_idx:
            await db.otps.create_index([("mobile", 1), ("purpose", 1), ("created_at", -1)])
    except Exception as exc:
        logger.warning("Could not ensure otps indexes: %s", exc)


async def _migrate_legacy_emails() -> None:
    """One-time: rename @pws-alpha.com accounts only when the target email is free."""
    legacy = await db.users.find({"email": {"$regex": "@pws-alpha\\.com$"}}).to_list(200)
    for lu in legacy:
        new_email = lu["email"].split("@")[0] + "@prarambhika.com"
        if await db.users.find_one({"email": new_email}, {"_id": 1}):
            continue
        try:
            await db.users.update_one({"id": lu["id"]}, {"$set": {"email": new_email}})
        except DuplicateKeyError:
            logger.warning("Legacy email migration skipped for %s — target taken", lu.get("email"))


async def _clear_all_phone_numbers() -> None:
    """One-time removal of mobile/phone fields from all users and people."""
    marker = await db.app_meta.find_one({"_id": "phones_cleared_v1"})
    if marker:
        return
    user_res = await db.users.update_many(
        {},
        {"$unset": {"mobile": "", "phone": ""}},
    )
    people_res = await db.people.update_many(
        {},
        {"$unset": {"mobile": "", "guardian_phone": "", "phone": ""}},
    )
    await db.otps.delete_many({})
    await db.app_meta.update_one(
        {"_id": "phones_cleared_v1"},
        {"$set": {"cleared_at": now_utc().isoformat(), "users_modified": user_res.modified_count, "people_modified": people_res.modified_count}},
        upsert=True,
    )
    logger.info(
        "Cleared all phone numbers — users: %s, people: %s",
        user_res.modified_count,
        people_res.modified_count,
    )


async def _seed_demo_users() -> None:
    """Insert demo users only when their email is not already in the database."""
    for u in DEMO_USERS:
        defaults = ROLE_DEFAULT_CAN_MANAGE.get(u["role"], [])
        coach_defaults = ROLE_DEFAULT_COACH_PERMS.get(u["role"], [])
        doc = {
            "id": str(uuid.uuid4()),
            "password_hash": hash_password(u["password"]),
            "is_password_set": True,
            "name": u["name"],
            "role": u["role"],
            "organization": u["organization"],
            "department": u["department"],
            "can_manage": defaults,
            "coach_permissions": coach_defaults,
            "coach_type": u.get("coach_type"),
            "assigned_sport": "Cricket" if u["role"] == "coach" else None,
            "assigned_centres": ["Balua"] if u["role"] == "coach" else [],
            "assigned_sports": ["Cricket", "Football"] if u["role"] == "coach" else [],
            "created_at": now_utc().isoformat(),
        }
        await _seed_user_if_absent(u["email"], doc)


async def _seed_super_admins() -> None:
    """Insert super-admin accounts only when email is not already in the database."""
    for sa in SUPER_ADMIN_SEEDS:
        doc = {
            "id": str(uuid.uuid4()),
            "password_hash": hash_password(sa["password"]),
            "is_password_set": True,
            "must_change_password": False,
            "name": sa["name"],
            "role": "super_admin",
            "organization": sa["organization"],
            "department": sa["department"],
            "can_manage": ["student", "player", "teacher", "coach", "staff"],
            "coach_permissions": [],
            "coach_type": None,
            "assigned_sport": None,
            "assigned_centres": [],
            "assigned_sports": [],
            "created_at": now_utc().isoformat(),
        }
        await _seed_user_if_absent(sa["email"], doc)


async def _backfill_staff_user_accounts() -> None:
    """Link staff people to user accounts — failures on individual rows are non-fatal."""
    from routers.people import ensure_staff_user_account
    async for sp in db.people.find({"kind": "staff"}, {"_id": 0}):
        try:
            await ensure_staff_user_account(sp)
        except DuplicateKeyError:
            logger.warning("Staff user sync skipped — duplicate key for %s", sp.get("name"))
        except Exception as exc:
            logger.warning("Staff user sync failed for %s: %s", sp.get("name"), exc)


async def seed_data():
    """Idempotent startup seed. Never raises — safe to call on every boot."""
    logger.info("Starting idempotent seed (insert-only, no production user overwrites)")
    try:
        await _run_seed()
    except DuplicateKeyError as exc:
        logger.warning("Seed duplicate key (non-fatal, startup continues): %s", exc)
    except Exception as exc:
        logger.exception("Seed failed (non-fatal, startup continues): %s", exc)


async def _run_seed():
    """Idempotent startup seed. Never overwrites existing production records."""
    await _ensure_indexes()
    await _sanitize_null_mobile_fields()

    for step_name, step in [
        ("clear_phone_numbers", _clear_all_phone_numbers),
        ("legacy_emails", _migrate_legacy_emails),
        ("staff_accounts", _backfill_staff_user_accounts),
        ("demo_users", _seed_demo_users),
        ("super_admins", _seed_super_admins),
    ]:
        try:
            await step()
        except DuplicateKeyError as exc:
            logger.warning("Seed step %s duplicate key (continuing): %s", step_name, exc)
        except Exception as exc:
            logger.exception("Seed step %s failed (continuing): %s", step_name, exc)

    sample_students = [
        ("Aarav Mishra", "9-A"), ("Isha Sinha", "9-A"), ("Rohit Kumar", "9-A"),
        ("Sneha Singh", "9-A"), ("Aman Raj", "9-A"), ("Kavya Patel", "9-A"),
        ("Dev Ranjan", "10-B"), ("Pooja Devi", "10-B"), ("Manish Roy", "10-B"),
        ("Tanvi Jha", "10-B"),
    ]
    for name, cls in sample_students:
        try:
            await _insert_if_absent(
                db.people,
                {"name": name, "kind": "student"},
                {
                    "id": str(uuid.uuid4()),
                    "kind": "student",
                    "name": name,
                    "group": cls,
                    "organization": "PWS",
                    "is_resident": cls == "9-A",
                    "date_of_admission": "2025-04-15",
                },
            )
        except DuplicateKeyError:
            logger.warning("Sample student seed skipped — duplicate key for %s", name)
        except Exception as exc:
            logger.warning("Sample student seed failed for %s: %s", name, exc)

    for step_name, step in [
        ("academic_structure", _seed_academic_structure),
        ("academic_marks", _seed_academic_marks),
        ("coach_assessments", _seed_coach_assessments),
        ("pws_student_fees", _seed_pws_student_fees),
        ("entity_settings", _seed_entity_settings),
        ("fee_catalog", _seed_fee_catalog),
        ("entity_foundation", _migrate_entity_foundation),
        ("attendance_mvp", _migrate_attendance_mvp),
        ("enrollment_ids", _backfill_enrollment_ids),
        ("report_cards", _seed_report_cards),
    ]:
        try:
            await step()
        except Exception as exc:
            logger.exception("Seed step %s failed (continuing): %s", step_name, exc)

    try:
        await _seed_people_and_links()
    except Exception as exc:
        logger.exception("Seed step people_and_links failed (continuing): %s", exc)


async def _seed_people_and_links() -> None:
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
            await _insert_if_absent(db.people, {"name": name, "kind": "player"}, {"id": str(uuid.uuid4()), **base})
        else:
            patch = {k: v for k, v in base.items() if k not in existing or existing.get(k) is None}
            if "date_of_admission" not in existing or not existing.get("date_of_admission"):
                patch["date_of_admission"] = six_months_ago
            if "status" not in existing or not existing.get("status"):
                patch["status"] = "active"
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
            await _insert_if_absent(db.people, {"name": name, "kind": "staff"}, {"id": str(uuid.uuid4()), **base})
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
        if not admin:
            return
        for t in [
            {"title": "Submit weekly lesson plan", "desc": "Share Class 9-A maths lesson plan", "p": "high", "ass": [teacher["id"]] if teacher else [], "entity": "pws"},
            {"title": "Cricket fitness drill report", "desc": "Compile U-15 fitness data", "p": "medium", "ass": [coach["id"]] if coach else [], "entity": "alpha"},
            {"title": "Hostel inspection round", "desc": "Verify cleanliness in Boys Hostel B1", "p": "high", "ass": [warden["id"]] if warden else [], "entity": "pws"},
            {"title": "Canteen hygiene audit", "desc": "Run Friday canteen checklist", "p": "low", "ass": [], "entity": "pws"},
        ]:
            due = (now_utc() + timedelta(days=3)).isoformat()
            assignee_id = t["ass"][0] if t["ass"] else None
            await _insert_if_absent(db.tasks, {"title": t["title"], "created_by": admin["id"]}, {
                "id": str(uuid.uuid4()),
                "title": t["title"],
                "description": t["desc"],
                "entity_id": t["entity"],
                "priority": t["p"],
                "due_date": due,
                "deadline": due,
                "assignee_id": assignee_id,
                "assignee_ids": t["ass"],
                "department": None,
                "follow_up_required": False,
                "status": "open",
                "created_by": admin["id"],
                "created_by_name": admin["name"],
                "created_at": now_utc().isoformat(),
                "updated_at": now_utc().isoformat(),
                "completed_at": None,
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
    elif year.get("status") == "planned":
        await db.academic_years.update_one({"id": year["id"]}, {"$set": {"status": "open"}})
        year["status"] = "open"
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
            {"kind": "student", "group": label, "section_id": {"$exists": False}},
            {"$set": {"section_id": sid}},
        )

    teacher = await db.users.find_one({"email": "teacher@prarambhika.com", "role": "teacher"})
    nine_a = section_ids.get("9-A")
    if teacher and nine_a:
        grade_9 = grade_ids.get("9")
        subject_ids: dict[str, str] = {}
        for sname, code, sort in [
            ("Mathematics", "MATH", 1),
            ("English", "ENG", 2),
            ("Science", "SCI", 3),
        ]:
            existing = await db.subjects.find_one({"academic_year_id": year_id, "name": sname})
            if existing:
                subject_ids[sname] = existing["id"]
                if not existing.get("grade_ids"):
                    await db.subjects.update_one(
                        {"id": existing["id"]},
                        {"$set": {"grade_ids": [grade_9] if grade_9 else [], "section_ids": [nine_a]}},
                    )
            else:
                sdoc = {
                    "id": str(uuid.uuid4()),
                    "academic_year_id": year_id,
                    "name": sname,
                    "code": code,
                    "sort_order": sort,
                    "grade_ids": [grade_9] if grade_9 else [],
                    "section_ids": [nine_a],
                    "entity_id": "pws",
                    "created_at": now_utc().isoformat(),
                }
                await db.subjects.insert_one(sdoc)
                subject_ids[sname] = sdoc["id"]

        math_id = subject_ids.get("Mathematics")
        if math_id and not await db.teacher_class_assignments.find_one({
            "teacher_user_id": teacher["id"],
            "section_id": nine_a,
            "subject_id": math_id,
            "academic_year_id": year_id,
        }):
            await db.teacher_class_assignments.insert_one({
                "id": str(uuid.uuid4()),
                "teacher_user_id": teacher["id"],
                "academic_year_id": year_id,
                "grade_id": grade_9,
                "section_id": nine_a,
                "subject_id": math_id,
                "created_at": now_utc().isoformat(),
                "created_by": "seed",
            })

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


async def _seed_academic_marks():
    """Subjects, exam term, grading scale, and sample marks for 9-A (idempotent)."""
    year = await db.academic_years.find_one({"name": "2025-26", "entity_id": "pws"})
    if not year:
        return
    year_id = year["id"]
    section = await db.sections.find_one({"academic_year_id": year_id, "label": "9-A"})
    if not section:
        return
    section_id = section["id"]

    subject_defs = [
        ("Mathematics", "MATH", 1),
        ("English", "ENG", 2),
        ("Science", "SCI", 3),
        ("Hindi", "HIN", 4),
    ]
    subject_ids: dict[str, str] = {}
    for name, code, sort in subject_defs:
        existing = await db.subjects.find_one({"academic_year_id": year_id, "name": name})
        if existing:
            subject_ids[name] = existing["id"]
        else:
            doc = {
                "id": str(uuid.uuid4()),
                "academic_year_id": year_id,
                "name": name,
                "code": code,
                "sort_order": sort,
                "entity_id": "pws",
                "created_at": now_utc().isoformat(),
            }
            await db.subjects.insert_one(doc)
            subject_ids[name] = doc["id"]

    term = await db.exam_terms.find_one({"academic_year_id": year_id, "name": "Term 1"})
    if not term:
        term = {
            "id": str(uuid.uuid4()),
            "academic_year_id": year_id,
            "name": "Term 1",
            "start_date": "2025-04-01",
            "end_date": "2025-09-30",
            "is_active": True,
            "entity_id": "pws",
            "created_at": now_utc().isoformat(),
        }
        await db.exam_terms.insert_one(term)

    if not await db.grading_scales.find_one({"academic_year_id": year_id, "is_default": True}):
        await db.grading_scales.insert_one({
            "id": str(uuid.uuid4()),
            "academic_year_id": year_id,
            "name": "CBSE-style",
            "bands": [
                {"min": 91, "max": 100, "grade": "A1", "description": "Outstanding"},
                {"min": 81, "max": 90, "grade": "A2", "description": "Excellent"},
                {"min": 71, "max": 80, "grade": "B1", "description": "Very Good"},
                {"min": 61, "max": 70, "grade": "B2", "description": "Good"},
                {"min": 51, "max": 60, "grade": "C1", "description": "Average"},
                {"min": 41, "max": 50, "grade": "C2", "description": "Below Average"},
                {"min": 33, "max": 40, "grade": "D", "description": "Pass"},
                {"min": 0, "max": 32, "grade": "E", "description": "Needs Improvement"},
            ],
            "is_default": True,
            "entity_id": "pws",
            "created_at": now_utc().isoformat(),
        })

    scale = await db.grading_scales.find_one({"academic_year_id": year_id, "is_default": True}, {"_id": 0})
    bands = (scale or {}).get("bands") or []
    from routers.marks import grade_for_score, percentage_for_score

    math_id = subject_ids.get("Mathematics")
    assessment_id = None
    if math_id and term:
        existing_asm = await db.assessments.find_one({
            "exam_term_id": term["id"],
            "section_id": section_id,
            "subject_id": math_id,
            "name": "Unit Test 1",
        })
        if existing_asm:
            assessment_id = existing_asm["id"]
        else:
            asm = {
                "id": str(uuid.uuid4()),
                "academic_year_id": year_id,
                "exam_term_id": term["id"],
                "section_id": section_id,
                "subject_id": math_id,
                "grade_id": section.get("grade_id"),
                "name": "Unit Test 1",
                "max_marks": 100,
                "entity_id": "pws",
                "created_at": now_utc().isoformat(),
                "created_by": "seed",
            }
            await db.assessments.insert_one(asm)
            assessment_id = asm["id"]
        for sname in subject_ids:
            if sname == "Mathematics":
                continue
            sid = subject_ids[sname]
            if not await db.assessments.find_one({
                "exam_term_id": term["id"], "section_id": section_id, "subject_id": sid, "name": "Unit Test 1",
            }):
                await db.assessments.insert_one({
                    "id": str(uuid.uuid4()),
                    "academic_year_id": year_id,
                    "exam_term_id": term["id"],
                    "section_id": section_id,
                    "subject_id": sid,
                    "grade_id": section.get("grade_id"),
                    "name": "Unit Test 1",
                    "max_marks": 100,
                    "entity_id": "pws",
                    "created_at": now_utc().isoformat(),
                    "created_by": "seed",
                })

    sample_scores = {
        "Aarav Mishra": {"Mathematics": 88, "English": 76, "Science": 82, "Hindi": 71},
        "Isha Sinha": {"Mathematics": 92, "English": 85, "Science": 90, "Hindi": 78},
        "Rohit Kumar": {"Mathematics": 65, "English": 58, "Science": 62, "Hindi": 55},
    }
    students = await db.people.find(
        {"kind": "student", "section_id": section_id, "name": {"$in": list(sample_scores.keys())}},
        {"_id": 0},
    ).to_list(20)
    ts = now_utc().isoformat()
    for st in students:
        scores = sample_scores.get(st["name"], {})
        for sub_name, score in scores.items():
            sid = subject_ids.get(sub_name)
            if not sid:
                continue
            asm = await db.assessments.find_one({
                "exam_term_id": term["id"],
                "section_id": section_id,
                "subject_id": sid,
                "name": "Unit Test 1",
            })
            asm_id = (asm or {}).get("id")
            filt = {"person_id": st["id"], "subject_id": sid, "exam_term_id": term["id"]}
            if asm_id:
                filt = {"person_id": st["id"], "assessment_id": asm_id}
            if await db.academic_marks.find_one(filt):
                continue
            max_m = (asm or {}).get("max_marks", 100)
            await db.academic_marks.insert_one({
                "id": str(uuid.uuid4()),
                "person_id": st["id"],
                "assessment_id": asm_id,
                "section_id": section_id,
                "subject_id": sid,
                "exam_term_id": term["id"],
                "academic_year_id": year_id,
                "marks_obtained": score,
                "max_marks": max_m,
                "percentage": percentage_for_score(score, max_m),
                "grade": grade_for_score(score, bands, max_m),
                "status": "published",
                "entity_id": "pws",
                "entered_by": "seed",
                "entered_by_name": "Seed",
                "entered_at": ts,
                "published_at": ts,
                "created_at": ts,
                "updated_at": ts,
            })


async def _seed_coach_assessments():
    """Coach assessment definitions and sample published results for ALPHA players."""
    defs = [
        ("Batting Technique", "rating", "Cricket", "Balua", "Morning", None),
        ("Fitness Test", "score", "Cricket", "Balua", "Morning", 100),
        ("Match Simulation", "test", "Cricket", "Balua", "Evening", 50),
        ("Dribbling Skills", "rating", "Football", "Balua", "Evening", None),
    ]
    def_ids: dict[str, str] = {}
    for name, atype, sport, centre, slot, max_score in defs:
        existing = await db.coach_assessment_definitions.find_one({"name": name, "sport": sport})
        if existing:
            def_ids[name] = existing["id"]
        else:
            doc = {
                "id": str(uuid.uuid4()),
                "name": name,
                "assessment_type": atype,
                "sport": sport,
                "centre": centre,
                "slot": slot,
                "max_score": max_score,
                "rating_labels": ["Needs work", "Developing", "Good", "Very good", "Excellent"],
                "entity_id": "alpha",
                "created_at": now_utc().isoformat(),
                "created_by": "seed",
            }
            await db.coach_assessment_definitions.insert_one(doc)
            def_ids[name] = doc["id"]

    karan = await db.people.find_one({"name": "Karan Raj", "kind": "player"})
    aditya = await db.people.find_one({"name": "Aditya Verma", "kind": "player"})
    fitness_id = def_ids.get("Fitness Test")
    batting_id = def_ids.get("Batting Technique")
    ts = now_utc().isoformat()
    today = ts[:10]
    for player in [karan, aditya]:
        if not player:
            continue
        samples = [
            (fitness_id, "score", 78.0, 100, None, "Strong endurance; work on sprint drills."),
            (batting_id, "rating", None, None, "Very good", "Improved footwork in nets."),
        ]
        for def_id, atype, score, max_s, rating, remark in samples:
            if not def_id:
                continue
            defn = await db.coach_assessment_definitions.find_one({"id": def_id})
            if not defn:
                continue
            if await db.player_assessments.find_one({"player_id": player["id"], "definition_id": def_id, "date": today}):
                continue
            await db.player_assessments.insert_one({
                "id": str(uuid.uuid4()),
                "player_id": player["id"],
                "definition_id": def_id,
                "definition_name": defn["name"],
                "date": today,
                "sport": player.get("sport", "Cricket"),
                "centre": player.get("centre", "Balua"),
                "slot": player.get("slot", "Morning"),
                "assessment_type": atype,
                "score": score,
                "max_score": max_s or defn.get("max_score"),
                "rating": rating,
                "coach_remark": remark,
                "status": "published",
                "entity_id": "alpha",
                "entered_by": "seed",
                "entered_by_name": "Seed Coach",
                "entered_at": ts,
                "published_at": ts,
                "created_at": ts,
                "updated_at": ts,
            })


async def _migrate_entity_foundation():
    """Backfill entity fields on people, attendance, fees (idempotent)."""
    from core import derive_person_entities, attendance_entity_for_kind

    people = await db.people.find({"entities": {"$exists": False}}, {"_id": 0}).to_list(2000)
    for p in people:
        ents = derive_person_entities(p)
        await db.people.update_one({"id": p["id"]}, {"$set": {"entities": ents}})

    fees = await db.fees.find({"entity_id": {"$exists": False}}, {"id": 1, "player_id": 1, "_id": 0}).to_list(5000)
    for f in fees:
        person = await db.people.find_one({"id": f.get("player_id")}, {"_id": 0, "kind": 1, "organization": 1, "entities": 1})
        if not person or not f.get("id"):
            continue
        from routers.fees import _fee_entity
        await db.fees.update_one({"id": f["id"]}, {"$set": {"entity_id": _fee_entity(person)}})

    att = await db.attendance.find({"entity_id": {"$exists": False}}, {"kind": 1, "_id": 1}).to_list(5000)
    for a in att:
        ent = attendance_entity_for_kind(a.get("kind"))
        if ent:
            await db.attendance.update_one({"_id": a["_id"]}, {"$set": {"entity_id": ent}})

    # Dual-participation demo: Aarav Mishra is a PWS student who also trains at ALPHA
    aarav = await db.people.find_one({"name": "Aarav Mishra", "kind": "student"})
    if aarav and aarav.get("entities") == ["PWS"]:
        await db.people.update_one(
            {"id": aarav["id"]},
            {"$set": {"entities": ["PWS", "ALPHA"], "organization": "BOTH"}},
        )

    await _backfill_enrollment_ids()


async def _backfill_enrollment_ids():
    """Assign admission / player / employee IDs to seeded records (idempotent)."""
    seq = 1
    students = await db.people.find({"kind": "student"}, {"_id": 0, "id": 1, "name": 1, "admission_number": 1, "roll_number": 1}).sort("name", 1).to_list(200)
    for i, st in enumerate(students, start=1):
        patch = {}
        if not st.get("admission_number"):
            patch["admission_number"] = f"PWS-{2025}{i:04d}"
        if not st.get("roll_number"):
            patch["roll_number"] = str(100 + i)
        if patch:
            await db.people.update_one({"id": st["id"]}, {"$set": patch})

    players = await db.people.find({"kind": "player"}, {"_id": 0, "id": 1, "player_id": 1}).sort("name", 1).to_list(200)
    for i, pl in enumerate(players, start=1):
        if not pl.get("player_id"):
            await db.people.update_one({"id": pl["id"]}, {"$set": {"player_id": f"APL-{i:04d}"}})

    staff = await db.people.find({"kind": "staff"}, {"_id": 0, "id": 1, "employee_id": 1}).sort("name", 1).to_list(200)
    for i, st in enumerate(staff, start=1):
        if not st.get("employee_id"):
            await db.people.update_one({"id": st["id"]}, {"$set": {"employee_id": f"EMP-{i:04d}"}})


async def _migrate_attendance_mvp():
    """Backfill session, marked_at, source on legacy attendance rows (idempotent)."""
    from routers.attendance import normalize_session

    rows = await db.attendance.find({}, {"_id": 1}).to_list(10000)
    for a in rows:
        full = await db.attendance.find_one({"_id": a["_id"]}, {"_id": 0})
        if not full:
            continue
        patch: dict = {}
        kind = full.get("kind", "student")
        if not full.get("session"):
            slot = full.get("slot")
            patch["session"] = normalize_session(None, slot=slot, kind=kind)
        if not full.get("marked_at"):
            patch["marked_at"] = full.get("created_at") or now_utc().isoformat()
        if not full.get("source"):
            patch["source"] = "legacy"
        if not full.get("entity_id"):
            from core import attendance_entity_for_kind
            ent = attendance_entity_for_kind(kind)
            if ent:
                patch["entity_id"] = ent
        if patch:
            await db.attendance.update_one({"_id": a["_id"]}, {"$set": patch})

    await db.roll_calls.update_many({"session": "night"}, {"$set": {"session": "evening"}})


async def _seed_pws_student_fees():
    """Backfill PWS school fees for existing students (idempotent)."""
    from routers.fees import auto_create_fees_for_student, ensure_monthly_fees_up_to_current
    students = await db.people.find(
        {"kind": "student", "organization": "PWS"},
        {"_id": 0},
    ).to_list(500)
    for s in students:
        if not s.get("date_of_admission"):
            await db.people.update_one({"id": s["id"]}, {"$set": {"date_of_admission": "2025-04-15"}})
            s["date_of_admission"] = "2025-04-15"
        await auto_create_fees_for_student(s)
        await ensure_monthly_fees_up_to_current(s["id"])


async def _seed_entity_settings():
    """Invoice engine feature flags — disabled by default (legacy fees remain active)."""
    for eid in ("alpha", "pws"):
        existing = await db.entity_settings.find_one({"entity_id": eid})
        if not existing:
            await db.entity_settings.insert_one({
                "entity_id": eid,
                "use_invoice_engine": False,
                "school_name": "Prarambhika World School" if eid == "pws" else "ALPHA Sports Academy",
                "tagline": "Excellence in Education & Character" if eid == "pws" else "Train · Compete · Excel",
                "tax_rate_percent": 0,
                "updated_at": now_utc().isoformat(),
            })
        elif eid == "pws" and not existing.get("school_name"):
            await db.entity_settings.update_one(
                {"entity_id": eid},
                {"$set": {
                    "school_name": "Prarambhika World School",
                    "tagline": "Excellence in Education & Character",
                    "tax_rate_percent": 0,
                    "updated_at": now_utc().isoformat(),
                }},
            )


async def _seed_report_cards():
    """Sample draft + published report cards for Term 1 (idempotent)."""
    from routers.report_cards import build_report_card_data

    year = await db.academic_years.find_one({"name": "2025-26", "entity_id": "pws"})
    term = await db.exam_terms.find_one({"academic_year_id": (year or {}).get("id"), "name": "Term 1"}) if year else None
    if not term:
        return
    aarav = await db.people.find_one({"name": "Aarav Mishra", "kind": "student"})
    isha = await db.people.find_one({"name": "Isha Sinha", "kind": "student"})
    if not aarav:
        return
    ts = now_utc().isoformat()

    for person, status, teacher_remark, coach_remark in [
        (
            aarav,
            "published",
            "Aarav is diligent and participates actively in class discussions. Keep up the good work in Science.",
            "Shows strong discipline and teamwork during cricket training sessions.",
        ),
        (
            isha,
            "draft",
            None,
            None,
        ) if isha else (None, None, None, None),
    ]:
        if not person:
            continue
        existing = await db.report_cards.find_one({"person_id": person["id"], "exam_term_id": term["id"]})
        if existing:
            continue
        data = await build_report_card_data(person["id"], term["id"])
        doc = {
            **data,
            "id": str(uuid.uuid4()),
            "status": status,
            "teacher_remark": teacher_remark,
            "approved_coach_remark": coach_remark if ("ALPHA" in (person.get("entities") or []) or person.get("organization") == "BOTH") else None,
            "built_at": ts,
            "built_by": "seed",
            "created_at": ts,
            "updated_at": ts,
        }
        if status == "published":
            doc["published_at"] = ts
            doc["published_by"] = "seed"
            doc["submitted_at"] = ts
            doc["submitted_by"] = "seed"
        await _insert_if_absent(
            db.report_cards,
            {"person_id": person["id"], "exam_term_id": term["id"]},
            doc,
        )


async def _seed_fee_catalog():
    """Migrate hardcoded rate cards into fee catalogue + plans (idempotent)."""
    from routers.fees import RATE_CARDS, PWS_RATE_CARDS
    from routers.fee_catalog import LEGACY_FEE_TYPE

    year = await db.academic_years.find_one({"name": "2025-26", "entity_id": "pws"})
    year_id = (year or {}).get("id")
    ts = now_utc().isoformat()

    async def _upsert_item(entity_id: str, code: str, name: str, fee_type: str, amount: float,
                           frequency: str, applicable: dict) -> str:
        existing = await db.fee_catalogue.find_one({"entity_id": entity_id, "code": code})
        if existing:
            return existing["id"]
        doc = {
            "id": str(uuid.uuid4()),
            "entity_id": entity_id,
            "code": code,
            "name": name,
            "fee_type": fee_type,
            "legacy_fee_type": LEGACY_FEE_TYPE.get(fee_type),
            "amount": amount,
            "billing_frequency": frequency,
            "academic_year_id": year_id if entity_id == "pws" else None,
            "applicable": applicable,
            "active": True,
            "created_at": ts,
            "updated_at": ts,
            "created_by": "seed",
        }
        await db.fee_catalogue.insert_one(doc)
        return doc["id"]

    # PWS catalogue
    pws_items: dict[str, str] = {}
    for category, rates in PWS_RATE_CARDS.items():
        cat_key = category.lower().replace(" ", "_")
        pws_items[f"reg_{cat_key}"] = await _upsert_item(
            "pws", f"pws_reg_{cat_key}", f"PWS Registration ({category})",
            "registration", rates["registration"], "one_time",
            {"categories": [category], "grades": [], "sections": [], "sports": [], "centres": []},
        )
        pws_items[f"tuition_{cat_key}"] = await _upsert_item(
            "pws", f"pws_tuition_{cat_key}", f"PWS Tuition ({category})",
            "tuition", rates["monthly"], "monthly",
            {"categories": [category], "grades": [], "sections": [], "sports": [], "centres": []},
        )
        pws_items[f"exam_{cat_key}"] = await _upsert_item(
            "pws", f"pws_exam_{cat_key}", f"PWS Examination ({category})",
            "examination", rates.get("exam", 0), "term_wise",
            {"categories": [category], "grades": [], "sections": [], "sports": [], "centres": []},
        )
        if rates.get("hostel_monthly"):
            pws_items[f"hostel_{cat_key}"] = await _upsert_item(
                "pws", f"pws_hostel_{cat_key}", f"PWS Hostel ({category})",
                "hostel", rates["hostel_monthly"], "monthly",
                {"categories": [category], "grades": [], "sections": [], "sports": [], "centres": []},
            )

    for category in PWS_RATE_CARDS:
        plan_code = f"pws_plan_{category.lower().replace(' ', '_')}"
        if await db.fee_plans.find_one({"entity_id": "pws", "name": f"PWS {category}"}):
            continue
        cat_key = category.lower().replace(" ", "_")
        items = [
            {"catalogue_item_id": pws_items[f"reg_{cat_key}"]},
            {"catalogue_item_id": pws_items[f"tuition_{cat_key}"]},
            {"catalogue_item_id": pws_items[f"exam_{cat_key}"]},
        ]
        if f"hostel_{cat_key}" in pws_items:
            items.append({"catalogue_item_id": pws_items[f"hostel_{cat_key}"]})
        await db.fee_plans.insert_one({
            "id": str(uuid.uuid4()),
            "entity_id": "pws",
            "name": f"PWS {category}",
            "academic_year_id": year_id,
            "description": f"Default PWS fee plan for {category} students",
            "items": items,
            "match": {
                "kind": "student",
                "is_resident": category == "Hostel",
                "player_type": category,
            },
            "is_default": True,
            "active": True,
            "created_at": ts,
            "updated_at": ts,
            "created_by": "seed",
        })

    # ALPHA catalogue — per category × sport
    alpha_plan_items: dict[str, list] = {}
    for category, sports in RATE_CARDS.items():
        for sport, rates in sports.items():
            cat_slug = category.lower().replace(" ", "_")
            sport_slug = sport.lower()
            reg_id = await _upsert_item(
                "alpha", f"alpha_reg_{cat_slug}_{sport_slug}",
                f"ALPHA Registration ({category} · {sport})",
                "registration", rates["registration"], "one_time",
                {"categories": [category], "sports": [sport], "centres": [], "grades": [], "section_ids": []},
            )
            coach_id = await _upsert_item(
                "alpha", f"alpha_coaching_{cat_slug}_{sport_slug}",
                f"ALPHA Coaching ({category} · {sport})",
                "coaching", rates["monthly"], "monthly",
                {"categories": [category], "sports": [sport], "centres": [], "grades": [], "section_ids": []},
            )
            key = f"{category}|{sport}"
            alpha_plan_items[key] = [
                {"catalogue_item_id": reg_id},
                {"catalogue_item_id": coach_id},
            ]

    for key, items in alpha_plan_items.items():
        category, sport = key.split("|")
        plan_name = f"ALPHA {category} — {sport}"
        if await db.fee_plans.find_one({"entity_id": "alpha", "name": plan_name}):
            continue
        await db.fee_plans.insert_one({
            "id": str(uuid.uuid4()),
            "entity_id": "alpha",
            "name": plan_name,
            "academic_year_id": None,
            "description": f"Default ALPHA plan for {category} {sport} players",
            "items": items,
            "match": {"kind": "player", "player_type": category, "sport": sport},
            "is_default": True,
            "active": True,
            "created_at": ts,
            "updated_at": ts,
            "created_by": "seed",
        })

    # Ad-hoc catalogue heads (inactive until admin enables)
    for entity, code, name, fee_type in [
        ("pws", "pws_uniform", "School Uniform", "uniform"),
        ("pws", "pws_kit", "Sports Kit", "kit"),
        ("alpha", "alpha_kit", "Sports Kit", "kit"),
        ("alpha", "alpha_tournament", "Tournament Fee", "tournament"),
    ]:
        await _upsert_item(entity, code, name, fee_type, 0, "one_time", {})

