from migration_task_pipeline.collectors.github_search import (
    collect_github_search_records,
    rows_to_seed_records as github_search_rows,
)
from migration_task_pipeline.config import GitHubSearchConfig, load_seed_config


def test_github_search_rows_use_repo_fields_without_package_metadata():
    rows = [
        {
            "name": "repo",
            "full_name": "owner/repo",
            "html_url": "https://github.com/owner/repo",
            "description": "CUDA kernels for PyTorch",
            "homepage": "https://owner.example",
            "topics": ["cuda", "pytorch"],
            "license": {"spdx_id": "MIT"},
            "stargazers_count": 15,
            "forks_count": 2,
            "archived": False,
            "fork": False,
            "default_branch": "main",
            "pushed_at": "2026-06-01T00:00:00Z",
            "size": 100,
            "language": "Python",
            "search_keyword": "cuda",
            "search_query": "cuda archived:false",
        }
    ]

    records = github_search_rows(rows, ["cuda", "pytorch"], "2026-06-16T00:00:00+00:00")

    assert len(records) == 1
    assert records[0]["source"] == "github-search"
    assert records[0]["package_name"] == ""
    assert records[0]["repo_key"] == "owner/repo"
    assert records[0]["summary"] == "CUDA kernels for PyTorch"
    assert records[0]["matched_keywords"] == ["cuda", "pytorch"]
    assert records[0]["github_stars"] == 15
    assert records[0]["github_license"] == "MIT"
    assert records[0]["github_topics"] == ["cuda", "pytorch"]


def test_github_search_collector_builds_queries():
    class FakeClient:
        def search_repositories(self, query, *, per_page, page, sort, order):
            self.calls = getattr(self, "calls", [])
            self.calls.append((query, per_page, page, sort, order))
            return {
                "items": [
                    {
                        "name": "repo",
                        "full_name": "owner/repo",
                        "html_url": "https://github.com/owner/repo",
                        "description": "CUDA package",
                        "topics": ["cuda"],
                    }
                ]
            }

    client = FakeClient()

    raw, records = collect_github_search_records(
        GitHubSearchConfig(
            enabled=True,
            keywords=["cuda"],
            extra_qualifiers=["archived:false", "fork:false"],
            per_page=10,
            max_pages_per_query=2,
        ),
        client,
        collected_at="2026-06-16T00:00:00+00:00",
    )

    assert len(raw) == 2
    assert len(records) == 2
    assert client.calls == [
        ("cuda archived:false fork:false", 10, 1, "stars", "desc"),
        ("cuda archived:false fork:false", 10, 2, "stars", "desc"),
    ]


def test_example_config_loads_github_search_source():
    config = load_seed_config("configs/seed-sources.example.yaml")

    assert config.github_search.enabled
    assert len(config.github_search.keywords) == 12

