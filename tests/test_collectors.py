from migration_task_pipeline.collectors.conda_forge import rows_to_seed_records as conda_rows
from migration_task_pipeline.collectors.github_search import (
    collect_github_search_records,
    rows_to_seed_records as github_search_rows,
)
from migration_task_pipeline.collectors.pypi import collect_pypi_records, rows_to_seed_records as pypi_rows
from migration_task_pipeline.config import GitHubSearchConfig, PyPIConfig


def test_pypi_project_source_url_wins_over_homepage():
    rows = [
        {
            "name": "demo",
            "version": "1.0",
            "summary": "CUDA toolkit",
            "description": "docs at https://github.com/docs/site",
            "keywords": "cuda",
            "home_page": "https://github.com/home/page",
            "project_urls": {
                "Documentation": "https://github.com/docs/site",
                "Source": "https://github.com/source/repo",
            },
            "license": "MIT",
            "downloads_30d": 42,
        }
    ]

    records = pypi_rows(rows, ["cuda"], "2026-06-16T00:00:00+00:00")

    assert len(records) == 1
    assert records[0]["repo_url"] == "https://github.com/source/repo"
    assert records[0]["url_extract_field"] == "project_urls.Source"


def test_conda_rows_match_keywords_and_extract_dev_url():
    rows = [
        {
            "name": "demo",
            "version": "2.0",
            "summary": "Machine learning package",
            "home": "https://example.com/demo",
            "dev_url": "https://github.com/example/demo.git",
            "license": "Apache-2.0",
            "source_record_id": "conda-forge:noarch:demo",
        }
    ]

    records = conda_rows(rows, ["machine learning"], "2026-06-16T00:00:00+00:00")

    assert len(records) == 1
    assert records[0]["repo_key"] == "example/demo"
    assert records[0]["url_extract_field"] == "dev_url"


def test_pypi_http_alias_reports_http_curated(monkeypatch):
    def fake_fetch(config, session=None):
        return []

    monkeypatch.setattr("migration_task_pipeline.collectors.pypi.fetch_pypi_http", fake_fetch)

    raw, records, backend = collect_pypi_records(
        PyPIConfig(enabled=True),
        backend="http",
        collected_at="2026-06-16T00:00:00+00:00",
    )

    assert raw == []
    assert records == []
    assert backend == "http-curated"


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
