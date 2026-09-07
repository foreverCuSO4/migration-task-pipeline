#!/usr/bin/env python3
"""Render a standalone HTML chart for one layered filtering run."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from migration_task_pipeline.layer_filter_visualization import collect_visualization_data, render_html


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, help="Run root, e.g. runs/<run>.")
    parser.add_argument(
        "--output",
        default=None,
        help="Output HTML path. Defaults to <run-root>/layer-filter-visualization.html.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_root = Path(args.run_root)
    output = Path(args.output) if args.output else run_root / "layer-filter-visualization.html"
    data = collect_visualization_data(run_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(data), encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
