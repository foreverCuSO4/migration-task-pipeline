"""Shared schemas for repository seed collection."""

from __future__ import annotations

BASE_SEED_COLUMNS = [
    "source",
    "package_name",
    "package_version",
    "repo_url",
    "homepage",
    "summary",
    "keywords",
    "license",
    "downloads_30d",
]

AUDIT_COLUMNS = [
    "collected_at",
    "source_record_id",
    "repo_owner",
    "repo_name",
    "repo_key",
    "url_extract_field",
    "matched_keywords",
    "sources",
    "package_names",
    "licenses",
    "homepage_candidates",
    "source_count",
]

GITHUB_COLUMNS = [
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

REPO_SEEDS_V0_COLUMNS = BASE_SEED_COLUMNS + AUDIT_COLUMNS + GITHUB_COLUMNS

NORMALIZED_REPO_COLUMNS = [
    "repo_key",
    "repo_url",
    "sources",
    "package_names",
    "licenses",
    "matched_keywords",
    "homepage_candidates",
    "first_seen_at",
]


def csv_value(value: object) -> str:
    """Return a stable CSV-safe representation for missing and list values."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return ";".join(str(item) for item in value if item not in (None, ""))
    return str(value)


def normalize_row(row: dict[str, object], columns: list[str]) -> dict[str, str]:
    """Return a row containing every column with consistent empty-string nulls."""
    return {column: csv_value(row.get(column, "")) for column in columns}

