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

from migration_task_pipeline.layers.b_remote_code_search.config import (
    LayerBConfig,
    RemoteCodeSearchConfig,
    load_layer_b_config,
)
from migration_task_pipeline.layers.b_remote_code_search.dashboard import TerminalDashboard
from migration_task_pipeline.layers.b_remote_code_search.pipeline import run_remote_code_screening


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="configs/layer-b.example.yaml",
        help="Path to Layer B YAML config.",
    )
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
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Resume from an existing repo-candidates-b.csv by skipping completed repo_key rows. "
            "Uses runtime.resume from config unless explicitly set."
        ),
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=None,
        help="GitHub code-search results to fetch per query. Overrides config.",
    )
    parser.add_argument(
        "--max-code-queries-per-repo",
        type=int,
        default=None,
        help="Maximum code-search queries to run per repository. Overrides config.",
    )
    tree_group = parser.add_mutually_exclusive_group()
    tree_group.add_argument(
        "--tree",
        dest="use_remote_tree",
        action="store_true",
        default=None,
        help="Fetch remote git trees before code search. Overrides config.",
    )
    tree_group.add_argument(
        "--no-tree",
        dest="use_remote_tree",
        action="store_false",
        help="Skip remote git tree fetches and rely only on code search. Overrides config.",
    )
    parser.add_argument(
        "--rate-limit-max-retries",
        type=int,
        default=None,
        help="Maximum retries for a rate-limited GitHub request. Overrides config.",
    )
    parser.add_argument(
        "--rate-limit-retry-sleep",
        type=float,
        default=None,
        help="Fallback sleep seconds between rate-limit retries when GitHub does not provide a reset time.",
    )
    parser.add_argument(
        "--rate-limit-max-sleep",
        type=float,
        default=None,
        help="Maximum sleep seconds for one rate-limit retry. Overrides config.",
    )
    dashboard_group = parser.add_mutually_exclusive_group()
    dashboard_group.add_argument(
        "--dashboard",
        dest="dashboard",
        action="store_true",
        default=None,
        help="Force the live terminal dashboard even when stderr is not detected as a TTY.",
    )
    dashboard_group.add_argument(
        "--no-dashboard",
        dest="dashboard",
        action="store_false",
        help="Disable the live terminal dashboard.",
    )
    return parser.parse_args()


def resolve_output_root(seed_csv: str | Path, explicit_output_root: str | None) -> Path:
    if explicit_output_root:
        return Path(explicit_output_root)

    path = Path(seed_csv)
    if path.name == "repo-seeds-v0.csv" and path.parent.name == "processed":
        return path.parent.parent
    return Path("data")


def build_remote_code_search_config(
    base: RemoteCodeSearchConfig,
    args: argparse.Namespace,
) -> RemoteCodeSearchConfig:
    return RemoteCodeSearchConfig(
        code_queries=base.code_queries,
        per_page=args.per_page if args.per_page is not None else base.per_page,
        max_code_queries_per_repo=(
            args.max_code_queries_per_repo
            if args.max_code_queries_per_repo is not None
            else base.max_code_queries_per_repo
        ),
        use_remote_tree=args.use_remote_tree if args.use_remote_tree is not None else base.use_remote_tree,
        promote_threshold=base.promote_threshold,
        maybe_threshold=base.maybe_threshold,
        rate_limit_max_retries=(
            args.rate_limit_max_retries
            if args.rate_limit_max_retries is not None
            else base.rate_limit_max_retries
        ),
        rate_limit_retry_sleep_seconds=(
            args.rate_limit_retry_sleep
            if args.rate_limit_retry_sleep is not None
            else base.rate_limit_retry_sleep_seconds
        ),
        rate_limit_max_sleep_seconds=(
            args.rate_limit_max_sleep if args.rate_limit_max_sleep is not None else base.rate_limit_max_sleep_seconds
        ),
    )


def resolve_dashboard_enabled(config: LayerBConfig, args: argparse.Namespace, *, stderr_isatty: bool) -> bool:
    if args.dashboard is not None:
        return bool(args.dashboard)
    if config.runtime.dashboard == "on":
        return True
    if config.runtime.dashboard == "off":
        return False
    return stderr_isatty


def main() -> int:
    args = parse_args()
    output_root = resolve_output_root(args.seed_csv, args.output_root)
    layer_config = load_layer_b_config(args.config)
    config = build_remote_code_search_config(layer_config.remote_code_search, args)
    resume = args.resume if args.resume is not None else layer_config.runtime.resume
    dashboard = None
    if resolve_dashboard_enabled(layer_config, args, stderr_isatty=sys.stderr.isatty()):
        dashboard = TerminalDashboard()

    try:
        outputs = run_remote_code_screening(
            args.seed_csv,
            output_root=output_root,
            run_date=args.date,
            auth_path=args.auth_file,
            config=config,
            limit=args.limit,
            progress_callback=dashboard,
            resume=resume,
        )
    except Exception as exc:
        if dashboard is not None:
            dashboard({"event": "error", "elapsed_sec": 0, "error": str(exc)})
            dashboard.close()
        print(f"Layer B screening failed: {exc}", file=sys.stderr)
        return 1

    if dashboard is not None:
        dashboard.close()
    print(f"scanned repos: {outputs.scanned_count}")
    print(f"resumed repos: {outputs.resumed_count}")
    print(f"promote: {outputs.promoted_count}")
    print(f"maybe: {outputs.maybe_count}")
    print(f"reject: {outputs.rejected_count}")
    print(f"signals jsonl: {outputs.signals_jsonl}")
    print(f"candidates csv: {outputs.candidates_csv}")
    print(f"log file: {outputs.log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
