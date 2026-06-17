from migration_task_pipeline.layers.a_seed_collection.collectors.github_search import (
    collect_github_search_records,
    iter_query_frontier,
    rows_to_seed_records as github_search_rows,
    search_github_repositories,
)
from migration_task_pipeline.layers.a_seed_collection.config import GitHubSearchConfig, load_seed_config


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
            max_pages_per_query=1,
        ),
        client,
        collected_at="2026-06-16T00:00:00+00:00",
    )

    assert len(raw) == len(client.calls)
    assert len(records) == len(client.calls)
    assert client.calls[0] == ("cuda archived:false fork:false", 10, 1, "stars", "desc")
    assert ("cuda archived:false fork:false stars:100..999", 10, 1, "stars", "desc") in client.calls


def test_github_search_request_limit_stops_frontier():
    class FakeClient:
        def search_repositories(self, query, *, per_page, page, sort, order):
            self.calls = getattr(self, "calls", [])
            self.calls.append((query, page))
            return {"items": []}

    client = FakeClient()

    raw = list(
        search_github_repositories(
            GitHubSearchConfig(
                enabled=True,
                keywords=["cuda", "torch"],
                extra_qualifiers=["archived:false", "fork:false", "stars:>=10"],
                per_page=10,
                max_pages_per_query=3,
            ),
            client,
            max_requests=2,
        )
    )

    assert raw == []
    assert client.calls == [
        ("cuda archived:false fork:false stars:>=10", 1),
        ("cuda archived:false fork:false stars:>=10", 2),
    ]


def test_query_frontier_includes_star_buckets_and_language_queries():
    specs = list(
        iter_query_frontier(
            GitHubSearchConfig(
                enabled=True,
                keywords=["cuda"],
                extra_qualifiers=["archived:false", "fork:false", "stars:>=10"],
                max_pages_per_query=1,
            )
        )
    )

    queries = [spec.query for spec in specs]
    assert "cuda archived:false fork:false stars:>=10" in queries
    assert "cuda archived:false fork:false stars:100..999" in queries
    assert "cuda archived:false fork:false stars:>=10 language:Python" in queries


def test_example_config_loads_github_search_source():
    config = load_seed_config("configs/seed-sources.example.yaml")

    assert config.github_search.enabled
    assert len(config.github_search.keywords) == 12
    assert config.goal.enabled
    assert config.goal.target_processed_repos == 10000
