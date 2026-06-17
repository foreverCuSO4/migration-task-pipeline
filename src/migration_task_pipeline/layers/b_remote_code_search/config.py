"""Configuration for Layer B remote code-search screening."""

from __future__ import annotations

from dataclasses import dataclass, field


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
