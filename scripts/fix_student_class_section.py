#!/usr/bin/env python3
"""Repair student class/section mappings against Academic Structure.

Usage:
  python scripts/fix_student_class_section.py [--dry-run]

Fixes students where section_id or group label does not match pws_class.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import db  # noqa: E402
from student_academic import (  # noqa: E402
    grade_matches_pws_class,
    section_letter_from_label,
    sync_student_academic_fields,
)


async def migrate(*, dry_run: bool) -> dict:
    students = await db.people.find({"kind": "student"}, {"_id": 0}).to_list(10000)
    stats = {"checked": 0, "fixed": 0, "skipped": 0}

    for student in students:
        stats["checked"] += 1
        sid = student.get("section_id")
        section = await db.sections.find_one({"id": sid}, {"_id": 0}) if sid else None
        mismatch = False
        if section and student.get("pws_class") and not grade_matches_pws_class(section.get("grade_name"), student.get("pws_class")):
            mismatch = True
        elif student.get("group") and student.get("pws_class"):
            letter = section_letter_from_label(student.get("group"))
            if letter and section and section_letter_from_label(section.get("label")) != letter:
                mismatch = True

        if not mismatch and student.get("section_id") and student.get("group"):
            stats["skipped"] += 1
            continue

        synced = await sync_student_academic_fields(student, fix_mismatch=True)
        patch = {
            "section_id": synced.get("section_id"),
            "group": synced.get("group"),
        }
        if patch == {"section_id": student.get("section_id"), "group": student.get("group")}:
            stats["skipped"] += 1
            continue

        stats["fixed"] += 1
        print(
            f"FIX: {student.get('name')} | class={student.get('pws_class')} "
            f"section_id {student.get('section_id')} -> {patch.get('section_id')} | "
            f"group {student.get('group')} -> {patch.get('group')}"
        )
        if not dry_run:
            await db.people.update_one({"id": student["id"]}, {"$set": patch})

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix student class/section mappings")
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not write")
    args = parser.parse_args()
    stats = asyncio.run(migrate(dry_run=args.dry_run))
    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(f"\n{mode}: {stats}")


if __name__ == "__main__":
    main()
