"""GitHub API metadata enrichment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests

from migration_task_pipeline.github_auth import GitHubTokenPool

from .schema import csv_value

GITHUB_METADATA_FIELDS = [
    "github_stars",
    "github_forks",
    "github_archived",
    "github_is_fork",
    "github_license",
    "github_default_branch",
    "github_pushed_at",
    "github_size_kb",
    "github_topics",
    "github_primary_language",
    "github_metadata_error",
]

REQUIRED_METADATA_FOR_SKIP = [
    "github_stars",
    "github_archived",
    "github_license",
    "github_size_kb",
    "github_pushed_at",
]


@dataclass(frozen=True)
class GitHubClient:
    token: str = ""
    session: requests.Session | None = None
    api_base_url: str = "https://api.github.com"
    token_pool: GitHubTokenPool | None = None

    def __post_init__(self) -> None:
        if self.token_pool is None:
            object.__setattr__(self, "token_pool", GitHubTokenPool.from_token(self.token))

    @classmethod
    def from_env(cls, *, auth_path: str | Path = "auth.json") -> "GitHubClient":
        return cls(token_pool=GitHubTokenPool.from_env(auth_path=auth_path))

    def get_repo_metadata(self, repo_key: str) -> dict[str, object]:
        response = self._get(
            f"{self.api_base_url}/repos/{repo_key}",
            timeout=30,
        )
        if response.status_code == 404:
            raise RuntimeError("repository not found")
        response.raise_for_status()
        return github_api_payload_to_metadata(response.json())

    def search_repositories(
        self,
        query: str,
        *,
        per_page: int,
        page: int,
        sort: str,
        order: str,
    ) -> dict[str, object]:
        response = self._get(
            f"{self.api_base_url}/search/repositories",
            params={
                "q": query,
                "per_page": per_page,
                "page": page,
                "sort": sort,
                "order": order,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def _get(self, url: str, *, timeout: int, params: dict[str, object] | None = None) -> requests.Response:
        http = self.session or requests.Session()
        pool = self.token_pool
        assert pool is not None
        rate_limit_errors = []
        access_errors = []
        for _ in range(len(pool)):
            token = pool.next_token()
            response = http.get(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token.token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                params=params,
                timeout=timeout,
            )
            if is_token_access_error_response(response):
                message = response_json_message(response)
                detail = f": {message}" if message else ""
                access_errors.append(f"{token.label}:HTTP {response.status_code}{detail}")
                continue
            if response.status_code not in {403, 429}:
                return response
            rate_limit_errors.append(f"{token.label}:HTTP {response.status_code}")
        details = []
        if rate_limit_errors:
            details.append(f"rate/permission: {', '.join(rate_limit_errors)}")
        if access_errors:
            details.append(f"access: {', '.join(access_errors)}")
        raise RuntimeError(f"GitHub rate limit or permission error for all tokens: {'; '.join(details)}")


def is_token_access_error_response(response: requests.Response) -> bool:
    if response.status_code == 401:
        return True
    if response.status_code != 403:
        return False
    message = response_json_message(response).lower()
    return "resource not accessible by personal access token" in message


def response_json_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return ""
    if isinstance(payload, dict):
        return str(payload.get("message") or "")
    return ""


def enrich_repositories(
    rows: Iterable[dict[str, object]],
    client: GitHubClient,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Fetch GitHub metadata and return retained rows plus all metadata records."""
    retained = []
    metadata_records = []
    for row in rows:
        enriched = dict(row)
        if has_complete_github_metadata(enriched):
            metadata = existing_github_metadata(enriched)
            metadata_records.append({"repo_key": row.get("repo_key", ""), "repo_url": row.get("repo_url", ""), **metadata})
            if should_keep_repo(enriched):
                retained.append(enriched)
            continue

        try:
            metadata = client.get_repo_metadata(csv_value(row.get("repo_key")))
        except Exception as exc:
            metadata = {
                "repo_key": row.get("repo_key", ""),
                "repo_url": row.get("repo_url", ""),
                "github_metadata_error": str(exc),
            }
            enriched.update(metadata)
            metadata_records.append(dict(metadata))
            continue

        enriched.update(metadata)
        metadata_records.append({"repo_key": row.get("repo_key", ""), "repo_url": row.get("repo_url", ""), **metadata})
        if should_keep_repo(enriched):
            retained.append(enriched)
    return retained, metadata_records


def github_api_payload_to_metadata(payload: dict[str, object]) -> dict[str, object]:
    license_info = payload.get("license") or {}
    if not isinstance(license_info, dict):
        license_info = {}
    return {
        "github_stars": payload.get("stargazers_count", ""),
        "github_forks": payload.get("forks_count", ""),
        "github_archived": payload.get("archived", ""),
        "github_is_fork": payload.get("fork", ""),
        "github_license": license_info.get("spdx_id") or license_info.get("key") or "",
        "github_default_branch": payload.get("default_branch", ""),
        "github_pushed_at": payload.get("pushed_at", ""),
        "github_size_kb": payload.get("size", ""),
        "github_topics": payload.get("topics") or [],
        "github_primary_language": payload.get("language", ""),
        "github_metadata_error": "",
    }


def existing_github_metadata(row: dict[str, object]) -> dict[str, object]:
    return {field: row.get(field, "") for field in GITHUB_METADATA_FIELDS}


def has_complete_github_metadata(row: dict[str, object]) -> bool:
    return all(csv_value(row.get(field)) for field in REQUIRED_METADATA_FOR_SKIP)


def should_keep_repo(row: dict[str, object]) -> bool:
    if _as_bool(row.get("github_archived")):
        return False
    if not csv_value(row.get("github_license")):
        return False
    if _as_int(row.get("github_size_kb")) >= 500_000:
        return False
    stars = _as_int(row.get("github_stars"))
    downloads = _as_int(row.get("downloads_30d"))
    source_count = _as_int(row.get("source_count"))
    return stars >= 10 or downloads >= 1000 or source_count >= 2


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return csv_value(value).lower() in {"true", "1", "yes"}


def _as_int(value: object) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0
