"""Migration: split <think> tags from text parts into proper thinking parts.

Old behavior: When the parser didn't know the provider (before provider
adapter fix), MiniMax's <think> tags stayed inside the text part as plain
text. This migration walks every session's messages, finds text parts
containing <think> tags, and splits them into:

    {type: thinking, text: "..."} + {type: text, text: "..."}

Run with --dry-run to preview changes without modifying the database.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path


THINK_PATTERN = re.compile(r"<think>([\s\S]*?)<\/think>")


def migrate_text_part(text: str) -> list[dict] | None:
    """If text contains <think> tags, split into thinking + text parts.

    Returns None if no tags found (no change needed).
    Returns list of new parts otherwise.
    """
    matches = list(THINK_PATTERN.finditer(text))
    if not matches:
        return None

    new_parts: list[dict] = []
    last_end = 0

    for match in matches:
        # Add text before the tag (if any)
        before = text[last_end : match.start()].strip()
        if before:
            new_parts.append({"type": "text", "text": before})

        # Add the thinking part
        thinking_text = match.group(1).strip()
        if thinking_text:
            new_parts.append({"type": "thinking", "text": thinking_text, "collapsed": True})

        last_end = match.end()

    # Add remaining text after the last tag
    remaining = text[last_end:].strip()
    if remaining:
        new_parts.append({"type": "text", "text": remaining})

    return new_parts


def migrate_message_parts(parts: list[dict]) -> tuple[list[dict], int]:
    """Walk through parts, splitting text parts with <think> tags.

    Returns (new_parts, num_splits).
    """
    new_parts: list[dict] = []
    num_splits = 0

    for part in parts:
        if part.get("type") != "text":
            new_parts.append(part)
            continue

        text = part.get("text", "")
        split = migrate_text_part(text)
        if split is None:
            new_parts.append(part)
        else:
            new_parts.extend(split)
            num_splits += 1

    return new_parts, num_splits


def run_migration(
    db_path: str,
    dry_run: bool = True,
    session_id: str | None = None,
) -> dict:
    """Run the migration on the given database.

    Args:
        db_path: Path to the user database.
        dry_run: If True, only print what would change; don't modify.
        session_id: If given, only migrate this session.

    Returns:
        Stats dict with counts.
    """
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    stats = {
        "messages_scanned": 0,
        "messages_modified": 0,
        "parts_split": 0,
        "thinking_parts_created": 0,
    }

    # Build query
    if session_id:
        query = "SELECT id, parts_json FROM messages WHERE session_id = ? AND role = 'assistant'"
        params = (session_id,)
    else:
        query = "SELECT id, parts_json FROM messages WHERE role = 'assistant'"
        params = ()

    rows = db.execute(query, params).fetchall()

    for row in rows:
        msg_id = row["id"]
        parts_json = row["parts_json"]

        if not parts_json:
            continue
        stats["messages_scanned"] += 1

        try:
            parts = json.loads(parts_json)
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(parts, list):
            continue

        # Skip messages that already have thinking parts (no work to do)
        has_existing_thinking = any(p.get("type") == "thinking" for p in parts)
        if has_existing_thinking:
            continue

        new_parts, num_splits = migrate_message_parts(parts)

        if num_splits > 0:
            thinking_count = sum(1 for p in new_parts if p.get("type") == "thinking")
            stats["messages_modified"] += 1
            stats["parts_split"] += num_splits
            stats["thinking_parts_created"] += thinking_count

            print(
                f"  [{msg_id[:12]}] splits: {num_splits}, "
                f"thinking_parts: {thinking_count}"
            )

            if not dry_run:
                new_parts_json = json.dumps(new_parts, ensure_ascii=False)
                db.execute(
                    "UPDATE messages SET parts_json = ? WHERE id = ?",
                    (new_parts_json, msg_id),
                )

    if not dry_run:
        db.commit()
        print(f"\n✓ Migration committed")
    else:
        print(f"\n[DRY RUN] Use --no-dry-run to apply changes")

    db.close()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate old sessions: split <think> tags into proper thinking parts"
    )
    parser.add_argument(
        "db_path",
        nargs="?",
        default="/home/ll/Public/qn-research/quantnodes_strategy_research_user.db",
        help="Path to user database",
    )
    parser.add_argument(
        "--session",
        help="Migrate only this session ID",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview changes without modifying (default)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Apply changes to the database",
    )
    args = parser.parse_args()

    if not Path(args.db_path).exists():
        print(f"Error: database not found: {args.db_path}")
        return 1

    dry_run = not args.no_dry_run
    print(f"Migrating: {args.db_path}")
    if args.session:
        print(f"  Session: {args.session}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'APPLY CHANGES'}")
    print()

    stats = run_migration(args.db_path, dry_run=dry_run, session_id=args.session)

    print()
    print("=" * 40)
    print("Summary:")
    for key, value in stats.items():
        print(f"  {key}: {value}")

    return 0


if __name__ == "__main__":
    sys.exit(main())