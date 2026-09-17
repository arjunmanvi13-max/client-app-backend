#!/usr/bin/env python3
"""Normalize legacy Class/Std strings on people and enquiries.

Usage:
  python3 scripts/normalize_pws_class.py [--dry-run]

Rewrites people.pws_class and PWS enquiry applying_for values such as
"Std 1", "1", "Class-10" to the canonical catalog (Nursery, LKG, UKG, Class I–X).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import db  # noqa: E402
from pws_class_catalog import normalize_class_value  # noqa: E402


async def _normalize_collection(coll, field: str, extra: dict, *, dry_run: bool) -> dict:
    stats = {"checked": 0, "updated": 0, "skipped": 0}
    cursor = coll.find({**extra, field: {"$exists": True, "$nin": [None, ""]}}, {"_id": 1, field: 1})
    async for doc in cursor:
        stats["checked"] += 1
        raw = doc.get(field)
        if not isinstance(raw, str):
            stats["skipped"] += 1
            continue
        canon = normalize_class_value(raw)
        if not canon or canon == raw:
            stats["skipped"] += 1
            continue
        stats["updated"] += 1
        if not dry_run:
            await coll.update_one({"_id": doc["_id"]}, {"$set": {field: canon}})
    return stats


async def migrate(*, dry_run: bool) -> None:
    people = await _normalize_collection(
        db.people, "pws_class", {"kind": {"$in": ["student", "player"]}}, dry_run=dry_run
    )
    enquiries = await _normalize_collection(
        db.enquiries, "applying_for", {"institution": "PWS"}, dry_run=dry_run
    )
    print(f"people.pws_class checked={people['checked']} updated={people['updated']} skipped={people['skipped']}")
    print(
        f"enquiries.applying_for checked={enquiries['checked']} "
        f"updated={enquiries['updated']} skipped={enquiries['skipped']}"
    )
    if dry_run:
        print("dry-run: no documents written")


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize PWS class strings")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(migrate(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
