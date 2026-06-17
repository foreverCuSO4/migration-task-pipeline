import pytest

from migration_task_pipeline.github_auth import GitHubToken, GitHubTokenPool, load_github_tokens


def test_loads_object_list_tokens_from_auth_file(tmp_path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        '{"github_tokens": [{"name": "a", "token": "tok-a"}, {"name": "b", "token": "tok-b"}]}',
        encoding="utf-8",
    )

    tokens = load_github_tokens(auth_file)

    assert [token.token for token in tokens] == ["tok-a", "tok-b"]
    assert [token.name for token in tokens] == ["a", "b"]


def test_loads_legacy_single_token_from_auth_file(tmp_path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"github_api_key": "legacy"}', encoding="utf-8")

    tokens = load_github_tokens(auth_file)

    assert [token.token for token in tokens] == ["legacy"]


def test_loads_string_list_and_named_dict_tokens_from_auth_file(tmp_path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        '{"github_tokens": ["tok-a", {"name": "named", "token": "tok-b"}]}',
        encoding="utf-8",
    )

    tokens = load_github_tokens(auth_file)

    assert [token.token for token in tokens] == ["tok-a", "tok-b"]
    assert [token.name for token in tokens] == ["github_tokens[1]", "named"]


def test_token_pool_merges_env_first_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        '{"github_tokens": [{"name": "dup", "token": "env-token"}, {"name": "file", "token": "file-token"}]}',
        encoding="utf-8",
    )

    pool = GitHubTokenPool.from_env(auth_path=auth_file)

    assert [token.token for token in pool.tokens] == ["env-token", "file-token"]
    assert [pool.next_token().token for _ in range(4)] == [
        "env-token",
        "file-token",
        "env-token",
        "file-token",
    ]


def test_token_pool_requires_at_least_one_token():
    with pytest.raises(RuntimeError, match="GitHub token"):
        GitHubTokenPool([])


def test_token_label_does_not_expose_token_value():
    assert GitHubToken(token="ghp_secret", name="ghp_secret").label == "github-token"
    assert GitHubToken(token="github_pat_secret", name="github_pat_secret").label == "github-token"
    assert GitHubToken(token="plain-secret", name="").label == "github-token"
    assert GitHubToken(token="plain-secret", name="primary").label == "primary"
