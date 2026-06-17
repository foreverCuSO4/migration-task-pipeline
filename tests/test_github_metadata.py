import pytest

from migration_task_pipeline.layers.a_seed_collection.github_metadata import (
    GitHubClient,
    enrich_repositories,
    has_complete_github_metadata,
    should_keep_repo,
)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.headers = None

    def get(self, url, headers, timeout, params=None):
        self.url = url
        self.headers = headers
        self.params = params
        self.timeout = timeout
        return self.response


def test_github_client_maps_repo_metadata():
    session = FakeSession(
        FakeResponse(
            200,
            {
                "stargazers_count": 12,
                "forks_count": 3,
                "archived": False,
                "fork": False,
                "license": {"spdx_id": "MIT"},
                "default_branch": "main",
                "pushed_at": "2026-06-01T00:00:00Z",
                "size": 123,
                "topics": ["cuda", "torch"],
                "language": "Python",
            },
        )
    )
    client = GitHubClient(token="token", session=session)

    metadata = client.get_repo_metadata("owner/repo")

    assert metadata["github_stars"] == 12
    assert metadata["github_license"] == "MIT"
    assert metadata["github_topics"] == ["cuda", "torch"]
    assert session.headers["Authorization"] == "Bearer token"


def test_should_keep_repo_applies_v0_filters():
    assert should_keep_repo(
        {
            "github_archived": False,
            "github_license": "MIT",
            "github_size_kb": 100,
            "github_stars": 10,
            "downloads_30d": "",
            "source_count": 1,
        }
    )
    assert not should_keep_repo(
        {
            "github_archived": True,
            "github_license": "MIT",
            "github_size_kb": 100,
            "github_stars": 100,
            "downloads_30d": "",
            "source_count": 1,
        }
    )
    assert not should_keep_repo(
        {
            "github_archived": False,
            "github_license": "",
            "github_size_kb": 100,
            "github_stars": 100,
            "downloads_30d": "",
            "source_count": 1,
        }
    )


def test_enrich_records_metadata_fetch_failure():
    client = GitHubClient(token="token", session=FakeSession(FakeResponse(404, {})))

    retained, metadata = enrich_repositories(
        [{"repo_key": "missing/repo", "repo_url": "https://github.com/missing/repo"}],
        client,
    )

    assert retained == []
    assert metadata[0]["repo_key"] == "missing/repo"
    assert "not found" in metadata[0]["github_metadata_error"]


def test_enrich_reuses_complete_existing_metadata_without_fetch():
    class FailingClient:
        def get_repo_metadata(self, repo_key):
            raise AssertionError("should not fetch metadata")

    row = {
        "repo_key": "owner/repo",
        "repo_url": "https://github.com/owner/repo",
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
        "downloads_30d": "",
        "source_count": 1,
    }

    retained, metadata = enrich_repositories([row], FailingClient())

    assert retained == [row]
    assert metadata[0]["repo_key"] == "owner/repo"
    assert metadata[0]["github_stars"] == 20
    assert has_complete_github_metadata(row)


def test_github_client_requires_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        GitHubClient.from_env(auth_path="/tmp/does-not-exist-auth.json")


def test_github_client_loads_token_from_auth_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"github_api_key": "from-file"}', encoding="utf-8")

    client = GitHubClient.from_env(auth_path=auth_file)

    assert client.token == "from-file"


def test_github_client_searches_repositories():
    session = FakeSession(FakeResponse(200, {"items": [{"full_name": "owner/repo"}]}))
    client = GitHubClient(token="token", session=session)

    payload = client.search_repositories(
        "cuda archived:false",
        per_page=25,
        page=2,
        sort="stars",
        order="desc",
    )

    assert payload["items"][0]["full_name"] == "owner/repo"
    assert session.url.endswith("/search/repositories")
    assert session.params == {
        "q": "cuda archived:false",
        "per_page": 25,
        "page": 2,
        "sort": "stars",
        "order": "desc",
    }
