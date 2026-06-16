"""Seed collector v0 pipeline orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .collectors.conda_forge import collect_conda_forge_records
from .collectors.github_search import collect_github_search_records
from .collectors.pypi import collect_pypi_records
from .config import SeedConfig
from .dedupe import dedupe_seed_records, normalized_repo_rows
from .github_metadata import GitHubClient, enrich_repositories
from .io import write_csv, write_jsonl
from .schema import NORMALIZED_REPO_COLUMNS, REPO_SEEDS_V0_COLUMNS


@dataclass(frozen=True)
class PipelineOutputs:
    pypi_raw: Path | None
    conda_raw: Path | None
    github_search_raw: Path | None
    normalized_csv: Path
    github_metadata_jsonl: Path
    processed_csv: Path
    raw_candidate_count: int
    normalized_count: int
    processed_count: int
    pypi_backend_used: str | None


def run_seed_collector_v0(
    config: SeedConfig,
    *,
    output_root: str | Path = "data",
    run_date: str | None = None,
    pypi_backend: str = "auto",
    github_client: GitHubClient | None = None,
) -> PipelineOutputs:
    run_date = run_date or datetime.now(UTC).strftime("%Y%m%d")
    collected_at = datetime.now(UTC).isoformat()
    data_root = Path(output_root)
    client = github_client or GitHubClient.from_env()

    raw_records: list[dict[str, object]] = []
    pypi_raw_path = None
    conda_raw_path = None
    github_search_raw_path = None
    pypi_backend_used = None

    if config.pypi.enabled:
        pypi_raw, pypi_records, pypi_backend_used = collect_pypi_records(
            config.pypi,
            backend=pypi_backend,
            collected_at=collected_at,
        )
        pypi_raw_path = data_root / "raw" / f"pypi-packages-{run_date}.jsonl"
        write_jsonl(pypi_raw_path, pypi_raw)
        raw_records.extend(pypi_records)

    if config.conda_forge.enabled:
        conda_raw, conda_records = collect_conda_forge_records(
            config.conda_forge,
            collected_at=collected_at,
        )
        conda_raw_path = data_root / "raw" / f"conda-forge-repodata-{run_date}.jsonl"
        write_jsonl(conda_raw_path, conda_raw)
        raw_records.extend(conda_records)

    if config.github_search.enabled:
        github_search_raw, github_search_records = collect_github_search_records(
            config.github_search,
            client,
            collected_at=collected_at,
        )
        github_search_raw_path = data_root / "raw" / f"github-search-repositories-{run_date}.jsonl"
        write_jsonl(github_search_raw_path, github_search_raw)
        raw_records.extend(github_search_records)

    deduped = dedupe_seed_records(raw_records)
    normalized_path = data_root / "interim" / f"repo-urls-normalized-{run_date}.csv"
    write_csv(normalized_path, normalized_repo_rows(raw_records), NORMALIZED_REPO_COLUMNS)

    enriched, metadata_records = enrich_repositories(deduped, client)
    github_metadata_path = data_root / "interim" / f"github-metadata-{run_date}.jsonl"
    write_jsonl(github_metadata_path, metadata_records)

    processed_path = data_root / "processed" / "repo-seeds-v0.csv"
    write_csv(processed_path, enriched, REPO_SEEDS_V0_COLUMNS)

    return PipelineOutputs(
        pypi_raw=pypi_raw_path,
        conda_raw=conda_raw_path,
        github_search_raw=github_search_raw_path,
        normalized_csv=normalized_path,
        github_metadata_jsonl=github_metadata_path,
        processed_csv=processed_path,
        raw_candidate_count=len(raw_records),
        normalized_count=len(deduped),
        processed_count=len(enriched),
        pypi_backend_used=pypi_backend_used,
    )
