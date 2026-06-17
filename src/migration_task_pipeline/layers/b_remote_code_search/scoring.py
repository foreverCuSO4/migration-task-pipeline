"""Rule-based scoring for Layer B remote code-search evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from .config import RemoteCodeSearchConfig

SOURCE_SUFFIXES = {
    ".py",
    ".c",
    ".cc",
    ".cpp",
    ".cu",
    ".cuh",
    ".h",
    ".hh",
    ".hpp",
    ".pyx",
    ".rs",
    ".go",
    ".java",
    ".js",
    ".ts",
}

INTERFACE_SCRIPT_NAMES = {
    "train.py",
    "eval.py",
    "evaluate.py",
    "infer.py",
    "inference.py",
    "predict.py",
    "benchmark.py",
    "demo.py",
}

INSTALL_FILES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "environment.yml",
    "environment.yaml",
    "dockerfile",
    "cmakelists.txt",
}

TEST_EXAMPLE_PARTS = {"tests", "test", "examples", "example", "demo", "demos", "notebooks", "notebook"}
DOC_PARTS = {"docs", "doc", "documentation"}
VENDOR_PARTS = {"vendor", "third_party", "third-party", "external", "build", "dist", "site-packages"}


@dataclass(frozen=True)
class CodeHit:
    group: str
    term: str
    path: str
    html_url: str = ""


def score_repository(
    seed_row: dict[str, object],
    *,
    code_hits: list[CodeHit],
    tree_paths: list[str],
    errors: list[str],
    config: RemoteCodeSearchConfig | None = None,
) -> dict[str, object]:
    config = config or RemoteCodeSearchConfig()
    unique_paths = sorted({hit.path for hit in code_hits if hit.path})
    matched_queries = sorted({hit.term for hit in code_hits if hit.term})

    source_like_hit_count = sum(1 for path in unique_paths if path_category(path) == "source")
    docs_like_hit_count = sum(1 for path in unique_paths if path_category(path) == "docs")
    test_example_hit_count = sum(1 for path in unique_paths if path_category(path) == "test_example")
    vendor_like_hit_count = sum(1 for path in unique_paths if path_category(path) == "vendor")

    cuda_score = executable_cuda_score(code_hits, tree_paths)
    interface_score = interface_signal_score(code_hits, tree_paths)
    install_score = installability_signal_score(tree_paths)
    test_score = test_example_signal_score(tree_paths, code_hits)
    reference_score = reference_signal_score(code_hits)
    manageability_score = repo_manageability_score(seed_row.get("github_size_kb"))
    risk_penalty = risk_signal_penalty(code_hits, tree_paths)

    b_score = clamp01(
        0.25 * cuda_score
        + 0.25 * interface_score
        + 0.20 * install_score
        + 0.10 * test_score
        + 0.10 * reference_score
        + 0.10 * manageability_score
        - risk_penalty
    )

    decision, reasons = decide_repository(
        seed_row,
        b_score=b_score,
        executable_cuda_score=cuda_score,
        source_like_hit_count=source_like_hit_count,
        docs_like_hit_count=docs_like_hit_count,
        config=config,
    )
    if errors:
        reasons.append("has_remote_scan_errors")

    return {
        "repo_key": seed_row.get("repo_key", ""),
        "repo_url": seed_row.get("repo_url", ""),
        "repo_owner": seed_row.get("repo_owner", ""),
        "repo_name": seed_row.get("repo_name", ""),
        "github_stars": seed_row.get("github_stars", ""),
        "github_size_kb": seed_row.get("github_size_kb", ""),
        "github_license": seed_row.get("github_license", ""),
        "github_primary_language": seed_row.get("github_primary_language", ""),
        "b_score": b_score,
        "b_decision": decision,
        "b_reasons": reasons,
        "executable_cuda_score": cuda_score,
        "interface_signal_score": interface_score,
        "installability_signal_score": install_score,
        "test_example_signal_score": test_score,
        "reference_signal_score": reference_score,
        "repo_manageability_score": manageability_score,
        "risk_signal_penalty": risk_penalty,
        "code_query_count": len({(hit.group, hit.term) for hit in code_hits}),
        "tree_path_count": len(tree_paths),
        "matched_queries": matched_queries,
        "top_hit_paths": unique_paths[:20],
        "source_like_hit_count": source_like_hit_count,
        "docs_like_hit_count": docs_like_hit_count,
        "test_example_hit_count": test_example_hit_count,
        "vendor_like_hit_count": vendor_like_hit_count,
        "b_errors": errors,
    }


def executable_cuda_score(code_hits: list[CodeHit], tree_paths: list[str]) -> float:
    cuda_hits = [hit for hit in code_hits if hit.group == "cuda"]
    weighted = sum(path_weight(hit.path) for hit in cuda_hits)
    source_cuda_paths = {hit.path for hit in cuda_hits if path_category(hit.path) == "source"}
    cuda_tree_paths = [path for path in tree_paths if lower_suffix(path) in {".cu", ".cuh"}]
    weighted += min(4.0, len(set(cuda_tree_paths))) * 0.75
    score = min(1.0, weighted / 8.0)
    if not source_cuda_paths and cuda_hits:
        return min(score, 0.25)
    return score


def interface_signal_score(code_hits: list[CodeHit], tree_paths: list[str]) -> float:
    terms = {hit.term for hit in code_hits if hit.group == "interface"}
    paths = {normalize_path(path) for path in tree_paths}
    score = 0.0
    if "console_scripts" in terms:
        score += 0.35
    if terms & {"argparse.ArgumentParser", "click.command", "typer.Typer"}:
        score += 0.25
    if any(PurePosixPath(path).name.lower() in INTERFACE_SCRIPT_NAMES for path in paths):
        score += 0.20
    if any(has_path_part(path, {"cli", "cmd", "commands"}) and lower_suffix(path) in SOURCE_SUFFIXES for path in paths):
        score += 0.15
    if any(has_path_part(path, {"examples", "example", "demo", "demos"}) for path in paths):
        score += 0.15
    if any(PurePosixPath(path).name.lower() in {"readme.md", "readme.rst"} for path in paths):
        score += 0.05
    return clamp01(score)


def installability_signal_score(tree_paths: list[str]) -> float:
    names = {PurePosixPath(path).name.lower() for path in tree_paths}
    score = 0.0
    if names & {"pyproject.toml", "setup.py", "setup.cfg"}:
        score += 0.45
    if names & {"requirements.txt", "environment.yml", "environment.yaml"}:
        score += 0.20
    if "dockerfile" in names:
        score += 0.15
    if "cmakelists.txt" in names:
        score += 0.15
    if names & {"poetry.lock", "uv.lock", "pdm.lock", "conda-lock.yml"}:
        score += 0.05
    if score <= 0.15 and "dockerfile" in names:
        return min(score, 0.5)
    return clamp01(score)


def test_example_signal_score(tree_paths: list[str], code_hits: list[CodeHit]) -> float:
    paths = {normalize_path(path) for path in tree_paths}
    terms = {hit.term for hit in code_hits}
    score = 0.0
    if any(has_path_part(path, {"tests", "test"}) for path in paths) or "pytest" in terms:
        score += 0.45
    if any(has_path_part(path, {"examples", "example"}) for path in paths):
        score += 0.25
    if any(has_path_part(path, {"demo", "demos"}) for path in paths):
        score += 0.20
    if any(has_path_part(path, {"notebooks", "notebook"}) for path in paths):
        score += 0.10
    return clamp01(score)


def reference_signal_score(code_hits: list[CodeHit]) -> float:
    terms = {hit.term for hit in code_hits if hit.group == "reference"}
    score = 0.0
    if terms & {"--device cpu", 'device == "cpu"', 'map_location="cpu"'}:
        score += 0.35
    if "backend" in terms:
        score += 0.25
    if terms & {"reference", "baseline", "expected"}:
        score += 0.20
    if terms & {"fixture", "golden"}:
        score += 0.15
    return clamp01(score)


def repo_manageability_score(value: object) -> float:
    size_kb = as_int(value)
    if size_kb <= 0:
        return 0.5
    size_mb = size_kb / 1024
    if size_mb <= 100:
        return 1.0
    if size_mb <= 250:
        return 0.7
    if size_mb < 500:
        return 0.3
    return 0.0


def risk_signal_penalty(code_hits: list[CodeHit], tree_paths: list[str]) -> float:
    terms = {hit.term for hit in code_hits if hit.group == "risk"}
    penalty = 0.0
    penalty += 0.05 * len(terms & {"wandb", "gdown"})
    penalty += 0.10 * len(terms & {"kaggle", "s3://", "flash-attn", "deepspeed"})
    names = {PurePosixPath(path).name.lower() for path in tree_paths}
    if ".gitmodules" in names:
        penalty += 0.05
    if ".gitattributes" in names:
        penalty += 0.05
    return min(0.4, penalty)


def decide_repository(
    seed_row: dict[str, object],
    *,
    b_score: float,
    executable_cuda_score: float,
    source_like_hit_count: int,
    docs_like_hit_count: int,
    config: RemoteCodeSearchConfig,
) -> tuple[str, list[str]]:
    reasons = []
    if as_bool(seed_row.get("github_archived")):
        return "reject", ["archived"]
    if repo_manageability_score(seed_row.get("github_size_kb")) <= 0:
        return "reject", ["repo_too_large"]
    if executable_cuda_score <= 0:
        return "reject", ["no_remote_cuda_signal"]
    if source_like_hit_count == 0 and docs_like_hit_count > 0:
        return "reject", ["docs_only_cuda_signal"]
    if b_score >= config.promote_threshold and executable_cuda_score >= 0.30:
        reasons.append("strong_remote_evidence")
        return "promote", reasons
    if b_score >= config.maybe_threshold or executable_cuda_score >= 0.50:
        reasons.append("partial_remote_evidence")
        return "maybe", reasons
    reasons.append("weak_remote_evidence")
    return "reject", reasons


def path_category(path: str) -> str:
    normalized = normalize_path(path)
    parts = set(normalized.split("/"))
    suffix = lower_suffix(normalized)
    if parts & VENDOR_PARTS:
        return "vendor"
    if parts & DOC_PARTS or suffix in {".md", ".rst", ".txt"}:
        return "docs"
    if parts & TEST_EXAMPLE_PARTS or suffix == ".ipynb":
        return "test_example"
    if suffix in SOURCE_SUFFIXES:
        return "source"
    return "other"


def path_weight(path: str) -> float:
    return {
        "source": 1.0,
        "test_example": 0.5,
        "docs": 0.2,
        "vendor": 0.1,
        "other": 0.4,
    }[path_category(path)]


def normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").lower().strip("/")


def has_path_part(path: str, parts: set[str]) -> bool:
    return bool(set(normalize_path(path).split("/")) & parts)


def lower_suffix(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def clamp01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def as_int(value: object) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0
