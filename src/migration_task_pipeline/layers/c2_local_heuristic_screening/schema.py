"""Output schema for Stage C2 local heuristic screening."""

from __future__ import annotations


C2_CANDIDATE_COLUMNS = [
    "repo_key",
    "repo_url",
    "local_path",
    "checkout_sha",
    "disk_bytes",
    "file_count",
    "c2_score",
    "c2_decision",
    "c2_reasons",
    "local_cuda_score",
    "interface_contract_score",
    "installability_score",
    "testability_score",
    "reference_cpu_score",
    "manageability_score",
    "risk_penalty",
    "scanned_file_count",
    "scanned_bytes",
    "skipped_large_file_count",
    "tree_path_count",
    "matched_terms",
    "top_hit_paths",
    "source_like_hit_count",
    "docs_like_hit_count",
    "test_example_hit_count",
    "vendor_like_hit_count",
    "install_files",
    "interface_files",
    "test_example_paths",
    "large_files",
    "c2_errors",
]


def csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, (list, tuple, set)):
        return ";".join(str(item) for item in value if item not in (None, ""))
    return str(value)


def normalize_row(row: dict[str, object], columns: list[str]) -> dict[str, str]:
    return {column: csv_value(row.get(column, "")) for column in columns}

