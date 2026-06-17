"""GitHub API client for Layer B remote screening."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import requests


@dataclass(frozen=True)
class GitHubRemoteClient:
    token: str
    session: requests.Session | None = None
    api_base_url: str = "https://api.github.com"

    @classmethod
    def from_env(cls, *, auth_path: str | Path = "auth.json") -> "GitHubRemoteClient":
        token = os.getenv("GITHUB_TOKEN") or load_github_api_key(auth_path)
        if not token:
            raise RuntimeError("GITHUB_TOKEN or auth.json github_api_key is required for GitHub API access")
        return cls(token=token)

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
        response = http.get(
            f"{self.api_base_url}{path}",
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params=params,
            timeout=30,
        )
        if response.status_code in {403, 429}:
            raise RuntimeError(f"GitHub rate limit or permission error: HTTP {response.status_code}")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        return {}


def load_github_api_key(auth_path: str | Path) -> str:
    path = Path(auth_path)
    if not path.exists():
        return ""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    candidate_keys = ("github_api_key", "github_token", "github_key")
    if isinstance(payload, dict):
        for key in candidate_keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            if isinstance(value, dict):
                for key in candidate_keys:
                    nested = value.get(key)
                    if isinstance(nested, str) and nested.strip():
                        return nested.strip()
    return ""

