"""PyPI metadata collectors."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any, Iterable

import requests

from ..config import PyPIConfig
from ..github_urls import best_github_url_from_fields
from ..keywords import match_keywords, normalize_keywords

SOURCE_LABELS = ("source", "repository", "repo", "code", "github")
HOMEPAGE_LABELS = ("homepage", "home page", "home")
DESCRIPTION_FIELDS = ("description", "summary", "classifiers")


def collect_pypi_records(
    config: PyPIConfig,
    *,
    backend: str,
    collected_at: str | None = None,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], str]:
    """Collect PyPI candidate records and return raw rows, candidates, backend used."""
    if backend == "http":
        backend = "http-curated"
    if backend not in {"auto", "bigquery", "http-curated"}:
        raise ValueError(f"Unsupported PyPI backend: {backend}")

    collected_at = collected_at or datetime.now(UTC).isoformat()
    if backend in {"auto", "bigquery"}:
        try:
            raw_rows = list(query_pypi_bigquery(config))
            return raw_rows, rows_to_seed_records(raw_rows, config.keywords, collected_at), "bigquery"
        except Exception as exc:
            if backend == "bigquery":
                raise
            print(
                "PyPI BigQuery backend unavailable; falling back to HTTP curated package list. "
                "This backend is for smoke/sample runs and does not provide broad PyPI discovery. "
                f"Reason: {exc}"
            )

    raw_rows = list(fetch_pypi_http(config, session=session))
    return raw_rows, rows_to_seed_records(raw_rows, config.keywords, collected_at), "http-curated"


def query_pypi_bigquery(config: PyPIConfig) -> Iterable[dict[str, object]]:
    """Query the PyPI public metadata dataset through BigQuery."""
    try:
        from google.cloud import bigquery
    except ImportError as exc:  # pragma: no cover - dependency/environment specific
        raise RuntimeError("google-cloud-bigquery is required for the PyPI BigQuery backend") from exc

    project = config.bigquery_project or os.getenv("GOOGLE_CLOUD_PROJECT")
    client = bigquery.Client(project=project)
    keyword_conditions = " OR ".join(
        [
            "LOWER(CONCAT("
            "COALESCE(summary, ''), ' ', "
            "COALESCE(description, ''), ' ', "
            "COALESCE(keywords, ''), ' ', "
            "ARRAY_TO_STRING(classifiers, ' '), ' ', "
            "COALESCE(home_page, ''), ' ', "
            "TO_JSON_STRING(project_urls)"
            f")) LIKE '%{_escape_sql_like(keyword.lower())}%'"
            for keyword in config.keywords
        ]
    )
    limit_clause = f"LIMIT {int(config.bigquery_limit)}" if config.bigquery_limit else ""
    query = f"""
        WITH latest AS (
          SELECT
            name,
            version,
            summary,
            description,
            classifiers,
            keywords,
            home_page,
            project_urls,
            license,
            ROW_NUMBER() OVER (PARTITION BY LOWER(name) ORDER BY upload_time DESC) AS rn
          FROM `{config.bigquery_dataset}`
          WHERE {keyword_conditions}
        ),
        downloads AS (
          SELECT file.project AS name, COUNT(*) AS downloads_30d
          FROM `{config.bigquery_downloads_table}`
          WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
          GROUP BY file.project
        )
        SELECT
          latest.name,
          latest.version,
          latest.summary,
          latest.description,
          latest.classifiers,
          latest.keywords,
          latest.home_page,
          latest.project_urls,
          latest.license,
          COALESCE(downloads.downloads_30d, 0) AS downloads_30d
        FROM latest
        LEFT JOIN downloads
          ON LOWER(latest.name) = LOWER(downloads.name)
        WHERE rn = 1
        {limit_clause}
    """
    for row in client.query(query).result():
        record = dict(row.items())
        if isinstance(record.get("project_urls"), str):
            try:
                record["project_urls"] = json.loads(record["project_urls"])
            except json.JSONDecodeError:
                pass
        yield record


def fetch_pypi_http(
    config: PyPIConfig,
    *,
    session: requests.Session | None = None,
) -> Iterable[dict[str, object]]:
    """Fetch PyPI metadata over the public JSON API for configured packages."""
    packages = config.packages
    if not packages:
        packages = _discover_pypi_packages_by_keywords(config)

    http = session or requests.Session()
    limit = config.http_limit
    for index, package_name in enumerate(packages):
        if limit is not None and index >= limit:
            break
        response = http.get(f"https://pypi.org/pypi/{package_name}/json", timeout=30)
        response.raise_for_status()
        payload = response.json()
        info = payload.get("info") or {}
        yield {
            "name": info.get("name") or package_name,
            "version": info.get("version", ""),
            "summary": info.get("summary", ""),
            "description": info.get("description", ""),
            "classifiers": info.get("classifiers") or [],
            "keywords": info.get("keywords", ""),
            "home_page": info.get("home_page", ""),
            "project_urls": info.get("project_urls") or {},
            "license": info.get("license", ""),
            "downloads_30d": "",
            "source_record_id": f"pypi:{package_name}",
        }


def rows_to_seed_records(
    raw_rows: Iterable[dict[str, object]],
    keywords: list[str],
    collected_at: str,
) -> list[dict[str, object]]:
    """Convert raw PyPI metadata rows into shared candidate records."""
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
            row.get("classifiers"),
            row.get("keywords"),
            row.get("home_page"),
            row.get("project_urls"),
        ],
        keywords,
    )
    if not matched_keywords:
        return None

    url_match = best_github_url_from_fields(_pypi_url_fields(row))
    if url_match is None:
        return None

    normalized, field_name = url_match
    return {
        "source": "pypi",
        "package_name": row.get("name", ""),
        "package_version": row.get("version", ""),
        "repo_url": normalized.repo_url,
        "homepage": row.get("home_page", ""),
        "summary": row.get("summary", ""),
        "keywords": normalize_keywords(row.get("keywords")),
        "license": row.get("license", ""),
        "downloads_30d": row.get("downloads_30d", ""),
        "collected_at": collected_at,
        "source_record_id": row.get("source_record_id") or f"pypi:{row.get('name', '')}",
        "repo_owner": normalized.owner,
        "repo_name": normalized.repo,
        "repo_key": normalized.repo_key,
        "url_extract_field": field_name,
        "matched_keywords": matched_keywords,
    }


def _pypi_url_fields(row: dict[str, object]) -> list[tuple[str, str]]:
    project_urls = row.get("project_urls") or {}
    if isinstance(project_urls, str):
        try:
            project_urls = json.loads(project_urls)
        except json.JSONDecodeError:
            project_urls = {"project_urls": project_urls}

    fields: list[tuple[str, str]] = []
    if isinstance(project_urls, dict):
        for label, url in project_urls.items():
            if _label_contains(label, SOURCE_LABELS):
                fields.append((f"project_urls.{label}", str(url or "")))
        for label, url in project_urls.items():
            if _label_contains(label, HOMEPAGE_LABELS) and not _label_contains(label, SOURCE_LABELS):
                fields.append((f"project_urls.{label}", str(url or "")))
        for label, url in project_urls.items():
            if not _label_contains(label, SOURCE_LABELS + HOMEPAGE_LABELS):
                fields.append((f"project_urls.{label}", str(url or "")))
    elif project_urls:
        fields.append(("project_urls", str(project_urls)))

    fields.append(("home_page", str(row.get("home_page") or "")))
    for field_name in DESCRIPTION_FIELDS:
        value = row.get(field_name)
        if isinstance(value, list):
            value = " ".join(str(item) for item in value)
        fields.append((field_name, str(value or "")))
    return fields


def _label_contains(label: object, needles: tuple[str, ...]) -> bool:
    normalized = str(label or "").lower()
    return any(needle in normalized for needle in needles)


def _escape_sql_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("'", "\\'")


def _discover_pypi_packages_by_keywords(config: PyPIConfig) -> list[str]:
    """Small built-in HTTP fallback package set keyed to the v0 seed keywords."""
    known_packages = [
        "torch",
        "pytorch-lightning",
        "tensorflow",
        "jax",
        "triton",
        "cupy-cuda12x",
        "numba",
        "transformers",
        "accelerate",
        "torchvision",
        "torch-geometric",
        "dgl",
        "mace-torch",
        "mpi4py",
        "ray",
        "distributed",
    ]
    keywords = {keyword.lower() for keyword in config.keywords}
    if not keywords:
        return known_packages
    return known_packages
