"""Configuration for Stage C1 local repository materialization."""

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


@dataclass(frozen=True)
class MaterializationConfig:
    clone_depth: int = 1
    clone_timeout_seconds: int = 1800
    lease_seconds: int = 3600
    retry_priority: int = 0
    submodules: bool = False
    lfs: bool = False
    http_proxy: str = ""
    https_proxy: str = ""
    all_proxy: str = ""
    no_proxy: str = ""


@dataclass(frozen=True)
class C1RuntimeConfig:
    concurrency: int = 4
    max_items: int | None = None


@dataclass(frozen=True)
class LayerC1Config:
    materialization: MaterializationConfig = field(default_factory=MaterializationConfig)
    runtime: C1RuntimeConfig = field(default_factory=C1RuntimeConfig)


def load_layer_c1_config(path: str | Path) -> LayerC1Config:
    """Load Stage C1 configuration from YAML."""
    if yaml is None:  # pragma: no cover - defensive
        raise RuntimeError("PyYAML is required to load Layer C1 config") from YAML_IMPORT_ERROR

    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Layer C1 config must be a mapping: {config_path}")

    materialization_raw = raw.get("materialization") or {}
    runtime_raw = raw.get("runtime") or {}
    if not isinstance(materialization_raw, dict):
        raise ValueError("materialization must be a mapping")
    if not isinstance(runtime_raw, dict):
        raise ValueError("runtime must be a mapping")
    proxy_raw = materialization_raw.get("proxy") or {}
    if not isinstance(proxy_raw, dict):
        raise ValueError("materialization.proxy must be a mapping")

    default_materialization = MaterializationConfig()
    default_runtime = C1RuntimeConfig()
    materialization = MaterializationConfig(
        clone_depth=as_int(materialization_raw.get("clone_depth"), default_materialization.clone_depth),
        clone_timeout_seconds=as_int(
            materialization_raw.get("clone_timeout_seconds"),
            default_materialization.clone_timeout_seconds,
        ),
        lease_seconds=as_int(materialization_raw.get("lease_seconds"), default_materialization.lease_seconds),
        retry_priority=as_int(materialization_raw.get("retry_priority"), default_materialization.retry_priority),
        submodules=as_bool(materialization_raw.get("submodules"), default_materialization.submodules),
        lfs=as_bool(materialization_raw.get("lfs"), default_materialization.lfs),
        http_proxy=as_str(proxy_raw.get("http"), default_materialization.http_proxy),
        https_proxy=as_str(proxy_raw.get("https"), default_materialization.https_proxy),
        all_proxy=as_str(proxy_raw.get("all"), default_materialization.all_proxy),
        no_proxy=as_str(proxy_raw.get("no_proxy"), default_materialization.no_proxy),
    )
    runtime = C1RuntimeConfig(
        concurrency=max(1, as_int(runtime_raw.get("concurrency"), default_runtime.concurrency)),
        max_items=as_optional_int(runtime_raw.get("max_items"), default_runtime.max_items),
    )
    return LayerC1Config(materialization=materialization, runtime=runtime)


def as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


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


def as_str(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value)
