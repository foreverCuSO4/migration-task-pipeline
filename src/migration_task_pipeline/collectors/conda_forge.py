"""conda-forge repodata collector."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterable

import requests

from ..config import CondaForgeConfig
from ..github_urls import best_github_url_from_fields
from ..keywords import match_keywords, normalize_keywords


def collect_conda_forge_records(
    config: CondaForgeConfig,
    *,
    collected_at: str | None = None,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    collected_at = collected_at or datetime.now(UTC).isoformat()
    raw_rows = list(fetch_conda_repodata(config, session=session))
    return raw_rows, rows_to_seed_records(raw_rows, config.keywords, collected_at)


def fetch_conda_repodata(
    config: CondaForgeConfig,
    *,
    session: requests.Session | None = None,
) -> Iterable[dict[str, object]]:
    http = session or requests.Session()
    for subdir in config.subdirs:
        url = f"{config.repodata_base_url}/{subdir}/repodata.json"
        response = http.get(url, timeout=120)
        response.raise_for_status()
        payload = response.json()
        packages = payload.get("packages", {})
        packages.update(payload.get("packages.conda", {}))
        for filename, package in packages.items():
            row = dict(package)
            row["subdir"] = subdir
            row["filename"] = filename
            row["source_record_id"] = f"conda-forge:{subdir}:{filename}"
            yield row


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
    matched_keywords = match_keywords(
        [
            row.get("name"),
            row.get("summary"),
            row.get("description"),
            row.get("home"),
            row.get("dev_url"),
            row.get("license"),
        ],
        keywords,
    )
    if not matched_keywords:
        return None

    url_match = best_github_url_from_fields(_conda_url_fields(row))
    if url_match is None:
        return None

    normalized, field_name = url_match
    return {
        "source": "conda-forge",
        "package_name": row.get("name", ""),
        "package_version": row.get("version", ""),
        "repo_url": normalized.repo_url,
        "homepage": row.get("home", ""),
        "summary": row.get("summary", "") or row.get("description", ""),
        "keywords": normalize_keywords(matched_keywords),
        "license": row.get("license", ""),
        "downloads_30d": "",
        "collected_at": collected_at,
        "source_record_id": row.get("source_record_id", ""),
        "repo_owner": normalized.owner,
        "repo_name": normalized.repo,
        "repo_key": normalized.repo_key,
        "url_extract_field": field_name,
        "matched_keywords": matched_keywords,
    }


def _conda_url_fields(row: dict[str, object]) -> list[tuple[str, str]]:
    return [
        ("dev_url", str(row.get("dev_url") or "")),
        ("home", str(row.get("home") or "")),
        ("summary", str(row.get("summary") or "")),
        ("description", str(row.get("description") or "")),
    ]
