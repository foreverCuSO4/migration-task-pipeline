"""GitHub API client for Layer B remote screening."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import requests

from migration_task_pipeline.github_auth import GitHubTokenPool


class GitHubRateLimitError(RuntimeError):
    """Raised when every configured token is currently rate limited."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class GitHubAccessError(RuntimeError):
    """Raised when GitHub refuses the request for authentication or permission reasons."""


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
        retry_after_seconds = []
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
            if not is_rate_limited_response(response):
                break
            rate_limit_errors.append(f"{token.label}:HTTP {response.status_code}")
            retry_after = response_retry_after_seconds(response)
            if retry_after is not None:
                retry_after_seconds.append(retry_after)
        else:
            retry_after = max(retry_after_seconds) if retry_after_seconds else None
            raise GitHubRateLimitError(
                f"GitHub rate limit for all tokens: {', '.join(rate_limit_errors)}",
                retry_after_seconds=retry_after,
            )
        if response.status_code in {401, 403}:
            message = response_json_message(response)
            detail = f": {message}" if message else ""
            raise GitHubAccessError(f"GitHub API access error: HTTP {response.status_code}{detail}")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        return {}


def is_rate_limited_response(response: requests.Response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    headers = getattr(response, "headers", {})
    if headers.get("X-RateLimit-Remaining") == "0":
        return True
    message = response_json_message(response).lower()
    return "rate limit" in message or "secondary rate limit" in message or "abuse detection" in message


def response_retry_after_seconds(response: requests.Response) -> float | None:
    headers = getattr(response, "headers", {})
    retry_after = headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass

    reset = headers.get("X-RateLimit-Reset")
    if reset:
        try:
            return max(0.0, float(reset) - time.time())
        except ValueError:
            pass
    return None


def response_json_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return ""
    if isinstance(payload, dict):
        return str(payload.get("message") or "")
    return ""
