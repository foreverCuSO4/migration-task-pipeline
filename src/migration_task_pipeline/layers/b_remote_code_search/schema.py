"""Output schema for Layer B remote code-search screening."""

from __future__ import annotations


B_CANDIDATE_COLUMNS = [
    "repo_key",
    "repo_url",
    "repo_owner",
    "repo_name",
    "github_stars",
    "github_size_kb",
    "github_license",
    "github_primary_language",
    "b_score",
    "b_decision",
    "b_reasons",
    "executable_cuda_score",
    "interface_signal_score",
    "installability_signal_score",
    "test_example_signal_score",
    "reference_signal_score",
    "repo_manageability_score",
    "risk_signal_penalty",
    "code_query_count",
    "tree_path_count",
    "matched_queries",
    "top_hit_paths",
    "source_like_hit_count",
    "docs_like_hit_count",
    "test_example_hit_count",
    "vendor_like_hit_count",
    "b_errors",
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

