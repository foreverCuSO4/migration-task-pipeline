"""Layer B remote GitHub code-search screening pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import csv
import json
import time
from typing import Callable

from .config import RemoteCodeSearchConfig
from .github_client import GitHubAccessError, GitHubRateLimitError, GitHubRemoteClient
from .io import count_csv_records, ensure_parent, file_has_content, iter_csv, write_jsonl_row
from .schema import B_CANDIDATE_COLUMNS, normalize_row
from .scoring import CodeHit, score_repository


class RemoteScanIncomplete(RuntimeError):
    """Raised when Layer B cannot collect complete remote evidence."""


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
) -> RemoteScreeningOutputs:
    config = config or RemoteCodeSearchConfig()
    client = github_client or GitHubRemoteClient.from_env(auth_path=auth_path)
    run_date = run_date or datetime.now(UTC).strftime("%Y%m%d")
    output_root = Path(output_root)

    signals_path = output_root / "interim" / f"github-code-signals-{run_date}.jsonl"
    candidates_path = output_root / "processed" / "repo-candidates-b.csv"
    log_path = output_root / "logs" / f"remote-code-screening-{run_date}.log"
    ensure_parent(signals_path)
    ensure_parent(candidates_path)
    ensure_parent(log_path)

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
                "total_count": total_count,
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
                    "elapsed_sec": round(repo_elapsed, 3),
                    "errors": scored.get("b_errors", []),
                },
            )
            emit_progress(
                "repo_done",
                decision=decision,
                b_score=scored.get("b_score"),
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
    )


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
            tree_payload = call_with_rate_limit_retry(
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
            payload = call_with_rate_limit_retry(
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


def call_with_rate_limit_retry(
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
    retry_count = 0
    while True:
        try:
            return operation_call()
        except GitHubRateLimitError as exc:
            retry_count += 1
            if config.rate_limit_max_retries is not None and retry_count > config.rate_limit_max_retries:
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
                    "retry_count": retry_count,
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
                retry_count=retry_count,
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
                retry_count=retry_count,
            )


def rate_limit_sleep_seconds(exc: GitHubRateLimitError, config: RemoteCodeSearchConfig) -> float:
    suggested = exc.retry_after_seconds
    if suggested is None:
        suggested = config.rate_limit_retry_sleep_seconds
    return min(max(0.0, float(suggested)), max(0.0, float(config.rate_limit_max_sleep_seconds)))


def sleep_with_progress(
    sleep_seconds: float,
    *,
    progress_callback: ProgressCallback | None,
    repo_key: str,
    operation: str,
    term: str | None,
    query: str | None,
    retry_count: int,
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
            "rate_limit_sleep",
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
