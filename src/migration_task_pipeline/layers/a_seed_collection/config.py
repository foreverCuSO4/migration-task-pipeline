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
class GitHubSearchConfig:
    enabled: bool = True
    keywords: list[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    extra_qualifiers: list[str] = field(
        default_factory=lambda: ["archived:false", "fork:false", "stars:>=10"]
    )
    per_page: int = 100
    max_pages_per_query: int = 10
    sort: str = "stars"
    order: str = "desc"


@dataclass(frozen=True)
class GoalConfig:
    enabled: bool = True
    target_processed_repos: int = 100
    max_search_requests: int = 1000


@dataclass(frozen=True)
class SeedConfig:
    github_search: GitHubSearchConfig = field(default_factory=GitHubSearchConfig)
    goal: GoalConfig = field(default_factory=GoalConfig)


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

    github_search_raw = raw.get("github_search") or {}
    goal_raw = raw.get("goal") or {}

    github_search = GitHubSearchConfig(
        enabled=_as_bool(github_search_raw.get("enabled"), True),
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
    goal = GoalConfig(
        enabled=_as_bool(goal_raw.get("enabled"), True),
        target_processed_repos=_as_int_or_none(goal_raw.get("target_processed_repos")) or 100,
        max_search_requests=_as_int_or_none(goal_raw.get("max_search_requests")) or 1000,
    )
    return SeedConfig(github_search=github_search, goal=goal)
