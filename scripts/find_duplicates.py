"""Report rows that block the unique indexes in seed._ensure_unique_indexes().

Read-only. Run before a deploy that adds those indexes:

    python scripts/find_duplicates.py           # report only
    python scripts/find_duplicates.py --fix     # keep newest per group, delete the rest
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db  # noqa: E402

GROUPS = [
    ("attendance", ["kind", "person_id", "date", "session"], {}),
    ("fees", ["player_id", "fee_type", "period_month"], {"period_month": {"$type": "string"}}),
    ("academic_marks", ["person_id", "assessment_id"], {}),
    ("roll_calls", ["date", "session", "resident_id"], {}),
]


async def duplicates(coll: str, keys: list, match: dict) -> list:
    pipeline = []
    if match:
        pipeline.append({"$match": match})
    pipeline += [
        {"$group": {
            "_id": {k: f"${k}" for k in keys},
            "n": {"$sum": 1},
            "ids": {"$push": "$_id"},
        }},
        {"$match": {"n": {"$gt": 1}}},
        {"$limit": 1000},
    ]
    return await db[coll].aggregate(pipeline).to_list(1000)


async def main(fix: bool) -> int:
    total = 0
    for coll, keys, match in GROUPS:
        rows = await duplicates(coll, keys, match)
        extra = sum(r["n"] - 1 for r in rows)
        total += extra
        status = "OK" if not rows else f"{len(rows)} duplicated key(s), {extra} extra row(s)"
        print(f"{coll:16} {'+'.join(keys):48} {status}")
        for r in rows[:5]:
            print(f"    {r['_id']}  x{r['n']}")
        if rows and len(rows) > 5:
            print(f"    ... and {len(rows) - 5} more")
        if fix and rows:
            doomed = [oid for r in rows for oid in sorted(r["ids"])[:-1]]
            res = await db[coll].delete_many({"_id": {"$in": doomed}})
            print(f"    deleted {res.deleted_count} duplicate row(s)")
    print()
    print("No duplicates — unique indexes can be created." if total == 0
          else f"{total} extra row(s) block the unique indexes. Re-run with --fix to remove them.")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="delete all but the newest row per duplicate group")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.fix)))
