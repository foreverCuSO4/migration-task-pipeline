from migration_task_pipeline.layers.a_seed_collection.config import GitHubSearchConfig, GoalConfig, SeedConfig
from migration_task_pipeline.layers.a_seed_collection.pipeline import run_seed_collector_v0


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
    def fake_github_search_raw_rows(config, client, max_requests=None):
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

    monkeypatch.setattr(
        "migration_task_pipeline.layers.a_seed_collection.pipeline.search_github_repositories",
        fake_github_search_raw_rows,
    )

    outputs = run_seed_collector_v0(
        SeedConfig(github_search=GitHubSearchConfig(enabled=True)),
        output_root=tmp_path,
        run_date="20260616",
        github_client=FakeGitHubClient(),
    )

    assert outputs.raw_candidate_count == 1
    assert outputs.normalized_count == 1
    assert outputs.processed_count == 1
    assert outputs.processed_csv.read_text(encoding="utf-8").splitlines()[0].startswith(
        "source,package_name,package_version,repo_url"
    )
    assert (tmp_path / "raw" / "github-search-repositories-20260616.jsonl").exists()
    assert (tmp_path / "interim" / "repo-urls-normalized-20260616.csv").exists()
    assert (tmp_path / "interim" / "github-metadata-20260616.jsonl").exists()
    assert (tmp_path / "processed" / "repo-seeds-v0.csv").exists()


def test_pipeline_enriches_each_candidate_before_collecting_next_raw_row(tmp_path, monkeypatch):
    events = []

    def fake_github_search_raw_rows(config, client, max_requests=None):
        events.append("raw:first")
        yield {
            "name": "First",
            "full_name": "Owner/First",
            "html_url": "https://github.com/Owner/First",
            "description": "CUDA package",
            "topics": ["cuda"],
        }
        events.append("raw:second")
        yield {
            "name": "Second",
            "full_name": "Owner/Second",
            "html_url": "https://github.com/Owner/Second",
            "description": "CUDA package",
            "topics": ["cuda"],
        }

    monkeypatch.setattr(
        "migration_task_pipeline.layers.a_seed_collection.pipeline.search_github_repositories",
        fake_github_search_raw_rows,
    )

    run_seed_collector_v0(
        SeedConfig(github_search=GitHubSearchConfig(enabled=True)),
        output_root=tmp_path,
        run_date="20260616",
        github_client=FakeGitHubClient(events),
    )

    assert events == [
        "raw:first",
        "enrich:owner/first",
        "raw:second",
        "enrich:owner/second",
    ]


def test_pipeline_stops_when_goal_processed_count_is_reached(tmp_path, monkeypatch):
    events = []

    def fake_github_search_raw_rows(config, client, max_requests=None):
        for name in ["First", "Second", "Third"]:
            events.append(f"raw:{name.lower()}")
            yield {
                "name": name,
                "full_name": f"Owner/{name}",
                "html_url": f"https://github.com/Owner/{name}",
                "description": "CUDA package",
                "topics": ["cuda"],
            }

    monkeypatch.setattr(
        "migration_task_pipeline.layers.a_seed_collection.pipeline.search_github_repositories",
        fake_github_search_raw_rows,
    )

    outputs = run_seed_collector_v0(
        SeedConfig(
            github_search=GitHubSearchConfig(enabled=True),
            goal=GoalConfig(enabled=True, target_processed_repos=1, max_search_requests=10),
        ),
        output_root=tmp_path,
        run_date="20260616",
        github_client=FakeGitHubClient(events),
    )

    assert outputs.processed_count == 1
    assert events == ["raw:first", "enrich:owner/first"]
