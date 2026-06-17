"""Configuration for Layer B remote code-search screening."""

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
class CodeQuerySpec:
    group: str
    term: str
    query: str | None = None

    def github_query(self, repo_key: str) -> str:
        term_query = self.query if self.query is not None else quote_search_term(self.term)
        return f"{term_query} repo:{repo_key}"


@dataclass(frozen=True)
class RemoteCodeSearchConfig:
    code_queries: list[CodeQuerySpec] = field(default_factory=lambda: list(DEFAULT_CODE_QUERIES))
    per_page: int = 5
    max_code_queries_per_repo: int = 24
    use_remote_tree: bool = True
    promote_threshold: float = 0.65
    maybe_threshold: float = 0.45
    rate_limit_max_retries: int | None = None
    rate_limit_retry_sleep_seconds: float = 60.0
    rate_limit_max_sleep_seconds: float = 300.0


@dataclass(frozen=True)
class LayerBRuntimeConfig:
    resume: bool = True
    dashboard: str = "auto"


@dataclass(frozen=True)
class LayerBConfig:
    remote_code_search: RemoteCodeSearchConfig = field(default_factory=RemoteCodeSearchConfig)
    runtime: LayerBRuntimeConfig = field(default_factory=LayerBRuntimeConfig)


def load_layer_b_config(path: str | Path) -> LayerBConfig:
    """Load Layer B configuration from YAML."""
    if yaml is None:  # pragma: no cover - defensive
        raise RuntimeError("PyYAML is required to load Layer B config") from YAML_IMPORT_ERROR

    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Layer B config must be a mapping: {config_path}")

    remote_raw = raw.get("remote_code_search") or {}
    runtime_raw = raw.get("runtime") or {}
    if not isinstance(remote_raw, dict):
        raise ValueError("remote_code_search must be a mapping")
    if not isinstance(runtime_raw, dict):
        raise ValueError("runtime must be a mapping")

    default_remote = RemoteCodeSearchConfig()
    rate_limit_raw = remote_raw.get("rate_limit") or {}
    if not isinstance(rate_limit_raw, dict):
        raise ValueError("remote_code_search.rate_limit must be a mapping")

    remote = RemoteCodeSearchConfig(
        code_queries=parse_code_queries(remote_raw.get("code_queries"), default_remote.code_queries),
        per_page=as_int(remote_raw.get("per_page"), default_remote.per_page),
        max_code_queries_per_repo=as_int(
            remote_raw.get("max_code_queries_per_repo"),
            default_remote.max_code_queries_per_repo,
        ),
        use_remote_tree=as_bool(remote_raw.get("use_remote_tree"), default_remote.use_remote_tree),
        promote_threshold=as_float(remote_raw.get("promote_threshold"), default_remote.promote_threshold),
        maybe_threshold=as_float(remote_raw.get("maybe_threshold"), default_remote.maybe_threshold),
        rate_limit_max_retries=as_optional_int(
            rate_limit_raw.get("max_retries"),
            default_remote.rate_limit_max_retries,
        ),
        rate_limit_retry_sleep_seconds=as_float(
            rate_limit_raw.get("retry_sleep_seconds"),
            default_remote.rate_limit_retry_sleep_seconds,
        ),
        rate_limit_max_sleep_seconds=as_float(
            rate_limit_raw.get("max_sleep_seconds"),
            default_remote.rate_limit_max_sleep_seconds,
        ),
    )
    runtime = LayerBRuntimeConfig(
        resume=as_bool(runtime_raw.get("resume"), True),
        dashboard=as_dashboard_mode(runtime_raw.get("dashboard"), "auto"),
    )
    return LayerBConfig(remote_code_search=remote, runtime=runtime)


def parse_code_queries(value: Any, default: list[CodeQuerySpec]) -> list[CodeQuerySpec]:
    if value is None:
        return list(default)
    if not isinstance(value, list):
        raise ValueError("remote_code_search.code_queries must be a list")

    queries = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"code_queries[{index}] must be a mapping")
        group = str(item.get("group") or "").strip()
        term = str(item.get("term") or "").strip()
        query = item.get("query")
        if not group or not term:
            raise ValueError(f"code_queries[{index}] requires group and term")
        queries.append(CodeQuerySpec(group=group, term=term, query=str(query) if query is not None else None))
    return queries


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


def as_float(value: Any, default: float) -> float:
    if value in (None, ""):
        return default
    return float(value)


def as_dashboard_mode(value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "on" if value else "off"
    mode = str(value).strip().lower()
    if mode in {"auto", "on", "off"}:
        return mode
    raise ValueError("runtime.dashboard must be one of: auto, on, off")


def quote_search_term(term: str) -> str:
    term = term.strip()
    if not term:
        return term
    if ":" in term and " " not in term and '"' not in term:
        return term
    if any(char.isspace() for char in term) or any(char in term for char in ".()'\"=:/"):
        escaped = term.replace('"', '\\"')
        return f'"{escaped}"'
    return term


DEFAULT_CODE_QUERIES = [
    CodeQuerySpec("cuda", "torch.cuda"),
    CodeQuerySpec("cuda", ".cuda("),
    CodeQuerySpec("cuda", 'device="cuda"'),
    CodeQuerySpec("cuda", "CUDAExtension"),
    CodeQuerySpec("cuda", "triton.jit"),
    CodeQuerySpec("cuda", "cupy"),
    CodeQuerySpec("cuda", "numba.cuda"),
    CodeQuerySpec("cuda", "nccl"),
    CodeQuerySpec("cuda", "nvcc"),
    CodeQuerySpec("cuda", "extension:cu", query="extension:cu"),
    CodeQuerySpec("cuda", "extension:cuh", query="extension:cuh"),
    CodeQuerySpec("interface", "console_scripts"),
    CodeQuerySpec("interface", "argparse.ArgumentParser"),
    CodeQuerySpec("interface", "click.command"),
    CodeQuerySpec("interface", "typer.Typer"),
    CodeQuerySpec("interface", 'if __name__ == "__main__"'),
    CodeQuerySpec("reference", "--device cpu"),
    CodeQuerySpec("reference", 'device == "cpu"'),
    CodeQuerySpec("reference", 'map_location="cpu"'),
    CodeQuerySpec("reference", "reference"),
    CodeQuerySpec("reference", "fixture"),
    CodeQuerySpec("risk", "wandb"),
    CodeQuerySpec("risk", "kaggle"),
    CodeQuerySpec("risk", "gdown"),
    CodeQuerySpec("risk", "s3://"),
    CodeQuerySpec("risk", "flash-attn"),
    CodeQuerySpec("risk", "deepspeed"),
]
