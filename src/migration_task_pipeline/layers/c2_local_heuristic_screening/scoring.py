"""Rule-based scoring for Stage C2 local heuristic evidence."""

from __future__ import annotations

from pathlib import PurePosixPath

from .config import LocalScoringConfig
from .scanner import (
    INSTALL_FILES,
    INTERFACE_SCRIPT_NAMES,
    LocalHit,
    has_path_part,
    hits_from_evidence,
    lower_suffix,
    path_category,
    path_weight,
    strings_from_evidence,
)


def score_repository(
    item: dict[str, object],
    evidence: dict[str, object],
    *,
    config: LocalScoringConfig | None = None,
) -> dict[str, object]:
    config = config or LocalScoringConfig()
    hits = hits_from_evidence(evidence)
    tree_paths = strings_from_evidence(evidence, "tree_paths")
    unique_hit_paths = sorted({hit.path for hit in hits if hit.path})

    source_like_hit_count = sum(1 for path in unique_hit_paths if path_category(path) == "source")
    docs_like_hit_count = sum(1 for path in unique_hit_paths if path_category(path) == "docs")
    test_example_hit_count = sum(1 for path in unique_hit_paths if path_category(path) == "test_example")
    vendor_like_hit_count = sum(1 for path in unique_hit_paths if path_category(path) == "vendor")

    cuda_score = local_cuda_score(hits, tree_paths)
    interface_score = interface_contract_score(hits, tree_paths, evidence)
    install_score = installability_score(tree_paths, evidence)
    test_score = testability_score(hits, tree_paths, evidence)
    reference_score = reference_cpu_score(hits)
    manageability = manageability_score(evidence)
    risk_penalty = local_risk_penalty(hits, tree_paths, evidence)

    c2_score = clamp01(
        0.25 * cuda_score
        + 0.25 * interface_score
        + 0.20 * install_score
        + 0.15 * test_score
        + 0.10 * reference_score
        + 0.05 * manageability
        - risk_penalty
    )
    decision, reasons = decide_repository(
        evidence,
        c2_score=c2_score,
        local_cuda_score=cuda_score,
        interface_contract_score=interface_score,
        source_like_hit_count=source_like_hit_count,
        docs_like_hit_count=docs_like_hit_count,
        config=config,
    )
    errors = [str(error) for error in evidence.get("errors") or [] if str(error)]
    if errors:
        reasons.append("has_local_scan_errors")

    payload = item.get("payload_json") or {}
    return {
        "repo_key": item.get("repo_key", ""),
        "repo_url": item.get("repo_url", ""),
        "local_path": evidence.get("local_path", ""),
        "checkout_sha": evidence.get("checkout_sha", ""),
        "disk_bytes": evidence.get("disk_bytes", payload.get("disk_bytes", "")) if isinstance(payload, dict) else "",
        "file_count": evidence.get("file_count", payload.get("file_count", "")) if isinstance(payload, dict) else "",
        "c2_score": c2_score,
        "c2_decision": decision,
        "c2_reasons": reasons,
        "local_cuda_score": cuda_score,
        "interface_contract_score": interface_score,
        "installability_score": install_score,
        "testability_score": test_score,
        "reference_cpu_score": reference_score,
        "manageability_score": manageability,
        "risk_penalty": risk_penalty,
        "scanned_file_count": evidence.get("scanned_file_count", 0),
        "scanned_bytes": evidence.get("scanned_bytes", 0),
        "skipped_large_file_count": evidence.get("skipped_large_file_count", 0),
        "tree_path_count": len(tree_paths),
        "matched_terms": evidence.get("matched_terms", []),
        "top_hit_paths": evidence.get("top_hit_paths", []),
        "source_like_hit_count": source_like_hit_count,
        "docs_like_hit_count": docs_like_hit_count,
        "test_example_hit_count": test_example_hit_count,
        "vendor_like_hit_count": vendor_like_hit_count,
        "install_files": evidence.get("install_files", []),
        "interface_files": evidence.get("interface_files", []),
        "test_example_paths": evidence.get("test_example_paths", []),
        "large_files": evidence.get("large_files", []),
        "c2_errors": errors,
    }


def local_cuda_score(hits: list[LocalHit], tree_paths: list[str]) -> float:
    cuda_hits = [hit for hit in hits if hit.group == "cuda"]
    weighted = sum(path_weight(hit.path) for hit in cuda_hits)
    source_cuda_paths = {hit.path for hit in cuda_hits if path_category(hit.path) == "source"}
    cuda_tree_paths = [path for path in tree_paths if lower_suffix(path) in {".cu", ".cuh"}]
    weighted += min(4.0, len(set(cuda_tree_paths))) * 0.75
    score = min(1.0, weighted / 4.0)
    if not source_cuda_paths and cuda_hits:
        return min(score, 0.25)
    return score


