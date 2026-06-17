"""Layer B remote GitHub code-search screening pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import csv
import json
import time

from .config import RemoteCodeSearchConfig
from .github_client import GitHubRemoteClient
from .io import ensure_parent, iter_csv, write_jsonl_row
from .schema import B_CANDIDATE_COLUMNS, normalize_row
from .scoring import CodeHit, score_repository


@dataclass(frozen=True)
class RemoteScreeningOutputs:
    signals_jsonl: Path
    candidates_csv: Path
    log_file: Path
    scanned_count: int
    promoted_count: int
    maybe_count: int
    rejected_count: int


def run_remote_code_screening(
    seed_csv: str | Path,
    *,
    output_root: str | Path,
    run_date: str | None = None,
    auth_path: str | Path = "auth.json",
    github_client: GitHubRemoteClient | None = None,
    config: RemoteCodeSearchConfig | None = None,
    limit: int | None = None,
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

    scanned_count = 0
    promoted_count = 0
    maybe_count = 0
    rejected_count = 0
    started = time.monotonic()

    with (
        signals_path.open("w", encoding="utf-8") as signals_handle,
        candidates_path.open("w", encoding="utf-8", newline="") as csv_handle,
        log_path.open("w", encoding="utf-8") as log_handle,
    ):
        writer = csv.DictWriter(csv_handle, fieldnames=B_CANDIDATE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        csv_handle.flush()
        write_log(
            log_handle,
            "start",
            {
                "seed_csv": str(seed_csv),
                "output_root": str(output_root),
                "limit": limit,
                "per_page": config.per_page,
                "max_code_queries_per_repo": config.max_code_queries_per_repo,
                "use_remote_tree": config.use_remote_tree,
            },
        )

        for index, seed_row in enumerate(iter_csv(seed_csv), start=1):
            if limit is not None and scanned_count >= limit:
                write_log(log_handle, "limit_reached", {"limit": limit})
                break

            repo_key = str(seed_row.get("repo_key") or "")
            write_log(log_handle, "repo_start", {"index": index, "repo_key": repo_key})
            repo_started = time.monotonic()
            evidence = scan_seed_row(seed_row, client=client, config=config, log_handle=log_handle)
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

            scanned_count += 1
            decision = scored.get("b_decision")
            if decision == "promote":
                promoted_count += 1
            elif decision == "maybe":
                maybe_count += 1
            elif decision == "reject":
                rejected_count += 1
            write_log(
                log_handle,
                "repo_done",
                {
                    "index": index,
                    "repo_key": repo_key,
                    "decision": decision,
                    "b_score": scored.get("b_score"),
                    "elapsed_sec": round(time.monotonic() - repo_started, 3),
                    "errors": scored.get("b_errors", []),
                },
            )

        write_log(
            log_handle,
            "finish",
            {
                "scanned_count": scanned_count,
                "promoted_count": promoted_count,
                "maybe_count": maybe_count,
                "rejected_count": rejected_count,
                "elapsed_sec": round(time.monotonic() - started, 3),
            },
        )

    return RemoteScreeningOutputs(
        signals_jsonl=signals_path,
        candidates_csv=candidates_path,
        log_file=log_path,
        scanned_count=scanned_count,
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
            tree_payload = client.get_tree(repo_key, default_branch)
            tree_paths = extract_tree_paths(tree_payload)
            if tree_payload.get("truncated"):
                errors.append("tree_truncated")
            write_log(log_handle, "tree_done", {"repo_key": repo_key, "path_count": len(tree_paths)})
        except Exception as exc:
            errors.append(f"tree_error:{exc}")
            write_log(log_handle, "tree_error", {"repo_key": repo_key, "error": str(exc)})

    for query_spec in config.code_queries[: config.max_code_queries_per_repo]:
        query = query_spec.github_query(repo_key)
        try:
            payload = client.search_code(query, per_page=config.per_page, page=1)
        except Exception as exc:
            errors.append(f"code_search_error:{query_spec.term}:{exc}")
            write_log(
                log_handle,
                "code_search_error",
                {"repo_key": repo_key, "term": query_spec.term, "query": query, "error": str(exc)},
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

    return {
        "repo_key": repo_key,
        "repo_url": seed_row.get("repo_url", ""),
        "github_default_branch": default_branch,
        "tree_paths": tree_paths,
        "code_hits": code_hits,
        "errors": errors,
    }


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
