import argparse

from migration_task_pipeline.layers.b_remote_code_search.config import load_layer_b_config
from scripts.screen_repo_candidates_b import (
    build_remote_code_search_config,
    resolve_b2c_buffer_enabled,
    resolve_dashboard_enabled,
)


def test_load_layer_b_config_reads_remote_search_and_runtime(tmp_path):
    config_path = tmp_path / "layer-b.yaml"
    config_path.write_text(
        """
remote_code_search:
  per_page: 3
  max_code_queries_per_repo: 7
  use_remote_tree: false
  promote_threshold: 0.7
  maybe_threshold: 0.4
  rate_limit:
    max_retries: 5
    retry_sleep_seconds: 12.5
    max_sleep_seconds: 90
  transient_error:
    max_retries: 4
    retry_sleep_seconds: 2.5
    max_sleep_seconds: 30
  code_queries:
    - group: cuda
      term: torch.cuda
    - group: interface
      term: console_scripts
runtime:
  resume: false
  dashboard: off
  b2c_buffer: false
""",
        encoding="utf-8",
    )

    config = load_layer_b_config(config_path)

    assert config.remote_code_search.per_page == 3
    assert config.remote_code_search.max_code_queries_per_repo == 7
    assert config.remote_code_search.use_remote_tree is False
    assert config.remote_code_search.promote_threshold == 0.7
    assert config.remote_code_search.maybe_threshold == 0.4
    assert config.remote_code_search.rate_limit_max_retries == 5
    assert config.remote_code_search.rate_limit_retry_sleep_seconds == 12.5
    assert config.remote_code_search.rate_limit_max_sleep_seconds == 90
    assert config.remote_code_search.transient_error_max_retries == 4
    assert config.remote_code_search.transient_error_retry_sleep_seconds == 2.5
    assert config.remote_code_search.transient_error_max_sleep_seconds == 30
    assert [query.term for query in config.remote_code_search.code_queries] == [
        "torch.cuda",
        "console_scripts",
    ]
    assert config.runtime.resume is False
    assert config.runtime.dashboard == "off"
    assert config.runtime.b2c_buffer is False


def test_cli_args_override_layer_b_config(tmp_path):
    config_path = tmp_path / "layer-b.yaml"
    config_path.write_text(
        """
remote_code_search:
  per_page: 3
  max_code_queries_per_repo: 7
  use_remote_tree: false
  rate_limit:
    max_retries: 5
    retry_sleep_seconds: 12.5
    max_sleep_seconds: 90
  transient_error:
    max_retries: 4
    retry_sleep_seconds: 2.5
    max_sleep_seconds: 30
runtime:
  dashboard: off
""",
        encoding="utf-8",
    )
    layer_config = load_layer_b_config(config_path)
    args = argparse.Namespace(
        per_page=9,
        max_code_queries_per_repo=None,
        use_remote_tree=True,
        rate_limit_max_retries=None,
        rate_limit_retry_sleep=1.5,
        rate_limit_max_sleep=None,
        transient_error_max_retries=8,
        transient_error_retry_sleep=None,
        transient_error_max_sleep=45,
        dashboard=True,
        b2c_buffer=False,
    )

    remote_config = build_remote_code_search_config(layer_config.remote_code_search, args)

    assert remote_config.per_page == 9
    assert remote_config.max_code_queries_per_repo == 7
    assert remote_config.use_remote_tree is True
    assert remote_config.rate_limit_max_retries == 5
    assert remote_config.rate_limit_retry_sleep_seconds == 1.5
    assert remote_config.rate_limit_max_sleep_seconds == 90
    assert remote_config.transient_error_max_retries == 8
    assert remote_config.transient_error_retry_sleep_seconds == 2.5
    assert remote_config.transient_error_max_sleep_seconds == 45
    assert resolve_dashboard_enabled(layer_config, args, stderr_isatty=False) is True
    assert resolve_b2c_buffer_enabled(layer_config, args) is False