def interface_contract_score(
    hits: list[LocalHit],
    tree_paths: list[str],
    evidence: dict[str, object],
) -> float:
    terms = {hit.term for hit in hits if hit.group == "interface"}
    interface_files = strings_from_evidence(evidence, "interface_files")
    paths = {path.strip().replace("\\", "/").lower().strip("/") for path in tree_paths}
    score = 0.0
    if terms & {"console_scripts", "project.scripts"}:
        score += 0.35
    if terms & {"argparse.ArgumentParser", "click.command", "typer.Typer"}:
        score += 0.25
    if terms & {'if __name__ == "__main__"', "def main"}:
        score += 0.15
    if interface_files:
        score += 0.20
    if any(PurePosixPath(path).name.lower() in INTERFACE_SCRIPT_NAMES for path in paths):
        score += 0.15
    if any(has_path_part(path, {"examples", "example", "demo", "demos"}) for path in paths):
        score += 0.10
    return clamp01(score)


def installability_score(tree_paths: list[str], evidence: dict[str, object]) -> float:
    names = {PurePosixPath(path).name.lower() for path in tree_paths}
    install_files = {PurePosixPath(path).name.lower() for path in strings_from_evidence(evidence, "install_files")}
    names |= install_files
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
    return clamp01(score)


def testability_score(hits: list[LocalHit], tree_paths: list[str], evidence: dict[str, object]) -> float:
    terms = {hit.term for hit in hits if hit.group == "test"}
    test_paths = strings_from_evidence(evidence, "test_example_paths")
    paths = {path.strip().replace("\\", "/").lower().strip("/") for path in tree_paths}
    score = 0.0
    if test_paths or any(has_path_part(path, {"tests", "test"}) for path in paths) or "pytest" in terms:
        score += 0.45
    if any(has_path_part(path, {"examples", "example"}) for path in paths):
        score += 0.25
    if any(has_path_part(path, {"demo", "demos"}) for path in paths):
        score += 0.20
    if any(has_path_part(path, {"notebooks", "notebook"}) for path in paths):
        score += 0.10
    return clamp01(score)


def reference_cpu_score(hits: list[LocalHit]) -> float:
    terms = {hit.term for hit in hits if hit.group == "reference"}
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


def manageability_score(evidence: dict[str, object]) -> float:
    disk_bytes = as_int(evidence.get("scanned_bytes")) or as_int(evidence.get("disk_bytes"))
    file_count = as_int(evidence.get("scanned_file_count")) or as_int(evidence.get("file_count"))
    if as_bool(evidence.get("truncated")):
        return 0.0
    if file_count > 50_000:
        return 0.0
    if disk_bytes <= 0:
        return 0.5
    size_mb = disk_bytes / (1024 * 1024)
    if size_mb <= 100:
        return 1.0
    if size_mb <= 250:
        return 0.7
    if size_mb < 500:
        return 0.3
    if size_mb <= 1024:
        return 0.1
    return 0.0


def local_risk_penalty(hits: list[LocalHit], tree_paths: list[str], evidence: dict[str, object]) -> float:
    terms = {hit.term for hit in hits if hit.group == "risk"}
    names = {PurePosixPath(path).name.lower() for path in tree_paths}
    penalty = 0.0
    penalty += 0.05 * len(terms & {"download", "wandb", "gdown"})
    penalty += 0.10 * len(terms & {"kaggle", "s3://", "flash-attn", "deepspeed"})
    if ".gitmodules" in names:
        penalty += 0.05
    if ".gitattributes" in names:
        penalty += 0.05
    if as_int(evidence.get("skipped_large_file_count")) > 0:
        penalty += 0.05
    if as_bool(evidence.get("truncated")):
        penalty += 0.20
    return min(0.5, penalty)


def decide_repository(
    evidence: dict[str, object],
    *,
    c2_score: float,
    local_cuda_score: float,
    interface_contract_score: float,
    source_like_hit_count: int,
    docs_like_hit_count: int,
    config: LocalScoringConfig,
) -> tuple[str, list[str]]:
    if "missing_local_path" in {str(error) for error in evidence.get("errors") or []}:
        return "reject", ["missing_local_path"]
    if manageability_score(evidence) <= 0:
        return "reject", ["repo_too_large_or_truncated"]
    if local_cuda_score <= 0:
        return "reject", ["no_local_cuda_signal"]
    if source_like_hit_count == 0 and docs_like_hit_count > 0:
        return "reject", ["docs_only_cuda_signal"]
    if interface_contract_score <= 0:
        return "reject", ["no_interface_signal"]
    if c2_score >= config.promote_threshold and local_cuda_score >= 0.30 and interface_contract_score >= 0.30:
        return "promote", ["strong_local_evidence"]
    if c2_score >= config.maybe_threshold or (local_cuda_score >= 0.50 and interface_contract_score >= 0.20):
        return "maybe", ["partial_local_evidence"]
    return "reject", ["weak_local_evidence"]


def clamp01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def as_int(value: object) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes"}
