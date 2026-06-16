import argparse
import re
from pathlib import Path

from scripts.collect_repo_seeds import resolve_output_root, slugify_run_name


def test_resolve_output_root_preserves_explicit_output_root():
    args = argparse.Namespace(
        output_root="data",
        runs_root="runs",
        run_name="Seed Collector v0",
    )

    assert resolve_output_root(args) == Path("data")


def test_resolve_output_root_uses_timestamped_run_directory():
    args = argparse.Namespace(
        output_root=None,
        runs_root="runs",
        run_name="GitHub Search Smoke",
    )

    output_root = resolve_output_root(args)

    assert output_root.parent.parent == Path("runs")
    assert output_root.name == "data"
    assert re.match(r"\d{8}-\d{6}-github-search-smoke", output_root.parent.name)


def test_slugify_run_name():
    assert slugify_run_name("GitHub Search Smoke") == "github-search-smoke"
    assert slugify_run_name("  ") == "seed-collector-v0"

