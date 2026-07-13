#!/usr/bin/env python3
"""Backfill strict single-sport assignment for existing coach accounts.

Usage:
  python scripts/migrate_coach_single_sport.py [--dry-run]

Strategy:
  - Exactly one sport in assigned_sports / assigned_sport → migrate automatically.
  - Zero sports → sport_assignment_status = required (no player data access).
  - Multiple sports → sport_assignment_status = ambiguous, clear assignments until admin fixes.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import db  # noqa: E402
from coach_scope import normalize_coach_assignments, coach_assignment_lists  # noqa: E402


async def migrate(*, dry_run: bool) -> dict:
    coaches = await db.users.find({"role": "coach"}, {"_id": 0}).to_list(5000)
    stats = {"ok": 0, "required": 0, "ambiguous": 0, "skipped": 0}

    for coach in coaches:
        _, sports = coach_assignment_lists(coach)
        upd: dict = {}

        if len(sports) == 1:
            doc = {**coach, "assigned_sports": sports, "assigned_sport": sports[0]}
            normalize_coach_assignments(doc)
            upd = {
                "assigned_sport": doc["assigned_sport"],
                "assigned_sports": doc["assigned_sports"],
                "sport_assignment_status": "ok",
            }
            stats["ok"] += 1
        elif len(sports) > 1:
            upd = {
                "assigned_sport": None,
                "assigned_sports": [],
                "sport_assignment_status": "ambiguous",
            }
            stats["ambiguous"] += 1
            print(f"AMBIGUOUS: {coach.get('email')} had {sports} — requires Super Admin assignment")
        else:
            upd = {
                "sport_assignment_status": "required",
            }
            if not coach.get("assigned_sport"):
                upd["assigned_sports"] = []
            stats["required"] += 1
            print(f"REQUIRED: {coach.get('email')} has no sport assignment")

        if upd and not dry_run:
            await db.users.update_one({"id": coach["id"]}, {"$set": upd})

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate coach accounts to single-sport assignment")
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not write")
    args = parser.parse_args()
    stats = asyncio.run(migrate(dry_run=args.dry_run))
    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(f"\n{mode}: {stats}")


if __name__ == "__main__":
    main()
