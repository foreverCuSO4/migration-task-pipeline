"""Configuration loading for seed collection."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - exercised only in broken envs
    yaml = None
    YAML_IMPORT_ERROR = exc
else:
    YAML_IMPORT_ERROR = None


DEFAULT_KEYWORDS = [
    "torch",
    "pytorch",
    "cuda",
    "triton",
    "deep learning",
    "machine learning",
    "vision",
    "graph",
    "molecular",
    "simulation",
    "distributed",
    "accelerate",
    "transformer",
]


@dataclass(frozen=True)
class PyPIConfig:
    enabled: bool = True
    keywords: list[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    packages: list[str] = field(default_factory=list)
    bigquery_project: str | None = None
    bigquery_dataset: str = "bigquery-public-data.pypi.distribution_metadata"
    bigquery_downloads_table: str = "bigquery-public-data.pypi.file_downloads"
    bigquery_limit: int | None = None
    http_limit: int | None = None


@dataclass(frozen=True)
class CondaForgeConfig:
    enabled: bool = True
    subdirs: list[str] = field(default_factory=lambda: ["noarch", "linux-64"])
    keywords: list[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    repodata_base_url: str = "https://conda.anaconda.org/conda-forge"


@dataclass(frozen=True)
class GitHubSearchConfig:
    enabled: bool = False
    keywords: list[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    extra_qualifiers: list[str] = field(
        default_factory=lambda: ["archived:false", "fork:false", "stars:>=10"]
    )
    per_page: int = 50
    max_pages_per_query: int = 1
    sort: str = "stars"
    order: str = "desc"


@dataclass(frozen=True)
class SeedConfig:
    pypi: PyPIConfig = field(default_factory=PyPIConfig)
    conda_forge: CondaForgeConfig = field(default_factory=CondaForgeConfig)
    github_search: GitHubSearchConfig = field(default_factory=GitHubSearchConfig)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _as_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _as_int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def load_seed_config(path: str | Path) -> SeedConfig:
    """Load collector configuration from YAML."""
    if yaml is None:  # pragma: no cover - defensive
        raise RuntimeError("PyYAML is required to load seed source config") from YAML_IMPORT_ERROR

    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    pypi_raw = raw.get("pypi") or {}
    conda_raw = raw.get("conda_forge") or {}
    github_search_raw = raw.get("github_search") or {}

    pypi = PyPIConfig(
        enabled=_as_bool(pypi_raw.get("enabled"), True),
        keywords=_as_list(pypi_raw.get("keywords"), DEFAULT_KEYWORDS),
        packages=_as_list(pypi_raw.get("packages"), []),
        bigquery_project=pypi_raw.get("bigquery_project"),
        bigquery_dataset=str(
            pypi_raw.get("bigquery_dataset", PyPIConfig.bigquery_dataset)
        ),
        bigquery_downloads_table=str(
            pypi_raw.get("bigquery_downloads_table", PyPIConfig.bigquery_downloads_table)
        ),
        bigquery_limit=_as_int_or_none(pypi_raw.get("bigquery_limit")),
        http_limit=_as_int_or_none(pypi_raw.get("http_limit")),
    )
    conda_forge = CondaForgeConfig(
        enabled=_as_bool(conda_raw.get("enabled"), True),
        subdirs=_as_list(conda_raw.get("subdirs"), ["noarch", "linux-64"]),
        keywords=_as_list(conda_raw.get("keywords"), DEFAULT_KEYWORDS),
        repodata_base_url=str(
            conda_raw.get("repodata_base_url", CondaForgeConfig.repodata_base_url)
        ).rstrip("/"),
    )
    github_search = GitHubSearchConfig(
        enabled=_as_bool(github_search_raw.get("enabled"), False),
        keywords=_as_list(github_search_raw.get("keywords"), DEFAULT_KEYWORDS),
        extra_qualifiers=_as_list(
            github_search_raw.get("extra_qualifiers"),
            ["archived:false", "fork:false", "stars:>=10"],
        ),
        per_page=_as_int_or_none(github_search_raw.get("per_page")) or 50,
        max_pages_per_query=_as_int_or_none(github_search_raw.get("max_pages_per_query")) or 1,
        sort=str(github_search_raw.get("sort", "stars")),
        order=str(github_search_raw.get("order", "desc")),
    )
    return SeedConfig(pypi=pypi, conda_forge=conda_forge, github_search=github_search)
