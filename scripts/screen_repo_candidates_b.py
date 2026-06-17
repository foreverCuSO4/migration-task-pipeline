#!/usr/bin/env python3
"""Run Layer B remote GitHub code-search screening."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from migration_task_pipeline.layers.b_remote_code_search.config import RemoteCodeSearchConfig
from migration_task_pipeline.layers.b_remote_code_search.pipeline import run_remote_code_screening


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed-csv",
        required=True,
        help="Path to A-layer repo-seeds-v0.csv.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help=(
            "Output root containing interim/processed subdirectories. "
            "If omitted, inferred from <run>/data/processed/repo-seeds-v0.csv."
        ),
    )
    parser.add_argument(
        "--date",
        default=None,
        help="YYYYMMDD date stamp for interim artifacts.",
    )
    parser.add_argument(
        "--auth-file",
        default="auth.json",
        help=(
            "JSON file containing github_tokens or legacy github_api_key; "
            "GITHUB_TOKEN is merged first when present."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Scan only the first N seed rows; useful for smoke tests.",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=5,
        help="GitHub code-search results to fetch per query.",
    )
    parser.add_argument(
        "--max-code-queries-per-repo",
        type=int,
        default=24,
        help="Maximum code-search queries to run per repository.",
    )
    parser.add_argument(
        "--no-tree",
        action="store_true",
        help="Skip remote git tree fetches and rely only on code search.",
    )
    return parser.parse_args()


def resolve_output_root(seed_csv: str | Path, explicit_output_root: str | None) -> Path:
    if explicit_output_root:
        return Path(explicit_output_root)

    path = Path(seed_csv)
    if path.name == "repo-seeds-v0.csv" and path.parent.name == "processed":
        return path.parent.parent
    return Path("data")


def main() -> int:
    args = parse_args()
    output_root = resolve_output_root(args.seed_csv, args.output_root)
    config = RemoteCodeSearchConfig(
        per_page=args.per_page,
        max_code_queries_per_repo=args.max_code_queries_per_repo,
        use_remote_tree=not args.no_tree,
    )

    try:
        outputs = run_remote_code_screening(
            args.seed_csv,
            output_root=output_root,
            run_date=args.date,
            auth_path=args.auth_file,
            config=config,
            limit=args.limit,
        )
    except Exception as exc:
        print(f"Layer B screening failed: {exc}", file=sys.stderr)
        return 1

    print(f"scanned repos: {outputs.scanned_count}")
    print(f"promote: {outputs.promoted_count}")
    print(f"maybe: {outputs.maybe_count}")
    print(f"reject: {outputs.rejected_count}")
    print(f"signals jsonl: {outputs.signals_jsonl}")
    print(f"candidates csv: {outputs.candidates_csv}")
    print(f"log file: {outputs.log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
