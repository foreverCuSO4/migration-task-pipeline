#!/usr/bin/env python3
"""Run the repository seed collector v0 pipeline."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from migration_task_pipeline.config import load_seed_config
from migration_task_pipeline.pipeline import run_seed_collector_v0

DEFAULT_RUN_NAME = "seed-collector-v0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="configs/seed-sources.example.yaml",
        help="Path to seed-source YAML config.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help=(
            "Output root containing raw/interim/processed subdirectories. "
            "If omitted, a timestamped run directory is created under --runs-root."
        ),
    )
    parser.add_argument(
        "--runs-root",
        default="runs",
        help="Root directory for timestamped run folders when --output-root is omitted.",
    )
    parser.add_argument(
        "--run-name",
        default=DEFAULT_RUN_NAME,
        help="Human-readable suffix for timestamped run folders.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="YYYYMMDD date stamp for raw and interim artifacts.",
    )
    parser.add_argument(
        "--auth-file",
        default="auth.json",
        help="JSON file containing github_api_key; GITHUB_TOKEN still takes precedence.",
    )
    return parser.parse_args()


def resolve_output_root(args: argparse.Namespace) -> Path:
    if args.output_root:
        return Path(args.output_root)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_name = slugify_run_name(args.run_name)
    return Path(args.runs_root) / f"{timestamp}-{run_name}" / "data"


def slugify_run_name(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    slug = "-".join(part for part in slug.split("-") if part)
    return slug or DEFAULT_RUN_NAME


def main() -> int:
    args = parse_args()
    output_root = resolve_output_root(args)
    try:
        config = load_seed_config(args.config)
        outputs = run_seed_collector_v0(
            config,
            output_root=output_root,
            run_date=args.date,
            auth_path=args.auth_file,
        )
    except Exception as exc:
        print(f"seed collector failed: {exc}", file=sys.stderr)
        return 1

    print(f"raw candidates: {outputs.raw_candidate_count}")
    print(f"normalized repos: {outputs.normalized_count}")
    print(f"processed repos: {outputs.processed_count}")
    print(f"output root: {output_root}")
    print(f"processed csv: {outputs.processed_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
