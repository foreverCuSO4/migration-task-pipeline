"""GitHub repository search collector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable

from ..config import GitHubSearchConfig
from ..github_metadata import GitHubClient, github_api_payload_to_metadata
from ..github_urls import normalize_github_url
from ..keywords import match_keywords

STAR_BUCKETS = [
    "stars:>=1000",
    "stars:100..999",
    "stars:50..99",
    "stars:10..49",
]

LANGUAGE_QUALIFIERS = [
    "language:Python",
    "language:C++",
    "language:C",
    "language:Cuda",
]


@dataclass(frozen=True)
class QuerySpec:
    query: str
    keyword: str
    page: int
    sort: str
    order: str


def collect_github_search_records(
    config: GitHubSearchConfig,
    client: GitHubClient,
    *,
    collected_at: str | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Search GitHub repositories and convert results into seed records."""
    collected_at = collected_at or datetime.now(UTC).isoformat()
    raw_rows = list(search_github_repositories(config, client))
    return raw_rows, rows_to_seed_records(raw_rows, config.keywords, collected_at)


def search_github_repositories(
    config: GitHubSearchConfig,
    client: GitHubClient,
    *,
    max_requests: int | None = None,
) -> Iterable[dict[str, object]]:
    request_count = 0
    for query_spec in iter_query_frontier(config):
        if max_requests is not None and request_count >= max_requests:
            break
        request_count += 1
        payload = client.search_repositories(
            query_spec.query,
            per_page=config.per_page,
            page=query_spec.page,
            sort=query_spec.sort,
            order=query_spec.order,
        )
        for item in payload.get("items") or []:
            row = dict(item)
            row["search_keyword"] = query_spec.keyword
            row["search_query"] = query_spec.query
            row["search_page"] = query_spec.page
            row["source_record_id"] = f"github-search:{item.get('full_name', '')}"
            yield row


def iter_query_frontier(config: GitHubSearchConfig) -> Iterable[QuerySpec]:
    seen: set[tuple[str, int, str, str]] = set()
    for query in _query_families(config):
        for page in range(1, config.max_pages_per_query + 1):
            key = (query, page, config.sort, config.order)
            if key in seen:
                continue
            seen.add(key)
            yield QuerySpec(
                query=query,
                keyword=_query_keyword(query),
                page=page,
                sort=config.sort,
                order=config.order,
            )


def rows_to_seed_records(
    raw_rows: Iterable[dict[str, object]],
    keywords: list[str],
    collected_at: str,
) -> list[dict[str, object]]:
    records = []
    for row in raw_rows:
        record = raw_row_to_seed_record(row, keywords, collected_at)
        if record is not None:
            records.append(record)
    return records


def raw_row_to_seed_record(
    row: dict[str, object],
    keywords: list[str],
    collected_at: str,
) -> dict[str, object] | None:
    html_url = str(row.get("html_url") or "")
    normalized = normalize_github_url(html_url)
    if normalized is None:
        return None

    topics = row.get("topics") or []
    matched_keywords = match_keywords(
        [
            row.get("name"),
            row.get("full_name"),
            row.get("description"),
            " ".join(str(topic) for topic in topics),
            row.get("search_keyword"),
            row.get("search_query"),
        ],
        keywords,
    )
    if not matched_keywords:
        matched_keywords = [str(row.get("search_keyword") or "").strip()]
    matched_keywords = [keyword for keyword in matched_keywords if keyword]

    license_info = row.get("license") or {}
    if not isinstance(license_info, dict):
        license_info = {}
    github_metadata = github_api_payload_to_metadata(row)
    return {
        "source": "github-search",
        "package_name": "",
        "package_version": "",
        "repo_url": normalized.repo_url,
        "homepage": row.get("homepage") or html_url,
        "summary": row.get("description", ""),
        "keywords": ";".join(str(topic) for topic in topics if topic),
        "license": license_info.get("spdx_id") or license_info.get("key") or "",
        "downloads_30d": "",
        "collected_at": collected_at,
        "source_record_id": row.get("source_record_id") or f"github-search:{normalized.repo_key}",
        "repo_owner": normalized.owner,
        "repo_name": normalized.repo,
        "repo_key": normalized.repo_key,
        "url_extract_field": "html_url",
        "matched_keywords": matched_keywords,
        **github_metadata,
    }


def _build_query(keyword: str, extra_qualifiers: list[str]) -> str:
    parts = [keyword.strip()]
    parts.extend(qualifier.strip() for qualifier in extra_qualifiers if qualifier.strip())
    return " ".join(part for part in parts if part)


def _query_families(config: GitHubSearchConfig) -> Iterable[str]:
    cleaned_qualifiers = [
        qualifier.strip()
        for qualifier in config.extra_qualifiers
        if qualifier.strip() and not qualifier.strip().startswith("stars:")
    ]

    for keyword in config.keywords:
        yield _build_query(keyword, config.extra_qualifiers)

    for keyword in config.keywords:
        for star_bucket in STAR_BUCKETS:
            yield _build_query(keyword, [*cleaned_qualifiers, star_bucket])

    for keyword in config.keywords:
        for language in LANGUAGE_QUALIFIERS:
            yield _build_query(keyword, [*config.extra_qualifiers, language])


def _query_keyword(query: str) -> str:
    return query.split(" ", 1)[0]
