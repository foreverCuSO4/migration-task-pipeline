"""Configuration for Stage D OpenCode agent review."""

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


DEFAULT_MACE_REFERENCE_PATH = (
    "/mnt/nvme0/zhujiayi/workspace/accel-trans-bench-private/tasks/mace-npu-migration"
)


@dataclass(frozen=True)
class OpenCodeConfig:
    provider_id: str = "d-reviewer"
    provider_name: str = "D Reviewer LLM"
    npm: str = "@ai-sdk/openai-compatible"
    base_url: str = ""
    model: str = ""
    agent_name: str = "g4-reviewer"
    agent_prompt_path: str = "templates/g4-reviewer-opencode.md"
    opencode_binary: str = "opencode"


@dataclass(frozen=True)
class DSelectionConfig:
    decisions: list[str] = field(default_factory=lambda: ["promote"])


@dataclass(frozen=True)
class DRuntimeConfig:
    concurrency: int = 1
    max_items: int | None = 1
    lease_seconds: int = 7200
    timeout_seconds: int = 1200
    max_attempts: int = 2


@dataclass(frozen=True)
class DPathConfig:
    candidate_cards_root: str = "candidate_cards"
    card_run_name: str = "{date}-g4-screening"
    workspace_root: str = "workspaces/d-review"
    logs_dir: str = "data/logs/d-review"
    mace_reference_path: str = DEFAULT_MACE_REFERENCE_PATH


@dataclass(frozen=True)
class LayerDConfig:
    opencode: OpenCodeConfig = field(default_factory=OpenCodeConfig)
    selection: DSelectionConfig = field(default_factory=DSelectionConfig)
    runtime: DRuntimeConfig = field(default_factory=DRuntimeConfig)
    paths: DPathConfig = field(default_factory=DPathConfig)


def load_layer_d_config(path: str | Path) -> LayerDConfig:
    """Load Stage D configuration from YAML."""
    if yaml is None:  # pragma: no cover - defensive
        raise RuntimeError("PyYAML is required to load Layer D config") from YAML_IMPORT_ERROR

    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Layer D config must be a mapping: {config_path}")

    opencode_raw = as_mapping(raw.get("opencode"), "opencode")
    selection_raw = as_mapping(raw.get("selection"), "selection")
    runtime_raw = as_mapping(raw.get("runtime"), "runtime")
    paths_raw = as_mapping(raw.get("paths"), "paths")

    default_opencode = OpenCodeConfig()
    default_selection = DSelectionConfig()
    default_runtime = DRuntimeConfig()
    default_paths = DPathConfig()

    opencode = OpenCodeConfig(
        provider_id=as_str(opencode_raw.get("provider_id"), default_opencode.provider_id),
        provider_name=as_str(opencode_raw.get("provider_name"), default_opencode.provider_name),
        npm=as_str(opencode_raw.get("npm"), default_opencode.npm),
        base_url=as_str(opencode_raw.get("base_url"), default_opencode.base_url),
        model=as_str(opencode_raw.get("model"), default_opencode.model),
        agent_name=as_str(opencode_raw.get("agent_name"), default_opencode.agent_name),
        agent_prompt_path=as_str(opencode_raw.get("agent_prompt_path"), default_opencode.agent_prompt_path),
        opencode_binary=as_str(opencode_raw.get("opencode_binary"), default_opencode.opencode_binary),
    )
    selection = DSelectionConfig(
        decisions=as_str_list(selection_raw.get("decisions"), default_selection.decisions),
    )
    runtime = DRuntimeConfig(
        concurrency=max(1, as_int(runtime_raw.get("concurrency"), default_runtime.concurrency)),
        max_items=as_optional_int(runtime_raw.get("max_items"), default_runtime.max_items),
        lease_seconds=max(1, as_int(runtime_raw.get("lease_seconds"), default_runtime.lease_seconds)),
        timeout_seconds=max(1, as_int(runtime_raw.get("timeout_seconds"), default_runtime.timeout_seconds)),
        max_attempts=max(1, as_int(runtime_raw.get("max_attempts"), default_runtime.max_attempts)),
    )
    paths = DPathConfig(
        candidate_cards_root=as_str(paths_raw.get("candidate_cards_root"), default_paths.candidate_cards_root),
        card_run_name=as_str(paths_raw.get("card_run_name"), default_paths.card_run_name),
        workspace_root=as_str(paths_raw.get("workspace_root"), default_paths.workspace_root),
        logs_dir=as_str(paths_raw.get("logs_dir"), default_paths.logs_dir),
        mace_reference_path=as_str(paths_raw.get("mace_reference_path"), default_paths.mace_reference_path),
    )
    return LayerDConfig(opencode=opencode, selection=selection, runtime=runtime, paths=paths)


def as_mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def as_str(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value).strip()


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


def as_str_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if not isinstance(value, list):
        raise ValueError("value must be a list")
    return [str(item).strip().lower() for item in value if str(item).strip()]
