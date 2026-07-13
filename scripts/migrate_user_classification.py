#!/usr/bin/env python3
"""Migrate legacy login roles to canonical user_type classification.

Usage:
  python3 scripts/migrate_user_classification.py [--dry-run]

Deterministic mapping:
  super_admin -> super_admin
  admin -> alpha_admin
  principal/vice_principal -> pws_admin (+ designation)
  teacher -> pws_teacher
  coach -> alpha_coach
  pws_accounts / alpha_accounts -> unchanged user_type

Unmapped roles (parent, student, player, staff, warden) -> requires_user_type_review=True
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import db  # noqa: E402
from user_classification import (  # noqa: E402
    apply_user_type_fields,
    migrate_legacy_role,
    resolve_user_type,
)


async def migrate(*, dry_run: bool) -> dict:
    users = await db.users.find({}, {"_id": 0}).to_list(10000)
    stats = {"migrated": 0, "review": 0, "skipped": 0, "exceptions": []}

    for user in users:
        role = user.get("role") or ""
        if user.get("user_type") and resolve_user_type(user) == user.get("user_type"):
            stats["skipped"] += 1
            continue

        user_type, designation, needs_review = migrate_legacy_role(role)
        upd: dict = {}

        if needs_review:
            upd = {
                "requires_user_type_review": True,
                "legacy_role": role,
            }
            stats["review"] += 1
            stats["exceptions"].append({
                "id": user.get("id"),
                "email": user.get("email"),
                "role": role,
                "reason": "No approved user type mapping",
            })
            print(f"REVIEW: {user.get('email')} role={role}")
        elif user_type:
            doc = {**user}
            apply_user_type_fields(doc, user_type=user_type, designation=designation)
            upd = {
                "user_type": doc["user_type"],
                "organization": doc["organization"],
                "entity_scope": doc["entity_scope"],
                "role": doc["role"],
                "requires_user_type_review": False,
                "legacy_role": role,
            }
            if designation:
                upd["designation"] = doc.get("designation")
            stats["migrated"] += 1
            print(f"MIGRATED: {user.get('email')} {role} -> {user_type}")
        else:
            stats["skipped"] += 1

        if upd and not dry_run:
            await db.users.update_one({"id": user["id"]}, {"$set": upd})

    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    stats = asyncio.run(migrate(dry_run=args.dry_run))
    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(f"\n{mode}: {stats}")


if __name__ == "__main__":
    main()
