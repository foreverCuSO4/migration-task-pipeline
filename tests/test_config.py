from migration_task_pipeline.config import load_seed_config


def test_example_config_keeps_github_search_keywords_in_sync_with_pypi():
    config = load_seed_config("configs/seed-sources.example.yaml")

    assert config.github_search.keywords == config.pypi.keywords

