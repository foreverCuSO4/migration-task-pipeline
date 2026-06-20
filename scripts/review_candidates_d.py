#!/usr/bin/env python3
"""Run Stage D OpenCode agent review."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from migration_task_pipeline.layers.d_agent_review.config import (
    DRuntimeConfig,
    LayerDConfig,
    load_layer_d_config,
)
from migration_task_pipeline.layers.d_agent_review.pipeline import run_d_agent_review


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, help="Run root, e.g. runs/<run>.")
    parser.add_argument("--config", default="configs/layer-d.example.yaml", help="Path to Layer D YAML config.")
    parser.add_argument("--auth-file", default="auth.json", help="JSON file containing OpenCode API keys.")
    parser.add_argument("--input-buffer", default=None, help="Override C2-to-D input SQLite buffer path.")
    parser.add_argument("--max-items", type=int, default=None, help="Maximum claimed items for this run.")
    parser.add_argument("--concurrency", type=int, default=None, help="Worker count. Overrides config.")
    parser.add_argument("--timeout-seconds", type=int, default=None, help="OpenCode subprocess timeout.")
    parser.add_argument("--lease-seconds", type=int, default=None, help="Input buffer lease seconds.")
    parser.add_argument("--max-attempts", type=int, default=None, help="Maximum review attempts before failed.")
    return parser.parse_args()


def build_runtime_config(base: DRuntimeConfig, args: argparse.Namespace) -> DRuntimeConfig:
    return DRuntimeConfig(
        concurrency=max(1, args.concurrency) if args.concurrency is not None else base.concurrency,
        max_items=args.max_items if args.max_items is not None else base.max_items,
        lease_seconds=max(1, args.lease_seconds) if args.lease_seconds is not None else base.lease_seconds,
        timeout_seconds=(
            max(1, args.timeout_seconds) if args.timeout_seconds is not None else base.timeout_seconds
        ),
        max_attempts=max(1, args.max_attempts) if args.max_attempts is not None else base.max_attempts,
    )


def build_layer_config(base: LayerDConfig, args: argparse.Namespace) -> LayerDConfig:
    return LayerDConfig(
        opencode=base.opencode,
        selection=base.selection,
        runtime=build_runtime_config(base.runtime, args),
        paths=base.paths,
    )


def main() -> int:
    args = parse_args()
    try:
        loaded_config = load_layer_d_config(args.config)
        config = build_layer_config(loaded_config, args)
        outputs = run_d_agent_review(
            run_root=args.run_root,
            config=config,
            auth_path=args.auth_file,
            input_buffer=args.input_buffer,
            max_items=args.max_items,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as exc:
        print(f"Layer D agent review failed: {exc}", file=sys.stderr)
        return 1

    print(f"claimed repos: {outputs.claimed_count}")
    print(f"reviewed repos: {outputs.reviewed_count}")
    print(f"failed repos: {outputs.failed_count}")
    print(f"skipped repos: {outputs.skipped_count}")
    print(f"input buffer: {outputs.input_buffer}")
    print(f"workspace root: {outputs.workspace_root}")
    print(f"logs dir: {outputs.logs_dir}")
    print(f"candidate cards dir: {outputs.candidate_cards_dir}")
    print(f"stage log file: {outputs.stage_log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
