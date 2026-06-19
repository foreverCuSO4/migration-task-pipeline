from migration_task_pipeline.layers.c2_local_heuristic_screening.config import load_layer_c2_config
from scripts.screen_local_repos_c2 import build_layer_config, resolve_dashboard_enabled


def test_load_layer_c2_config_and_cli_overrides(tmp_path):
    config_path = tmp_path / "layer-c2.yaml"
    config_path.write_text(
        """
scanner:
  max_file_size_bytes: 100
  max_files_per_repo: 200
  max_repo_bytes: 300
  max_hits_per_repo: 20
  max_paths_per_group: 7
  skip_dirs:
    - .git
    - vendor
scoring:
  promote_threshold: 0.8
  maybe_threshold: 0.4
runtime:
  concurrency: 3
  max_items: 9
  lease_seconds: 45
  dashboard: off
""",
        encoding="utf-8",
    )

    config = load_layer_c2_config(config_path)

    assert config.scanner.max_file_size_bytes == 100
    assert config.scanner.max_files_per_repo == 200
    assert config.scanner.max_repo_bytes == 300
    assert config.scanner.max_hits_per_repo == 20
    assert config.scanner.max_paths_per_group == 7
    assert config.scanner.skip_dirs == [".git", "vendor"]
    assert config.scoring.promote_threshold == 0.8
    assert config.scoring.maybe_threshold == 0.4
    assert config.runtime.concurrency == 3
    assert config.runtime.max_items == 9
    assert config.runtime.lease_seconds == 45
    assert config.runtime.dashboard == "off"

    args = type(
        "Args",
        (),
        {
            "max_file_size_bytes": 101,
            "max_files_per_repo": None,
            "max_repo_bytes": 301,
            "max_hits_per_repo": None,
            "concurrency": 5,
            "max_items": None,
            "lease_seconds": 60,
            "dashboard": True,
        },
    )()
    overridden = build_layer_config(config, args)
    assert overridden.scanner.max_file_size_bytes == 101
    assert overridden.scanner.max_files_per_repo == 200
    assert overridden.scanner.max_repo_bytes == 301
    assert overridden.runtime.concurrency == 5
    assert overridden.runtime.lease_seconds == 60
    assert resolve_dashboard_enabled(overridden, args, stderr_isatty=False) is True

