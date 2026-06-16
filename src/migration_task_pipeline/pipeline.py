"""Seed collector v0 pipeline orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable

from .collectors.conda_forge import fetch_conda_repodata
from .collectors.conda_forge import raw_row_to_seed_record as conda_raw_to_seed
from .collectors.github_search import raw_row_to_seed_record as github_search_raw_to_seed
from .collectors.github_search import search_github_repositories
from .collectors.pypi import fetch_pypi_http, query_pypi_bigquery
from .collectors.pypi import raw_row_to_seed_record as pypi_raw_to_seed
from .config import SeedConfig
from .dedupe import dedupe_seed_records, normalized_repo_rows
from .github_metadata import GitHubClient, enrich_repositories
from .io import ensure_parent, write_csv
from .schema import NORMALIZED_REPO_COLUMNS, REPO_SEEDS_V0_COLUMNS, normalize_row


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


class StreamingSeedCollector:
    """Incrementally normalize, dedupe, enrich, and filter seed records."""

    def __init__(self, client: GitHubClient) -> None:
        self.client = client
        self.raw_candidate_count = 0
        self.records_by_repo: dict[str, list[dict[str, object]]] = {}
        self.repo_rows: dict[str, dict[str, object]] = {}
        self.metadata_by_repo: dict[str, dict[str, object]] = {}
        self.retained_by_repo: dict[str, dict[str, object]] = {}

    def process_raw_rows(
        self,
        raw_rows: Iterable[dict[str, object]],
        *,
        raw_path: Path,
        transform: Callable[[dict[str, object]], dict[str, object] | None],
        metadata_handle,
    ) -> None:
        ensure_parent(raw_path)
        with raw_path.open("w", encoding="utf-8") as raw_handle:
            for raw_row in raw_rows:
                _write_jsonl_row(raw_handle, raw_row)
                seed_record = transform(raw_row)
                if seed_record is None:
                    continue
                self.raw_candidate_count += 1
                self.process_seed_record(seed_record, metadata_handle)

    def process_seed_record(self, seed_record: dict[str, object], metadata_handle) -> None:
        repo_key = str(seed_record.get("repo_key") or "").lower()
        if not repo_key:
            return

        self.records_by_repo.setdefault(repo_key, []).append(seed_record)
        deduped = dedupe_seed_records(self.records_by_repo[repo_key])
        if not deduped:
            return

        repo_row = deduped[0]
        self.repo_rows[repo_key] = repo_row
        if repo_key in self.metadata_by_repo:
            enriched = {**repo_row, **self.metadata_by_repo[repo_key]}
            self._update_retained(repo_key, enriched)
            return

        retained, metadata_records = enrich_repositories([repo_row], self.client)
        metadata = metadata_records[0] if metadata_records else {}
        self.metadata_by_repo[repo_key] = metadata
        if metadata:
            _write_jsonl_row(metadata_handle, metadata)
        if retained:
            self.retained_by_repo[repo_key] = retained[0]
        else:
            self.retained_by_repo.pop(repo_key, None)

    def _update_retained(self, repo_key: str, enriched: dict[str, object]) -> None:
        retained, _metadata_records = enrich_repositories([enriched], self.client)
        if retained:
            self.retained_by_repo[repo_key] = retained[0]
        else:
            self.retained_by_repo.pop(repo_key, None)

    def normalized_rows(self) -> list[dict[str, object]]:
        all_records = [record for records in self.records_by_repo.values() for record in records]
        return normalized_repo_rows(all_records)

    def processed_rows(self) -> list[dict[str, object]]:
        return [self.retained_by_repo[key] for key in sorted(self.retained_by_repo)]


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
    collector = StreamingSeedCollector(client)

    pypi_raw_path = None
    conda_raw_path = None
    github_search_raw_path = None
    pypi_backend_state: dict[str, str | None] = {"value": None}
    github_metadata_path = data_root / "interim" / f"github-metadata-{run_date}.jsonl"
    ensure_parent(github_metadata_path)

    with github_metadata_path.open("w", encoding="utf-8") as metadata_handle:
        if config.pypi.enabled:
            pypi_raw_path = data_root / "raw" / f"pypi-packages-{run_date}.jsonl"
            collector.process_raw_rows(
                _iter_pypi_raw_rows(config, pypi_backend, pypi_backend_state),
                raw_path=pypi_raw_path,
                transform=lambda row: pypi_raw_to_seed(row, config.pypi.keywords, collected_at),
                metadata_handle=metadata_handle,
            )

        if config.conda_forge.enabled:
            conda_raw_path = data_root / "raw" / f"conda-forge-repodata-{run_date}.jsonl"
            collector.process_raw_rows(
                fetch_conda_repodata(config.conda_forge),
                raw_path=conda_raw_path,
                transform=lambda row: conda_raw_to_seed(row, config.conda_forge.keywords, collected_at),
                metadata_handle=metadata_handle,
            )

        if config.github_search.enabled:
            github_search_raw_path = data_root / "raw" / f"github-search-repositories-{run_date}.jsonl"
            collector.process_raw_rows(
                search_github_repositories(config.github_search, client),
                raw_path=github_search_raw_path,
                transform=lambda row: github_search_raw_to_seed(row, config.github_search.keywords, collected_at),
                metadata_handle=metadata_handle,
            )

    normalized_path = data_root / "interim" / f"repo-urls-normalized-{run_date}.csv"
    write_csv(normalized_path, collector.normalized_rows(), NORMALIZED_REPO_COLUMNS)

    processed_path = data_root / "processed" / "repo-seeds-v0.csv"
    processed_rows = collector.processed_rows()
    write_csv(processed_path, processed_rows, REPO_SEEDS_V0_COLUMNS)

    return PipelineOutputs(
        pypi_raw=pypi_raw_path,
        conda_raw=conda_raw_path,
        github_search_raw=github_search_raw_path,
        normalized_csv=normalized_path,
        github_metadata_jsonl=github_metadata_path,
        processed_csv=processed_path,
        raw_candidate_count=collector.raw_candidate_count,
        normalized_count=len(collector.repo_rows),
        processed_count=len(processed_rows),
        pypi_backend_used=pypi_backend_state["value"],
    )


def _iter_pypi_raw_rows(
    config: SeedConfig,
    backend: str,
    backend_state: dict[str, str | None],
) -> Iterable[dict[str, object]]:
    if backend == "http":
        backend = "http-curated"
    if backend not in {"auto", "bigquery", "http-curated"}:
        raise ValueError(f"Unsupported PyPI backend: {backend}")

    if backend in {"auto", "bigquery"}:
        try:
            backend_state["value"] = "bigquery"
            yield from query_pypi_bigquery(config.pypi)
            return
        except Exception as exc:
            if backend == "bigquery":
                raise
            print(
                "PyPI BigQuery backend unavailable; falling back to HTTP curated package list. "
                "This backend is for smoke/sample runs and does not provide broad PyPI discovery. "
                f"Reason: {exc}"
            )

    backend_state["value"] = "http-curated"
    yield from fetch_pypi_http(config.pypi)


def _write_jsonl_row(handle, row: dict[str, object]) -> None:
    handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True, default=str))
    handle.write("\n")
