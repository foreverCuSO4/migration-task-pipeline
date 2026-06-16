"""Deduplicate package-level seed records to repository-level rows."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Iterable

from .github_metadata import GITHUB_METADATA_FIELDS
from .schema import csv_value

PROVENANCE_RANKS = {
    "project_urls.source": 0,
    "project_urls.repository": 0,
    "project_urls.repo": 0,
    "project_urls.code": 0,
    "project_urls.github": 0,
    "dev_url": 1,
    "home_page": 1,
    "home": 1,
    "summary": 2,
    "description": 2,
    "classifiers": 2,
}


def dedupe_seed_records(records: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        repo_key = csv_value(record.get("repo_key")).lower()
        if repo_key:
            groups[repo_key].append(record)

    rows = []
    for repo_key in sorted(groups):
        group = groups[repo_key]
        primary = min(group, key=_record_sort_key)
        row = dict(primary)
        sources = _sorted_unique(record.get("source") for record in group)
        package_names = _sorted_unique(record.get("package_name") for record in group)
        licenses = _sorted_unique(record.get("license") for record in group)
        matched_keywords = _sorted_unique(
            keyword
            for record in group
            for keyword in _split_values(record.get("matched_keywords"))
        )
        homepage_candidates = _sorted_unique(record.get("homepage") for record in group)
        downloads = [_to_int(record.get("downloads_30d")) for record in group]

        row.update(
            {
                "repo_key": repo_key,
                "sources": sources,
                "package_names": package_names,
                "licenses": licenses,
                "matched_keywords": matched_keywords,
                "homepage_candidates": homepage_candidates,
                "source_count": len(sources),
                "downloads_30d": max(downloads) if downloads else "",
                "first_seen_at": _first_seen(group),
            }
        )
        row.update(_best_existing_metadata(group))
        rows.append(row)
    return rows


def normalized_repo_rows(records: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    rows = []
    for row in dedupe_seed_records(records):
        rows.append(
            {
                "repo_key": row.get("repo_key", ""),
                "repo_url": row.get("repo_url", ""),
                "sources": row.get("sources", ""),
                "package_names": row.get("package_names", ""),
                "licenses": row.get("licenses", ""),
                "matched_keywords": row.get("matched_keywords", ""),
                "homepage_candidates": row.get("homepage_candidates", ""),
                "first_seen_at": row.get("first_seen_at", ""),
            }
        )
    return rows


def _record_sort_key(record: dict[str, object]) -> tuple[int, int, str]:
    field = csv_value(record.get("url_extract_field")).lower()
    rank = 9
    for prefix, value in PROVENANCE_RANKS.items():
        if field.startswith(prefix):
            rank = value
            break
    downloads = _to_int(record.get("downloads_30d"))
    return rank, -downloads, csv_value(record.get("package_name")).lower()


def _split_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [csv_value(item) for item in value if csv_value(item)]
    return [item.strip() for item in csv_value(value).split(";") if item.strip()]


def _sorted_unique(values: Iterable[object]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        for item in _split_values(value):
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
    return sorted(result, key=lambda item: item.lower())


def _to_int(value: object) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _first_seen(records: list[dict[str, object]]) -> str:
    values = sorted(csv_value(record.get("collected_at")) for record in records if record.get("collected_at"))
    if values:
        return values[0]
    return datetime.now(UTC).isoformat()


def _best_existing_metadata(records: list[dict[str, object]]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for field in GITHUB_METADATA_FIELDS:
        for record in records:
            value = record.get(field)
            if csv_value(value):
                metadata[field] = value
                break
    return metadata
