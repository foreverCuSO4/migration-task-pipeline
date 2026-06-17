"""GitHub API client for Layer B remote screening."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests

from migration_task_pipeline.github_auth import GitHubTokenPool


@dataclass(frozen=True)
class GitHubRemoteClient:
    token: str = ""
    session: requests.Session | None = None
    api_base_url: str = "https://api.github.com"
    token_pool: GitHubTokenPool | None = None

    def __post_init__(self) -> None:
        if self.token_pool is None:
            object.__setattr__(self, "token_pool", GitHubTokenPool.from_token(self.token))

    @classmethod
    def from_env(cls, *, auth_path: str | Path = "auth.json") -> "GitHubRemoteClient":
        return cls(token_pool=GitHubTokenPool.from_env(auth_path=auth_path))

    def search_code(self, query: str, *, per_page: int = 5, page: int = 1) -> dict[str, object]:
        response = self._get(
            "/search/code",
            params={"q": query, "per_page": per_page, "page": page},
            accept="application/vnd.github.text-match+json",
        )
        return response

    def get_tree(self, repo_key: str, ref: str) -> dict[str, object]:
        return self._get(f"/repos/{repo_key}/git/trees/{ref}", params={"recursive": "1"})

    def _get(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
        accept: str = "application/vnd.github+json",
    ) -> dict[str, object]:
        http = self.session or requests.Session()
        pool = self.token_pool
        assert pool is not None
        rate_limit_errors = []
        for _ in range(len(pool)):
            token = pool.next_token()
            response = http.get(
                f"{self.api_base_url}{path}",
                headers={
                    "Accept": accept,
                    "Authorization": f"Bearer {token.token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                params=params,
                timeout=30,
            )
            if response.status_code not in {403, 429}:
                break
            rate_limit_errors.append(f"{token.label}:HTTP {response.status_code}")
        else:
            raise RuntimeError(f"GitHub rate limit or permission error for all tokens: {', '.join(rate_limit_errors)}")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        return {}
