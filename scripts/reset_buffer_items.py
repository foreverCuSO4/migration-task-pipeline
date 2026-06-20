#!/usr/bin/env python3
"""Inspect and reset pipeline buffer items back to pending."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from migration_task_pipeline.buffer_admin import (
    BufferItemSelector,
    counts_by_status,
    counts_by_status_and_decision,
    reset_buffer_items,
    resolve_buffer_path,
    select_buffer_items,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", default=None, help="Run root, e.g. runs/<run>.")
    parser.add_argument(
        "--buffer",
        default="c2_to_d",
        choices=["b_to_c", "c1_to_c2", "c2_to_d"],
        help="Known buffer under --run-root.",
    )
    parser.add_argument("--buffer-path", default=None, help="Explicit SQLite buffer path.")
    parser.add_argument(
        "--status",
        action="append",
        default=None,
        help="Status to reset. Repeatable or comma-separated. Defaults to done; use all for every status.",
    )
    parser.add_argument("--repo-key", action="append", default=None, help="Exact repo_key. Repeatable.")
    parser.add_argument("--repo-contains", default="", help="Case-insensitive repo_key substring.")
    parser.add_argument("--item-id", action="append", default=None, help="Exact item_id. Repeatable.")
    parser.add_argument("--decision", action="append", default=None, help="Decision filter, e.g. promote/maybe.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum matching items to reset.")
    parser.add_argument("--keep-attempts", action="store_true", help="Do not reset attempts to 0.")
    parser.add_argument("--last-error", default="reset_by_buffer_admin", help="last_error text after reset.")
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation.")
    parser.add_argument("--dry-run", action="store_true", help="Preview matches without changing the buffer.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        buffer_path = resolve_buffer_path(
            run_root=args.run_root,
            buffer_name=args.buffer,
            buffer_path=args.buffer_path,
        )
        print(f"buffer: {buffer_path}")
        print_counts(buffer_path)
        selector = build_selector(args)
        if not args.yes:
            selector = interactive_selector(selector)
        matches = select_buffer_items(buffer_path, selector)
        print_preview(matches)
        if not matches:
            print("No matching items.")
            return 0
        if args.dry_run:
            print("Dry run only. No changes made.")
            return 0
        if not args.yes and not confirm_reset(len(matches)):
            print("Cancelled. No changes made.")
            return 1
        updated = reset_buffer_items(
            buffer_path,
            [str(item["item_id"]) for item in matches],
            reset_attempts=not args.keep_attempts,
            last_error=args.last_error,
        )
    except Exception as exc:
        print(f"Buffer reset failed: {exc}", file=sys.stderr)
        return 1

    print(f"reset items: {updated}")
    print_counts(buffer_path)
    return 0


def build_selector(args: argparse.Namespace) -> BufferItemSelector:
    statuses = parse_statuses(split_values(args.status or []))
    return BufferItemSelector(
        statuses=statuses,
        repo_keys=set(split_values(args.repo_key or [])),
        item_ids=set(split_values(args.item_id or [])),
        decisions=set(value.lower() for value in split_values(args.decision or [])),
        repo_contains=str(args.repo_contains or "").strip(),
        limit=args.limit,
    )


def interactive_selector(selector: BufferItemSelector) -> BufferItemSelector:
    print("")
    print("Interactive reset selection. Leave fields blank to keep current/default values.")
    statuses_default = ",".join(sorted(selector.statuses)) if selector.statuses else "done"
    statuses = parse_statuses(prompt_values(f"Statuses to reset [{statuses_default}]", default=statuses_default))
    repo_keys = prompt_values(
        "Exact repo_key values, comma-separated [current]",
        default=",".join(sorted(selector.repo_keys)),
    )
    repo_contains = input(f"repo_key substring [{selector.repo_contains}]: ").strip() or selector.repo_contains
    item_ids = prompt_values(
        "Exact item_id values, comma-separated [current]",
        default=",".join(sorted(selector.item_ids)),
    )
    decisions = {
        value.lower()
        for value in prompt_values(
            "Decision filter, comma-separated, e.g. promote/maybe [current]",
            default=",".join(sorted(selector.decisions)),
        )
    }
    limit_raw = input(f"Limit [{selector.limit or ''}]: ").strip()
    limit = int(limit_raw) if limit_raw else selector.limit
    return BufferItemSelector(
        statuses=statuses,
        repo_keys=set(repo_keys),
        item_ids=set(item_ids),
        decisions=decisions,
        repo_contains=repo_contains,
        limit=limit,
    )


def confirm_reset(count: int) -> bool:
    expected = f"RESET {count}"
    print("")
    print(f"Type exactly '{expected}' to reset these items to pending.")
    return input("> ").strip() == expected


def print_counts(buffer_path: Path) -> None:
    print("status counts:")
    for status, count in sorted(counts_by_status(buffer_path).items()):
        print(f"  {status:12s} {count}")
    print("status/decision counts:")
    for row in counts_by_status_and_decision(buffer_path):
        decision = str(row["decision"] or "-")
        print(f"  {row['status']:12s} {decision:12s} {row['count']}")


def print_preview(items: list[dict[str, object]], *, max_rows: int = 20) -> None:
    print("")
    print(f"matching items: {len(items)}")
    for index, item in enumerate(items[:max_rows], start=1):
        print(
            f"{index:3d}. {str(item.get('status','')):11s} "
            f"attempts={item.get('attempts')} "
            f"priority={item.get('priority')} "
            f"repo={item.get('repo_key')} "
            f"item_id={item.get('item_id')}"
        )
    if len(items) > max_rows:
        print(f"  ... {len(items) - max_rows} more")


def prompt_values(label: str, *, default: str) -> list[str]:
    raw = input(f"{label}: ").strip()
    return split_values([raw or default])


def split_values(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        for part in str(value).split(","):
            cleaned = part.strip()
            if cleaned:
                result.append(cleaned)
    return result


def parse_statuses(values: list[str]) -> set[str]:
    normalized = {value.lower() for value in values}
    if "all" in normalized:
        return set()
    return normalized or {"done"}


if __name__ == "__main__":
    raise SystemExit(main())
