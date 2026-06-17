import pytest

from migration_task_pipeline.github_auth import GitHubToken, GitHubTokenPool
from migration_task_pipeline.layers.b_remote_code_search.github_client import GitHubRemoteClient


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
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers_seen = []

    def get(self, url, headers, params, timeout):
        self.headers_seen.append(headers)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def test_remote_client_round_robins_tokens():
    session = FakeSession(
        [
            FakeResponse(200, {"items": []}),
            FakeResponse(200, {"tree": []}),
        ]
    )
    client = GitHubRemoteClient(token_pool=GitHubTokenPool(["one", "two"]), session=session)

    client.search_code("torch.cuda repo:owner/repo")
    client.get_tree("owner/repo", "main")

    assert [headers["Authorization"] for headers in session.headers_seen] == [
        "Bearer one",
        "Bearer two",
    ]


def test_remote_client_retries_rate_limited_token_with_next_token():
    session = FakeSession(
        [
            FakeResponse(429, {"message": "rate limited"}),
            FakeResponse(200, {"items": [{"path": "src/model.py"}]}),
        ]
    )
    client = GitHubRemoteClient(token_pool=GitHubTokenPool(["limited", "ok"]), session=session)

    payload = client.search_code("torch.cuda repo:owner/repo")

    assert payload["items"][0]["path"] == "src/model.py"
    assert [headers["Authorization"] for headers in session.headers_seen] == [
        "Bearer limited",
        "Bearer ok",
    ]


def test_remote_client_fails_after_all_tokens_are_rate_limited_without_leaking_tokens():
    session = FakeSession(
        [
            FakeResponse(403, {"message": "rate limited"}),
            FakeResponse(429, {"message": "rate limited"}),
        ]
    )
    client = GitHubRemoteClient(
        token_pool=GitHubTokenPool(
            [
                GitHubToken(token="ghp_first_secret", name="first"),
                GitHubToken(token="ghp_second_secret", name="ghp_second_secret"),
            ]
        ),
        session=session,
    )

    with pytest.raises(RuntimeError) as exc_info:
        client.search_code("torch.cuda repo:owner/repo")

    message = str(exc_info.value)
    assert "first:HTTP 403" in message
    assert "github-token:HTTP 429" in message
    assert "ghp_first_secret" not in message
    assert "ghp_second_secret" not in message
