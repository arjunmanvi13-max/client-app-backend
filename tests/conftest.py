"""Shared test lifecycle: purge TEST_-prefixed fixtures the API cannot delete."""
import asyncio
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

TEST_NAME_RE = "^(TEST[_ ]|QA )"
TEST_EMAIL_RE = "^(test_|testteacher|TEST_)"
TEST_USER_NAME_RE = "^(TEST |TEST_|Perm Sync Test|Directory Teacher Test)"


async def _purge():
    from motor.motor_asyncio import AsyncIOMotorClient

    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not (mongo_url and db_name):
        return
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    people = await db.people.find({"name": {"$regex": TEST_NAME_RE}}, {"_id": 0, "id": 1}).to_list(5000)
    ids = [p["id"] for p in people]
    if ids:
        for coll, field in (
            ("fees", "player_id"),
            ("attendance", "person_id"),
            ("invoices", "person_id"),
            ("academic_marks", "person_id"),
            ("report_cards", "person_id"),
            ("player_assessments", "player_id"),
            ("approval_requests", "subject_id"),
        ):
            await db[coll].delete_many({field: {"$in": ids}})
        await db.people.delete_many({"id": {"$in": ids}})
    await db.users.delete_many({"email": {"$regex": TEST_EMAIL_RE, "$options": "i"}})
    await db.users.delete_many({"name": {"$regex": TEST_USER_NAME_RE}})
    await db.tasks.delete_many({"title": {"$regex": TEST_NAME_RE}})
    await db.fees.delete_many({
        "$or": [
            {"notes": {"$regex": "^concurrency probe "}},
            {"fee_type": "Other", "amount": 500, "period_month": "2026-12"},
        ]
    })
    await db.invoices.delete_many({"notes": {"$regex": "^regression "}})
    await db.academic_years.delete_many({"name": {"$regex": "^ZZ-test-"}})
    await db.exam_terms.delete_many({"name": {"$regex": "^Test Term"}})
    await db.fee_items.delete_many({"code": {"$regex": "^test_"}})
    await db.fee_plans.delete_many({"name": {"$regex": TEST_NAME_RE}})
    await db.sections.delete_many({"name": {"$regex": TEST_NAME_RE}})
    await db.subjects.delete_many({"name": {"$regex": TEST_NAME_RE}})
    client.close()


@pytest.fixture(scope="session", autouse=True)
def purge_test_fixtures():
    yield
    asyncio.new_event_loop().run_until_complete(_purge())
