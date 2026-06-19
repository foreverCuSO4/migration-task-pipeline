"""Configuration for Stage C2 local heuristic screening."""

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


DEFAULT_SKIP_DIRS = [
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "build",
    "dist",
    "site-packages",
    "third_party",
    "third-party",
    "vendor",
    "external",
    "datasets",
    "dataset",
    "data",
    "checkpoints",
    "checkpoint",
    "weights",
    "models",
]


@dataclass(frozen=True)
class LocalScannerConfig:
    max_file_size_bytes: int = 2_097_152
    max_files_per_repo: int = 50_000
    max_repo_bytes: int = 1_073_741_824
    max_hits_per_repo: int = 200
    max_paths_per_group: int = 20
    skip_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_SKIP_DIRS))


@dataclass(frozen=True)
class LocalScoringConfig:
    promote_threshold: float = 0.70
    maybe_threshold: float = 0.50


@dataclass(frozen=True)
class C2RuntimeConfig:
    concurrency: int = 16
    max_items: int | None = None
    lease_seconds: int = 3600
    dashboard: str = "auto"


@dataclass(frozen=True)
class LayerC2Config:
    scanner: LocalScannerConfig = field(default_factory=LocalScannerConfig)
    scoring: LocalScoringConfig = field(default_factory=LocalScoringConfig)
    runtime: C2RuntimeConfig = field(default_factory=C2RuntimeConfig)


def load_layer_c2_config(path: str | Path) -> LayerC2Config:
    """Load Stage C2 configuration from YAML."""
    if yaml is None:  # pragma: no cover - defensive
        raise RuntimeError("PyYAML is required to load Layer C2 config") from YAML_IMPORT_ERROR

    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Layer C2 config must be a mapping: {config_path}")

    scanner_raw = raw.get("scanner") or {}
    scoring_raw = raw.get("scoring") or {}
    runtime_raw = raw.get("runtime") or {}
    if not isinstance(scanner_raw, dict):
        raise ValueError("scanner must be a mapping")
    if not isinstance(scoring_raw, dict):
        raise ValueError("scoring must be a mapping")
    if not isinstance(runtime_raw, dict):
        raise ValueError("runtime must be a mapping")

    default_scanner = LocalScannerConfig()
    default_scoring = LocalScoringConfig()
    default_runtime = C2RuntimeConfig()
    scanner = LocalScannerConfig(
        max_file_size_bytes=max(
            1,
            as_int(scanner_raw.get("max_file_size_bytes"), default_scanner.max_file_size_bytes),
        ),
        max_files_per_repo=max(1, as_int(scanner_raw.get("max_files_per_repo"), default_scanner.max_files_per_repo)),
        max_repo_bytes=max(1, as_int(scanner_raw.get("max_repo_bytes"), default_scanner.max_repo_bytes)),
        max_hits_per_repo=max(1, as_int(scanner_raw.get("max_hits_per_repo"), default_scanner.max_hits_per_repo)),
        max_paths_per_group=max(1, as_int(scanner_raw.get("max_paths_per_group"), default_scanner.max_paths_per_group)),
        skip_dirs=as_str_list(scanner_raw.get("skip_dirs"), default_scanner.skip_dirs),
    )
    scoring = LocalScoringConfig(
        promote_threshold=as_float(scoring_raw.get("promote_threshold"), default_scoring.promote_threshold),
        maybe_threshold=as_float(scoring_raw.get("maybe_threshold"), default_scoring.maybe_threshold),
    )
    runtime = C2RuntimeConfig(
        concurrency=max(1, as_int(runtime_raw.get("concurrency"), default_runtime.concurrency)),
        max_items=as_optional_int(runtime_raw.get("max_items"), default_runtime.max_items),
        lease_seconds=max(1, as_int(runtime_raw.get("lease_seconds"), default_runtime.lease_seconds)),
        dashboard=as_dashboard_mode(runtime_raw.get("dashboard"), default_runtime.dashboard),
    )
    return LayerC2Config(scanner=scanner, scoring=scoring, runtime=runtime)


def as_int(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    return int(value)


def as_optional_int(value: Any, default: int | None) -> int | None:
    if value in (None, ""):
        return default
    if isinstance(value, str) and value.strip().lower() in {"none", "null", "unlimited"}:
        return None
    return int(value)


def as_float(value: Any, default: float) -> float:
    if value in (None, ""):
        return default
    return float(value)


def as_str_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if not isinstance(value, list):
        raise ValueError("value must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def as_dashboard_mode(value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "on" if value else "off"
    mode = str(value).strip().lower()
    if mode in {"auto", "on", "off"}:
        return mode
    raise ValueError("runtime.dashboard must be one of: auto, on, off")

