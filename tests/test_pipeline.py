from migration_task_pipeline.config import CondaForgeConfig, GitHubSearchConfig, PyPIConfig, SeedConfig
from migration_task_pipeline.pipeline import run_seed_collector_v0


class FakeGitHubClient:
    def get_repo_metadata(self, repo_key):
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
    def fake_pypi(config, *, backend, collected_at, session=None):
        return (
            [{"name": "demo", "version": "1.0"}],
            [
                {
                    "source": "pypi",
                    "package_name": "demo",
                    "package_version": "1.0",
                    "repo_url": "https://github.com/Owner/Repo",
                    "homepage": "https://github.com/Owner/Repo",
                    "summary": "CUDA package",
                    "keywords": "cuda",
                    "license": "MIT",
                    "downloads_30d": 1200,
                    "collected_at": collected_at,
                    "source_record_id": "pypi:demo",
                    "repo_owner": "Owner",
                    "repo_name": "Repo",
                    "repo_key": "owner/repo",
                    "url_extract_field": "project_urls.Source",
                    "matched_keywords": ["cuda"],
                }
            ],
            "http-curated",
        )

    def fake_conda(config, *, collected_at, session=None):
        return ([], [])

    def fake_github_search(config, client, *, collected_at):
        return (
            [{"full_name": "Search/Repo"}],
            [
                {
                    "source": "github-search",
                    "package_name": "",
                    "package_version": "",
                    "repo_url": "https://github.com/Search/Repo",
                    "homepage": "https://github.com/Search/Repo",
                    "summary": "CUDA search result",
                    "keywords": "cuda",
                    "license": "MIT",
                    "downloads_30d": "",
                    "collected_at": collected_at,
                    "source_record_id": "github-search:Search/Repo",
                    "repo_owner": "Search",
                    "repo_name": "Repo",
                    "repo_key": "search/repo",
                    "url_extract_field": "html_url",
                    "matched_keywords": ["cuda"],
                }
            ],
        )

    monkeypatch.setattr("migration_task_pipeline.pipeline.collect_pypi_records", fake_pypi)
    monkeypatch.setattr("migration_task_pipeline.pipeline.collect_conda_forge_records", fake_conda)
    monkeypatch.setattr("migration_task_pipeline.pipeline.collect_github_search_records", fake_github_search)

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
    assert outputs.processed_csv.read_text(encoding="utf-8").splitlines()[0].startswith(
        "source,package_name,package_version,repo_url"
    )
    assert (tmp_path / "raw" / "pypi-packages-20260616.jsonl").exists()
    assert (tmp_path / "raw" / "conda-forge-repodata-20260616.jsonl").exists()
    assert (tmp_path / "raw" / "github-search-repositories-20260616.jsonl").exists()
    assert (tmp_path / "interim" / "repo-urls-normalized-20260616.csv").exists()
    assert (tmp_path / "interim" / "github-metadata-20260616.jsonl").exists()
    assert (tmp_path / "processed" / "repo-seeds-v0.csv").exists()
