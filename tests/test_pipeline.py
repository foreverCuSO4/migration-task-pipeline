from migration_task_pipeline.config import CondaForgeConfig, GitHubSearchConfig, PyPIConfig, SeedConfig
from migration_task_pipeline.pipeline import run_seed_collector_v0


class FakeGitHubClient:
    def __init__(self, events=None):
        self.events = events if events is not None else []

    def get_repo_metadata(self, repo_key):
        self.events.append(f"enrich:{repo_key}")
        return {
            "github_stars": 20,
            "github_forks": 1,
            "github_archived": False,
            "github_is_fork": False,
            "github_license": "MIT",
            "github_default_branch": "main",
            "github_pushed_at": "2026-06-01T00:00:00Z",
            "github_size_kb": 100,
            "github_topics": ["cuda"],
            "github_primary_language": "Python",
            "github_metadata_error": "",
        }

    def search_repositories(self, query, *, per_page, page, sort, order):
        return {"items": []}


def test_pipeline_writes_expected_artifacts(tmp_path, monkeypatch):
    def fake_pypi_raw_rows(config, backend, backend_state):
        backend_state["value"] = "http-curated"
        yield {
            "name": "demo",
            "version": "1.0",
            "summary": "CUDA package",
            "keywords": "cuda",
            "home_page": "https://github.com/Owner/Repo",
        }

    def fake_conda_raw_rows(config):
        return iter(())

    def fake_github_search_raw_rows(config, client):
        yield {
            "name": "Repo",
            "full_name": "Search/Repo",
            "html_url": "https://github.com/Search/Repo",
            "description": "CUDA search result",
            "topics": ["cuda"],
            "license": {"spdx_id": "MIT"},
            "stargazers_count": 20,
            "archived": False,
            "fork": False,
            "pushed_at": "2026-06-01T00:00:00Z",
            "size": 100,
        }

    monkeypatch.setattr("migration_task_pipeline.pipeline._iter_pypi_raw_rows", fake_pypi_raw_rows)
    monkeypatch.setattr("migration_task_pipeline.pipeline.fetch_conda_repodata", fake_conda_raw_rows)
    monkeypatch.setattr("migration_task_pipeline.pipeline.search_github_repositories", fake_github_search_raw_rows)

    outputs = run_seed_collector_v0(
        SeedConfig(
            pypi=PyPIConfig(enabled=True),
            conda_forge=CondaForgeConfig(enabled=True),
            github_search=GitHubSearchConfig(enabled=True),
        ),
        output_root=tmp_path,
        run_date="20260616",
        pypi_backend="http-curated",
        github_client=FakeGitHubClient(),
    )

    assert outputs.raw_candidate_count == 2
    assert outputs.normalized_count == 2
    assert outputs.processed_count == 2
    assert outputs.pypi_backend_used == "http-curated"
    assert outputs.processed_csv.read_text(encoding="utf-8").splitlines()[0].startswith(
        "source,package_name,package_version,repo_url"
    )
    assert (tmp_path / "raw" / "pypi-packages-20260616.jsonl").exists()
    assert (tmp_path / "raw" / "conda-forge-repodata-20260616.jsonl").exists()
    assert (tmp_path / "raw" / "github-search-repositories-20260616.jsonl").exists()
    assert (tmp_path / "interim" / "repo-urls-normalized-20260616.csv").exists()
    assert (tmp_path / "interim" / "github-metadata-20260616.jsonl").exists()
    assert (tmp_path / "processed" / "repo-seeds-v0.csv").exists()


def test_pipeline_enriches_each_candidate_before_collecting_next_raw_row(tmp_path, monkeypatch):
    events = []

    def fake_pypi_raw_rows(config, backend, backend_state):
        backend_state["value"] = "http-curated"
        events.append("raw:first")
        yield {
            "name": "first",
            "version": "1.0",
            "summary": "CUDA package",
            "keywords": "cuda",
            "home_page": "https://github.com/Owner/First",
        }
        events.append("raw:second")
        yield {
            "name": "second",
            "version": "1.0",
            "summary": "CUDA package",
            "keywords": "cuda",
            "home_page": "https://github.com/Owner/Second",
        }

    monkeypatch.setattr("migration_task_pipeline.pipeline._iter_pypi_raw_rows", fake_pypi_raw_rows)

    run_seed_collector_v0(
        SeedConfig(
            pypi=PyPIConfig(enabled=True),
            conda_forge=CondaForgeConfig(enabled=False),
            github_search=GitHubSearchConfig(enabled=False),
        ),
        output_root=tmp_path,
        run_date="20260616",
        pypi_backend="http-curated",
        github_client=FakeGitHubClient(events),
    )

    assert events == [
        "raw:first",
        "enrich:owner/first",
        "raw:second",
        "enrich:owner/second",
    ]

