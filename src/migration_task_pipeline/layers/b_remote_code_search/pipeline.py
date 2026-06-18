"""Layer B remote GitHub code-search screening pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import csv
import hashlib
import json
import time
from typing import Callable

import requests

from migration_task_pipeline.buffers import BufferItem, SQLiteBuffer
from migration_task_pipeline.layers.a_seed_collection.github_urls import normalize_github_url

from .config import RemoteCodeSearchConfig
from .github_client import GitHubAccessError, GitHubRateLimitError, GitHubRemoteClient
from .io import count_csv_records, ensure_parent, file_has_content, iter_csv, write_jsonl_row
from .schema import B_CANDIDATE_COLUMNS, normalize_row
from .scoring import CodeHit, score_repository


class RemoteScanIncomplete(RuntimeError):
    """Raised when Layer B cannot collect complete remote evidence."""


TRANSIENT_REQUEST_EXCEPTIONS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.SSLError,
    requests.exceptions.ChunkedEncodingError,
)


ProgressCallback = Callable[[dict[str, object]], None]


@dataclass(frozen=True)
class RemoteScreeningOutputs:
    signals_jsonl: Path
    candidates_csv: Path
    log_file: Path
    scanned_count: int
    resumed_count: int
    promoted_count: int
    maybe_count: int
    rejected_count: int
    b2c_buffer: Path | None = None
    b2c_buffer_inserted_count: int = 0
    b2c_buffer_backfilled_count: int = 0


@dataclass(frozen=True)
class ResumeState:
    repo_keys: set[str]
    promoted_count: int = 0
    maybe_count: int = 0
    rejected_count: int = 0

    @property
    def completed_count(self) -> int:
        return len(self.repo_keys)


def run_remote_code_screening(
    seed_csv: str | Path,
    *,
    output_root: str | Path,
    run_date: str | None = None,
    auth_path: str | Path = "auth.json",
    github_client: GitHubRemoteClient | None = None,
    config: RemoteCodeSearchConfig | None = None,
    limit: int | None = None,
    progress_callback: ProgressCallback | None = None,
    resume: bool = True,
    b2c_buffer_enabled: bool = True,
    b2c_buffer_path: str | Path | None = None,
) -> RemoteScreeningOutputs:
    config = config or RemoteCodeSearchConfig()
    client = github_client or GitHubRemoteClient.from_env(auth_path=auth_path)
    run_date = run_date or datetime.now(UTC).strftime("%Y%m%d")
    output_root = Path(output_root)

    signals_path = output_root / "interim" / f"github-code-signals-{run_date}.jsonl"
    candidates_path = output_root / "processed" / "repo-candidates-b.csv"
    log_path = output_root / "logs" / f"remote-code-screening-{run_date}.log"
    buffer_path = Path(b2c_buffer_path) if b2c_buffer_path is not None else default_b2c_buffer_path(output_root)
    ensure_parent(signals_path)
    ensure_parent(candidates_path)
    ensure_parent(log_path)

    b2c_buffer = SQLiteBuffer(buffer_path) if b2c_buffer_enabled else None
    source_run_id = infer_source_run_id(output_root)
    b2c_buffer_backfilled_count = 0
    if b2c_buffer is not None and resume:
        b2c_buffer_backfilled_count = backfill_b2c_buffer(
            b2c_buffer,
            candidates_path=candidates_path,
            signal_paths=signal_paths_for_backfill(output_root, signals_path),
            source_run_id=source_run_id,
        )
    b2c_buffer_inserted_count = 0

    resume_state = load_resume_state(candidates_path) if resume else ResumeState(repo_keys=set())
    completed_repo_keys = set(resume_state.repo_keys)
    resumed_count = resume_state.completed_count
    scanned_count = resumed_count
    promoted_count = resume_state.promoted_count
    maybe_count = resume_state.maybe_count
    rejected_count = resume_state.rejected_count
    started = time.monotonic()
    total_count = count_csv_records(seed_csv)
    if limit is not None:
        total_count = min(total_count, limit)
    current_index = 0
    current_repo_key = ""

    def emit_progress(event: object, **payload: object) -> None:
        if progress_callback is None:
            return
        if isinstance(event, dict):
            event_name = str(event.get("event") or "progress")
            event_payload = {key: value for key, value in event.items() if key != "event"}
        else:
            event_name = str(event)
            event_payload = payload
        progress_callback(
            {
                "event": event_name,
                "total_count": total_count,
                "scanned_count": scanned_count,
                "promoted_count": promoted_count,
                "maybe_count": maybe_count,
                "rejected_count": rejected_count,
                "current_index": current_index,
                "current_repo_key": current_repo_key,
                "elapsed_sec": time.monotonic() - started,
                **event_payload,
            }
        )

    with (
        signals_path.open("a" if resume else "w", encoding="utf-8") as signals_handle,
        candidates_path.open("a" if resume else "w", encoding="utf-8", newline="") as csv_handle,
        log_path.open("a" if resume else "w", encoding="utf-8") as log_handle,
    ):
        writer = csv.DictWriter(csv_handle, fieldnames=B_CANDIDATE_COLUMNS, extrasaction="ignore")
        if not resume or not file_has_content(candidates_path):
            writer.writeheader()
            csv_handle.flush()
        write_log(
            log_handle,
            "start",
            {
                "seed_csv": str(seed_csv),
                "output_root": str(output_root),
                "limit": limit,
                "resume_enabled": resume,
                "resumed_count": resumed_count,
                "per_page": config.per_page,
                "max_code_queries_per_repo": config.max_code_queries_per_repo,
                "use_remote_tree": config.use_remote_tree,
                "rate_limit_max_retries": config.rate_limit_max_retries,
                "rate_limit_retry_sleep_seconds": config.rate_limit_retry_sleep_seconds,
                "rate_limit_max_sleep_seconds": config.rate_limit_max_sleep_seconds,
                "transient_error_max_retries": config.transient_error_max_retries,
                "transient_error_retry_sleep_seconds": config.transient_error_retry_sleep_seconds,
                "transient_error_max_sleep_seconds": config.transient_error_max_sleep_seconds,
                "total_count": total_count,
                "b2c_buffer_enabled": b2c_buffer is not None,
                "b2c_buffer": str(buffer_path) if b2c_buffer is not None else None,
                "b2c_buffer_backfilled_count": b2c_buffer_backfilled_count,
            },
        )
        emit_progress(
            "start",
            seed_csv=str(seed_csv),
            output_root=str(output_root),
            signals_jsonl=str(signals_path),
            candidates_csv=str(candidates_path),
            log_file=str(log_path),
            resume_enabled=resume,
            resumed_count=resumed_count,
            b2c_buffer=str(buffer_path) if b2c_buffer is not None else None,
            b2c_buffer_backfilled_count=b2c_buffer_backfilled_count,
        )
        if b2c_buffer is not None:
            write_log(
                log_handle,
                "b2c_buffer_backfill_done",
                {
                    "path": str(buffer_path),
                    "inserted_count": b2c_buffer_backfilled_count,
                },
            )

        for index, seed_row in enumerate(iter_csv(seed_csv), start=1):
            if limit is not None and index > limit:
                write_log(log_handle, "limit_reached", {"limit": limit})
                emit_progress("limit_reached", limit=limit)
                break

            current_index = index
            current_repo_key = str(seed_row.get("repo_key") or "")
            repo_key = current_repo_key
            normalized_repo_key = repo_key.strip().lower()
            if normalized_repo_key and normalized_repo_key in completed_repo_keys:
                write_log(log_handle, "repo_skipped_resume", {"index": index, "repo_key": repo_key})
                emit_progress("repo_skipped_resume")
                continue

            write_log(log_handle, "repo_start", {"index": index, "repo_key": repo_key})
            emit_progress("repo_start")
            repo_started = time.monotonic()
            evidence = scan_seed_row(
                seed_row,
                client=client,
                config=config,
                log_handle=log_handle,
                progress_callback=emit_progress,
            )
            scored = score_repository(
                seed_row,
                code_hits=[
                    CodeHit(
                        group=str(hit.get("group", "")),
                        term=str(hit.get("term", "")),
                        path=str(hit.get("path", "")),
                        html_url=str(hit.get("html_url", "")),
                    )
                    for hit in evidence["code_hits"]
                ],
                tree_paths=[str(path) for path in evidence["tree_paths"]],
                errors=[str(error) for error in evidence["errors"]],
                config=config,
            )
            evidence["scores"] = scored
            write_jsonl_row(signals_handle, evidence)
            writer.writerow(normalize_row(scored, B_CANDIDATE_COLUMNS))
            csv_handle.flush()
            if b2c_buffer is not None:
                if insert_b2c_buffer_item(b2c_buffer, scored, evidence, source_run_id=source_run_id):
                    b2c_buffer_inserted_count += 1
            if normalized_repo_key:
                completed_repo_keys.add(normalized_repo_key)

            scanned_count += 1
            decision = scored.get("b_decision")
            if decision == "promote":
                promoted_count += 1
            elif decision == "maybe":
                maybe_count += 1
            elif decision == "reject":
                rejected_count += 1
            repo_elapsed = time.monotonic() - repo_started
            write_log(
                log_handle,
                "repo_done",
                {
                    "index": index,
                    "repo_key": repo_key,
                    "decision": decision,
                    "b_score": scored.get("b_score"),
                    "b2c_buffer_inserted_count": b2c_buffer_inserted_count,
                    "elapsed_sec": round(repo_elapsed, 3),
                    "errors": scored.get("b_errors", []),
                },
            )
            emit_progress(
                "repo_done",
                decision=decision,
                b_score=scored.get("b_score"),
                b2c_buffer_inserted_count=b2c_buffer_inserted_count,
                repo_elapsed_sec=repo_elapsed,
                errors=scored.get("b_errors", []),
            )

        write_log(
            log_handle,
            "finish",
            {
                "scanned_count": scanned_count,
                "resumed_count": resumed_count,
                "promoted_count": promoted_count,
                "maybe_count": maybe_count,
                "rejected_count": rejected_count,
                "b2c_buffer": str(buffer_path) if b2c_buffer is not None else None,
                "b2c_buffer_inserted_count": b2c_buffer_inserted_count,
                "b2c_buffer_backfilled_count": b2c_buffer_backfilled_count,
                "elapsed_sec": round(time.monotonic() - started, 3),
            },
        )
        emit_progress("finish")

    return RemoteScreeningOutputs(
        signals_jsonl=signals_path,
        candidates_csv=candidates_path,
        log_file=log_path,
        scanned_count=scanned_count,
        resumed_count=resumed_count,
        promoted_count=promoted_count,
        maybe_count=maybe_count,
        rejected_count=rejected_count,
        b2c_buffer=buffer_path if b2c_buffer is not None else None,
        b2c_buffer_inserted_count=b2c_buffer_inserted_count,
        b2c_buffer_backfilled_count=b2c_buffer_backfilled_count,
    )


B2C_DECISIONS = {"promote", "maybe"}


def default_b2c_buffer_path(output_root: str | Path) -> Path:
    root = Path(output_root)
    if is_run_data_root(root):
        return root.parent / "buffers" / "b_to_c.sqlite"
    return root / "buffers" / "b_to_c.sqlite"


def infer_source_run_id(output_root: str | Path) -> str:
    root = Path(output_root)
    if is_run_data_root(root):
        return root.parent.name
    return root.name


def is_run_data_root(path: Path) -> bool:
    parts = path.parts
    return len(parts) >= 3 and parts[-1] == "data" and parts[-3] == "runs"


def signal_paths_for_backfill(output_root: str | Path, current_signals_path: str | Path) -> list[Path]:
    current = Path(current_signals_path)
    interim_dir = Path(output_root) / "interim"
    paths = sorted(interim_dir.glob("github-code-signals-*.jsonl")) if interim_dir.exists() else []
    if current not in paths:
        paths.append(current)
    return paths


def backfill_b2c_buffer(
    buffer: SQLiteBuffer,
    *,
    candidates_path: str | Path,
    signal_paths: list[Path],
    source_run_id: str,
) -> int:
    evidence_by_repo = load_signal_evidence_by_repo(signal_paths)
    csv_repo_keys: set[str] = set()
    inserted_count = 0

    if file_has_content(candidates_path):
        with Path(candidates_path).open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                repo_key = normalized_repo_key(row)
                if repo_key:
                    csv_repo_keys.add(repo_key)
                if not is_b2c_candidate(row):
                    continue
                evidence = evidence_by_repo.get(repo_key, {})
                evidence_json = dict(evidence)
                evidence_json["resume_source"] = "csv_and_jsonl" if evidence else "csv_only"
                evidence_json["candidate_csv_row"] = row
                if insert_b2c_buffer_item(buffer, row, evidence_json, source_run_id=source_run_id):
                    inserted_count += 1

    for repo_key, evidence in evidence_by_repo.items():
        if repo_key in csv_repo_keys:
            continue
        scores = evidence.get("scores")
        if not isinstance(scores, dict) or not is_b2c_candidate(scores):
            continue
        evidence_json = dict(evidence)
        evidence_json["resume_source"] = "jsonl_only"
        if insert_b2c_buffer_item(buffer, scores, evidence_json, source_run_id=source_run_id):
            inserted_count += 1

    return inserted_count


def load_signal_evidence_by_repo(signal_paths: list[Path]) -> dict[str, dict[str, object]]:
    evidence_by_repo: dict[str, dict[str, object]] = {}
    for path in signal_paths:
        if not file_has_content(path):
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    evidence = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(evidence, dict):
                    continue
                repo_key = str(evidence.get("repo_key") or "").strip().lower()
                if repo_key:
                    evidence_by_repo[repo_key] = evidence
    return evidence_by_repo


def insert_b2c_buffer_item(
    buffer: SQLiteBuffer,
    scored: dict[str, object],
    evidence: dict[str, object],
    *,
    source_run_id: str,
) -> bool:
    if not is_b2c_candidate(scored):
        return False
    item = build_b2c_buffer_item(scored, evidence, source_run_id=source_run_id)
    if item is None:
        return False
    return buffer.insert_item(item)


def build_b2c_buffer_item(
    scored: dict[str, object],
    evidence: dict[str, object],
    *,
    source_run_id: str,
) -> BufferItem | None:
    repo_key = normalized_repo_key(scored) or normalized_repo_key(evidence)
    if not repo_key:
        return None

    repo_url = normalized_repo_url(scored, repo_key)
    item_id = github_url_item_id(repo_url)
    repo_owner = str(scored.get("repo_owner") or "").strip()
    repo_name = str(scored.get("repo_name") or "").strip()
    repo_full_name = f"{repo_owner}/{repo_name}" if repo_owner and repo_name else repo_key
    payload = {
        "repo_key": repo_key,
        "repo_full_name": repo_full_name,
        "repo_url": repo_url,
        "repo_owner": repo_owner,
        "repo_name": repo_name,
        "github_default_branch": evidence.get("github_default_branch", ""),
        "github_license": scored.get("github_license", ""),
        "github_size_kb": scored.get("github_size_kb", ""),
        "github_primary_language": scored.get("github_primary_language", ""),
        "b_decision": scored.get("b_decision", ""),
        "b_reasons": scored.get("b_reasons", []),
    }

    return BufferItem(
        item_id=item_id,
        repo_id=item_id,
        repo_key=repo_key,
        repo_full_name=repo_full_name,
        repo_url=repo_url,
        source_layer="B",
        source_run_id=source_run_id,
        payload_version="b_to_c.v1",
        payload_json=payload,
        scores_json=dict(scored),
        evidence_json=dict(evidence),
        priority=b2c_priority(scored),
        status="pending",
    )


def normalized_repo_key(row: dict[str, object]) -> str:
    return str(row.get("repo_key") or "").strip().lower()


def normalized_repo_url(row: dict[str, object], repo_key: str) -> str:
    repo_url = str(row.get("repo_url") or "").strip()
    normalized = normalize_github_url(repo_url) if repo_url else None
    if normalized is None and repo_key:
        normalized = normalize_github_url(f"https://github.com/{repo_key}")
    if normalized is not None:
        return normalized.repo_url
    return repo_url or f"https://github.com/{repo_key}"


def github_url_item_id(repo_url: str) -> str:
    digest = hashlib.sha256(repo_url.strip().lower().encode("utf-8")).hexdigest()
    return f"github-url:{digest}"


def is_b2c_candidate(row: dict[str, object]) -> bool:
    return str(row.get("b_decision") or "").strip().lower() in B2C_DECISIONS


def b2c_priority(row: dict[str, object]) -> int:
    score = as_float(row.get("b_score"))
    decision = str(row.get("b_decision") or "").strip().lower()
    base = 200_000 if decision == "promote" else 100_000
    return base + round(score * 10_000)


def as_float(value: object) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_resume_state(candidates_path: str | Path) -> ResumeState:
    path = Path(candidates_path)
    if not file_has_content(path):
        return ResumeState(repo_keys=set())

    repo_keys: set[str] = set()
    promoted_count = 0
    maybe_count = 0
    rejected_count = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            repo_key = str(row.get("repo_key") or "").strip().lower()
            if not repo_key or repo_key in repo_keys:
                continue
            repo_keys.add(repo_key)
            decision = str(row.get("b_decision") or "").strip()
            if decision == "promote":
                promoted_count += 1
            elif decision == "maybe":
                maybe_count += 1
            elif decision == "reject":
                rejected_count += 1
    return ResumeState(
        repo_keys=repo_keys,
        promoted_count=promoted_count,
        maybe_count=maybe_count,
        rejected_count=rejected_count,
    )


def scan_seed_row(
    seed_row: dict[str, object],
    *,
    client: GitHubRemoteClient,
    config: RemoteCodeSearchConfig,
    log_handle=None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, object]:
    repo_key = str(seed_row.get("repo_key") or "").strip().lower()
    default_branch = str(seed_row.get("github_default_branch") or "HEAD").strip() or "HEAD"
    errors: list[str] = []
    tree_paths: list[str] = []
    code_hits: list[dict[str, object]] = []

    if not repo_key:
        return {
            "repo_key": repo_key,
            "repo_url": seed_row.get("repo_url", ""),
            "tree_paths": tree_paths,
            "code_hits": code_hits,
            "errors": ["missing_repo_key"],
        }

    if config.use_remote_tree:
        try:
            tree_payload = call_with_remote_retry(
                lambda: client.get_tree(repo_key, default_branch),
                config=config,
                log_handle=log_handle,
                progress_callback=progress_callback,
                repo_key=repo_key,
                operation="tree",
            )
            tree_paths = extract_tree_paths(tree_payload)
            if tree_payload.get("truncated"):
                errors.append("tree_truncated")
            write_log(log_handle, "tree_done", {"repo_key": repo_key, "path_count": len(tree_paths)})
            emit_scan_progress(progress_callback, "tree_done", repo_key=repo_key, path_count=len(tree_paths))
        except (GitHubAccessError, RemoteScanIncomplete):
            raise
        except Exception as exc:
            errors.append(f"tree_error:{exc}")
            write_log(log_handle, "tree_error", {"repo_key": repo_key, "error": str(exc)})
            emit_scan_progress(progress_callback, "tree_error", repo_key=repo_key, error=str(exc))

    for query_spec in config.code_queries[: config.max_code_queries_per_repo]:
        query = query_spec.github_query(repo_key)
        emit_scan_progress(
            progress_callback,
            "code_search_start",
            repo_key=repo_key,
            term=query_spec.term,
            query=query,
        )
        try:
            payload = call_with_remote_retry(
                lambda: client.search_code(query, per_page=config.per_page, page=1),
                config=config,
                log_handle=log_handle,
                progress_callback=progress_callback,
                repo_key=repo_key,
                operation="code_search",
                term=query_spec.term,
                query=query,
            )
        except (GitHubAccessError, RemoteScanIncomplete):
            raise
        except Exception as exc:
            errors.append(f"code_search_error:{query_spec.term}:{exc}")
            write_log(
                log_handle,
                "code_search_error",
                {"repo_key": repo_key, "term": query_spec.term, "query": query, "error": str(exc)},
            )
            emit_scan_progress(
                progress_callback,
                "code_search_error",
                repo_key=repo_key,
                term=query_spec.term,
                query=query,
                error=str(exc),
            )
            continue

        item_count = 0
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_count += 1
            path = str(item.get("path") or "")
            code_hits.append(
                {
                    "group": query_spec.group,
                    "term": query_spec.term,
                    "query": query,
                    "path": path,
                    "html_url": item.get("html_url", ""),
                    "sha": item.get("sha", ""),
                    "score": item.get("score", ""),
                }
            )
        write_log(
            log_handle,
            "code_search_done",
            {"repo_key": repo_key, "term": query_spec.term, "query": query, "hit_count": item_count},
        )
        emit_scan_progress(
            progress_callback,
            "code_search_done",
            repo_key=repo_key,
            term=query_spec.term,
            query=query,
            hit_count=item_count,
        )

    return {
        "repo_key": repo_key,
        "repo_url": seed_row.get("repo_url", ""),
        "github_default_branch": default_branch,
        "tree_paths": tree_paths,
        "code_hits": code_hits,
        "errors": errors,
    }


def call_with_remote_retry(
    operation_call,
    *,
    config: RemoteCodeSearchConfig,
    log_handle,
    progress_callback: ProgressCallback | None,
    repo_key: str,
    operation: str,
    term: str | None = None,
    query: str | None = None,
) -> dict[str, object]:
    rate_limit_retry_count = 0
    transient_retry_count = 0
    while True:
        try:
            return operation_call()
        except GitHubRateLimitError as exc:
            rate_limit_retry_count += 1
            if (
                config.rate_limit_max_retries is not None
                and rate_limit_retry_count > config.rate_limit_max_retries
            ):
                raise RemoteScanIncomplete(
                    f"{operation}_rate_limit_exhausted after {config.rate_limit_max_retries} retries: {exc}"
                ) from exc

            sleep_seconds = rate_limit_sleep_seconds(exc, config)
            write_log(
                log_handle,
                "rate_limit_retry",
                {
                    "repo_key": repo_key,
                    "operation": operation,
                    "term": term,
                    "query": query,
                    "retry_count": rate_limit_retry_count,
                    "sleep_seconds": sleep_seconds,
                    "error": str(exc),
                },
            )
            emit_scan_progress(
                progress_callback,
                "rate_limit_retry",
                repo_key=repo_key,
                operation=operation,
                term=term,
                query=query,
                retry_count=rate_limit_retry_count,
                sleep_seconds=sleep_seconds,
                error=str(exc),
            )
            sleep_with_progress(
                sleep_seconds,
                progress_callback=progress_callback,
                repo_key=repo_key,
                operation=operation,
                term=term,
                query=query,
                retry_count=rate_limit_retry_count,
            )
        except TRANSIENT_REQUEST_EXCEPTIONS as exc:
            transient_retry_count += 1
            if transient_retry_count > config.transient_error_max_retries:
                raise RemoteScanIncomplete(
                    (
                        f"{operation}_transient_error_exhausted after "
                        f"{config.transient_error_max_retries} retries: {exc}"
                    )
                ) from exc

            sleep_seconds = transient_error_sleep_seconds(config)
            write_log(
                log_handle,
                "transient_error_retry",
                {
                    "repo_key": repo_key,
                    "operation": operation,
                    "term": term,
                    "query": query,
                    "retry_count": transient_retry_count,
                    "sleep_seconds": sleep_seconds,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            emit_scan_progress(
                progress_callback,
                "transient_error_retry",
                repo_key=repo_key,
                operation=operation,
                term=term,
                query=query,
                retry_count=transient_retry_count,
                sleep_seconds=sleep_seconds,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            sleep_with_progress(
                sleep_seconds,
                progress_callback=progress_callback,
                repo_key=repo_key,
                operation=operation,
                term=term,
                query=query,
                retry_count=transient_retry_count,
                sleep_event="transient_error_sleep",
            )


def rate_limit_sleep_seconds(exc: GitHubRateLimitError, config: RemoteCodeSearchConfig) -> float:
    suggested = exc.retry_after_seconds
    if suggested is None:
        suggested = config.rate_limit_retry_sleep_seconds
    return min(max(0.0, float(suggested)), max(0.0, float(config.rate_limit_max_sleep_seconds)))


def transient_error_sleep_seconds(config: RemoteCodeSearchConfig) -> float:
    return min(
        max(0.0, float(config.transient_error_retry_sleep_seconds)),
        max(0.0, float(config.transient_error_max_sleep_seconds)),
    )


def sleep_with_progress(
    sleep_seconds: float,
    *,
    progress_callback: ProgressCallback | None,
    repo_key: str,
    operation: str,
    term: str | None,
    query: str | None,
    retry_count: int,
    sleep_event: str = "rate_limit_sleep",
) -> None:
    if sleep_seconds <= 0:
        return
    if progress_callback is None:
        time.sleep(sleep_seconds)
        return

    deadline = time.monotonic() + sleep_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        emit_scan_progress(
            progress_callback,
            sleep_event,
            repo_key=repo_key,
            operation=operation,
            term=term,
            query=query,
            retry_count=retry_count,
            sleep_remaining_sec=remaining,
        )
        time.sleep(min(1.0, remaining))


def emit_scan_progress(progress_callback: ProgressCallback | None, event: str, **payload: object) -> None:
    if progress_callback is not None:
        progress_callback({"event": event, **payload})


def extract_tree_paths(payload: dict[str, object]) -> list[str]:
    paths = []
    for item in payload.get("tree") or []:
        if isinstance(item, dict) and item.get("type") == "blob" and item.get("path"):
            paths.append(str(item["path"]))
    return sorted(set(paths))


def write_log(handle, event: str, payload: dict[str, object]) -> None:
    if handle is None:
        return
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        **payload,
    }
    handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True, default=str))
    handle.write("\n")
    handle.flush()
