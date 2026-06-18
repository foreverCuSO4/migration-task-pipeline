import argparse
import re
from pathlib import Path

from migration_task_pipeline.layers.b_remote_code_search.pipeline import default_b2c_buffer_path
from scripts.check_github_tokens import collect_tokens, token_fingerprint
from scripts.collect_repo_seeds import resolve_output_root, slugify_run_name
from scripts.screen_repo_candidates_b import resolve_output_root as resolve_b_output_root


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


def test_b_output_root_infers_data_root_from_seed_csv():
    assert (
        resolve_b_output_root("runs/example/data/processed/repo-seeds-v0.csv", None)
        == Path("runs/example/data")
    )
    assert resolve_b_output_root("seeds.csv", "out") == Path("out")


def test_b2c_buffer_path_uses_run_root_when_output_root_is_run_data():
    assert default_b2c_buffer_path("runs/example/data") == Path("runs/example/buffers/b_to_c.sqlite")
    assert default_b2c_buffer_path("data") == Path("data/buffers/b_to_c.sqlite")


def test_check_github_tokens_collects_auth_file_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"github_tokens": [{"name": "file", "token": "file-token"}]}', encoding="utf-8")

    assert [token.token for token in collect_tokens(auth_file)] == ["file-token"]
    assert [token.token for token in collect_tokens(auth_file, include_env=True)] == [
        "env-token",
        "file-token",
    ]


def test_check_github_tokens_fingerprint_does_not_expose_token_value():
    fingerprint = token_fingerprint("ghp_super-secret-token")

    assert "ghp_" not in fingerprint
    assert "super-secret-token" not in fingerprint
    assert fingerprint.startswith("sha256:")
