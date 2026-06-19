#!/usr/bin/env python3
"""Run Stage C1 local repository materialization."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from migration_task_pipeline.layers.c1_local_materialization.config import (
    LayerC1Config,
    MaterializationConfig,
    load_layer_c1_config,
)
from migration_task_pipeline.layers.c1_local_materialization.dashboard import TerminalDashboard
from migration_task_pipeline.layers.c1_local_materialization.pipeline import run_c1_materialization


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, help="Run root, e.g. runs/<run>.")
    parser.add_argument("--config", default="configs/layer-c1.example.yaml", help="Path to Layer C1 YAML config.")
    parser.add_argument("--auth-file", default="auth.json", help="JSON file containing github_tokens for clone auth.")
    parser.add_argument("--input-buffer", default=None, help="Override B-to-C input SQLite buffer path.")
    parser.add_argument("--output-buffer", default=None, help="Override C1-to-C2 output SQLite buffer path.")
    parser.add_argument("--repo-root", default=None, help="Override local repository root path.")
    parser.add_argument("--registry", default=None, help="Override local repo registry SQLite path.")
    parser.add_argument("--log-file", default=None, help="Override C1 JSONL log path.")
    parser.add_argument("--concurrency", type=int, default=None, help="Worker count. Overrides config.")
    parser.add_argument("--max-items", type=int, default=None, help="Maximum claimed items for this run.")
    parser.add_argument("--clone-depth", type=int, default=None, help="Git clone depth. 1 is shallow; 0 is full.")
    parser.add_argument("--clone-timeout", type=int, default=None, help="Git clone timeout seconds.")
    parser.add_argument("--lease-seconds", type=int, default=None, help="Input buffer lease seconds.")
    parser.add_argument("--max-attempts", type=int, default=None, help="Maximum clone attempts per repo.")
    parser.add_argument("--retry-priority", type=int, default=None, help="Priority assigned to failed clone retries.")
    parser.add_argument("--submodules", action=argparse.BooleanOptionalAction, default=None, help="Clone submodules.")
    parser.add_argument("--lfs", action=argparse.BooleanOptionalAction, default=None, help="Allow Git LFS smudge.")
    parser.add_argument("--http-proxy", default=None, help="Override HTTP proxy for git clone.")
    parser.add_argument("--https-proxy", default=None, help="Override HTTPS proxy for git clone.")
    parser.add_argument("--all-proxy", default=None, help="Override ALL proxy for git clone.")
    parser.add_argument("--no-proxy", default=None, help="Override no_proxy/NO_PROXY for git clone.")
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


def build_materialization_config(base: MaterializationConfig, args: argparse.Namespace) -> MaterializationConfig:
    return MaterializationConfig(
        clone_depth=args.clone_depth if args.clone_depth is not None else base.clone_depth,
        clone_timeout_seconds=(
            args.clone_timeout if args.clone_timeout is not None else base.clone_timeout_seconds
        ),
        lease_seconds=args.lease_seconds if args.lease_seconds is not None else base.lease_seconds,
        max_attempts=max(1, args.max_attempts) if args.max_attempts is not None else base.max_attempts,
        retry_priority=args.retry_priority if args.retry_priority is not None else base.retry_priority,
        submodules=args.submodules if args.submodules is not None else base.submodules,
        lfs=args.lfs if args.lfs is not None else base.lfs,
        http_proxy=args.http_proxy if args.http_proxy is not None else base.http_proxy,
        https_proxy=args.https_proxy if args.https_proxy is not None else base.https_proxy,
        all_proxy=args.all_proxy if args.all_proxy is not None else base.all_proxy,
        no_proxy=args.no_proxy if args.no_proxy is not None else base.no_proxy,
    )


def build_layer_config(base: LayerC1Config, args: argparse.Namespace) -> LayerC1Config:
    return LayerC1Config(
        materialization=build_materialization_config(base.materialization, args),
        runtime=base.runtime,
    )


def resolve_dashboard_enabled(config: LayerC1Config, args: argparse.Namespace, *, stderr_isatty: bool) -> bool:
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
        loaded_config = load_layer_c1_config(args.config)
        config = build_layer_config(loaded_config, args)
        if resolve_dashboard_enabled(config, args, stderr_isatty=sys.stderr.isatty()):
            dashboard = TerminalDashboard()
        outputs = run_c1_materialization(
            run_root=args.run_root,
            config=config,
            input_buffer=args.input_buffer,
            output_buffer=args.output_buffer,
            repo_root=args.repo_root,
            registry_path=args.registry,
            log_file=args.log_file,
            max_items=args.max_items,
            concurrency=args.concurrency,
            auth_path=args.auth_file,
            progress_callback=dashboard,
        )
    except Exception as exc:
        if dashboard is not None:
            dashboard({"event": "error", "elapsed_sec": 0, "error": str(exc)})
            dashboard.close()
        print(f"Layer C1 materialization failed: {exc}", file=sys.stderr)
        return 1

    if dashboard is not None:
        dashboard.close()
    print(f"claimed repos: {outputs.claimed_count}")
    print(f"cloned repos: {outputs.cloned_count}")
    print(f"failed attempts: {outputs.failed_count}")
    print(f"terminal failed repos: {outputs.terminal_failed_count}")
    print(f"enqueued repos: {outputs.enqueued_count}")
    print(f"input buffer: {outputs.input_buffer}")
    print(f"output buffer: {outputs.output_buffer}")
    print(f"repo root: {outputs.repo_root}")
    print(f"registry: {outputs.registry}")
    print(f"log file: {outputs.log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
