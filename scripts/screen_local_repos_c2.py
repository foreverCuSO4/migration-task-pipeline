#!/usr/bin/env python3
"""Run Stage C2 local heuristic screening."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from migration_task_pipeline.layers.c2_local_heuristic_screening.config import (
    C2RuntimeConfig,
    LayerC2Config,
    LocalScannerConfig,
    load_layer_c2_config,
)
from migration_task_pipeline.layers.c2_local_heuristic_screening.dashboard import TerminalDashboard
from migration_task_pipeline.layers.c2_local_heuristic_screening.pipeline import run_c2_local_screening


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, help="Run root, e.g. runs/<run>.")
    parser.add_argument("--config", default="configs/layer-c2.example.yaml", help="Path to Layer C2 YAML config.")
    parser.add_argument("--input-buffer", default=None, help="Override C1-to-C2 input SQLite buffer path.")
    parser.add_argument("--output-buffer", default=None, help="Override C2-to-D output SQLite buffer path.")
    parser.add_argument("--evidence-jsonl", default=None, help="Override C2 evidence JSONL path.")
    parser.add_argument("--candidates-csv", default=None, help="Override C2 candidates CSV path.")
    parser.add_argument("--log-file", default=None, help="Override C2 JSONL log path.")
    parser.add_argument("--concurrency", type=int, default=None, help="Worker count. Overrides config.")
    parser.add_argument("--max-items", type=int, default=None, help="Maximum claimed items for this run.")
    parser.add_argument("--lease-seconds", type=int, default=None, help="Input buffer lease seconds.")
    parser.add_argument("--max-file-size-bytes", type=int, default=None, help="Maximum text file bytes to scan.")
    parser.add_argument("--max-files-per-repo", type=int, default=None, help="Maximum files to inspect per repo.")
    parser.add_argument("--max-repo-bytes", type=int, default=None, help="Maximum bytes to inspect per repo.")
    parser.add_argument("--max-hits-per-repo", type=int, default=None, help="Maximum evidence hits per repo.")
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


def build_scanner_config(base: LocalScannerConfig, args: argparse.Namespace) -> LocalScannerConfig:
    return LocalScannerConfig(
        max_file_size_bytes=(
            max(1, args.max_file_size_bytes)
            if args.max_file_size_bytes is not None
            else base.max_file_size_bytes
        ),
        max_files_per_repo=(
            max(1, args.max_files_per_repo)
            if args.max_files_per_repo is not None
            else base.max_files_per_repo
        ),
        max_repo_bytes=max(1, args.max_repo_bytes) if args.max_repo_bytes is not None else base.max_repo_bytes,
        max_hits_per_repo=(
            max(1, args.max_hits_per_repo) if args.max_hits_per_repo is not None else base.max_hits_per_repo
        ),
        max_paths_per_group=base.max_paths_per_group,
        skip_dirs=base.skip_dirs,
    )


def build_runtime_config(base: C2RuntimeConfig, args: argparse.Namespace) -> C2RuntimeConfig:
    return C2RuntimeConfig(
        concurrency=max(1, args.concurrency) if args.concurrency is not None else base.concurrency,
        max_items=args.max_items if args.max_items is not None else base.max_items,
        lease_seconds=max(1, args.lease_seconds) if args.lease_seconds is not None else base.lease_seconds,
        dashboard=base.dashboard,
    )


def build_layer_config(base: LayerC2Config, args: argparse.Namespace) -> LayerC2Config:
    return LayerC2Config(
        scanner=build_scanner_config(base.scanner, args),
        scoring=base.scoring,
        runtime=build_runtime_config(base.runtime, args),
    )


def resolve_dashboard_enabled(config: LayerC2Config, args: argparse.Namespace, *, stderr_isatty: bool) -> bool:
    if args.dashboard is not None:
        return bool(args.dashboard)
    if config.runtime.dashboard == "on":
        return True
    if config.runtime.dashboard == "off":
        return False
    return stderr_isatty


def main() -> int:
    args = parse_args()
    dashboard = None
    try:
        loaded_config = load_layer_c2_config(args.config)
        config = build_layer_config(loaded_config, args)
        if resolve_dashboard_enabled(config, args, stderr_isatty=sys.stderr.isatty()):
            dashboard = TerminalDashboard()
        outputs = run_c2_local_screening(
            run_root=args.run_root,
            config=config,
            input_buffer=args.input_buffer,
            output_buffer=args.output_buffer,
            evidence_jsonl=args.evidence_jsonl,
            candidates_csv=args.candidates_csv,
            log_file=args.log_file,
            max_items=args.max_items,
            concurrency=args.concurrency,
            progress_callback=dashboard,
        )
    except Exception as exc:
        if dashboard is not None:
            dashboard({"event": "error", "elapsed_sec": 0, "error": str(exc)})
            dashboard.close()
        print(f"Layer C2 local screening failed: {exc}", file=sys.stderr)
        return 1

    if dashboard is not None:
        dashboard.close()
    print(f"claimed repos: {outputs.claimed_count}")
    print(f"promote: {outputs.promoted_count}")
    print(f"maybe: {outputs.maybe_count}")
    print(f"reject: {outputs.rejected_count}")
    print(f"failed repos: {outputs.failed_count}")
    print(f"enqueued repos: {outputs.enqueued_count}")
    print(f"input buffer: {outputs.input_buffer}")
    print(f"output buffer: {outputs.output_buffer}")
    print(f"evidence jsonl: {outputs.evidence_jsonl}")
    print(f"candidates csv: {outputs.candidates_csv}")
    print(f"log file: {outputs.log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

