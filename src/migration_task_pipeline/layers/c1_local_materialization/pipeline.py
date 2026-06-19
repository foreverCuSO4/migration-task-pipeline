"""Stage C1 local repository materialization pipeline."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import tempfile
import time
from typing import Any, Callable

from migration_task_pipeline.buffers import BufferItem, SQLiteBuffer
from migration_task_pipeline.github_auth import GitHubTokenPool, load_github_tokens

from .config import LayerC1Config, MaterializationConfig
from .registry import LocalRepoRecord, LocalRepoRegistry


MANIFEST_FILENAME = ".repo-manifest.json"
ProgressCallback = Callable[[dict[str, object]], None]
ProgressEmitter = Callable[..., None]


@dataclass(frozen=True)
class C1Paths:
    run_root: Path
    input_buffer: Path
    output_buffer: Path
    repo_root: Path
    registry: Path
    log_file: Path


@dataclass(frozen=True)
class C1Outputs:
    input_buffer: Path
    output_buffer: Path
    repo_root: Path
    registry: Path
    log_file: Path
    claimed_count: int
    cloned_count: int
    failed_count: int
    terminal_failed_count: int
    enqueued_count: int


class JsonlLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, event: str, payload: dict[str, object] | None = None) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **(payload or {}),
        }
        with self._lock:
            self._handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True, default=str))
            self._handle.write("\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._handle.close()


class RunCounters:
    def __init__(self, max_items: int | None) -> None:
        self.max_items = max_items
        self.claimed_count = 0
        self.cloned_count = 0
        self.failed_count = 0
        self.terminal_failed_count = 0
        self.enqueued_count = 0
        self._lock = threading.Lock()

    def can_claim(self) -> bool:
        with self._lock:
            return self.max_items is None or self.claimed_count < self.max_items

    def record_claimed(self) -> bool:
        with self._lock:
            if self.max_items is not None and self.claimed_count >= self.max_items:
                return False
            self.claimed_count += 1
            return True

    def record_cloned(self) -> None:
        with self._lock:
            self.cloned_count += 1

    def record_failed(self) -> None:
        with self._lock:
            self.failed_count += 1

    def record_terminal_failed(self) -> None:
        with self._lock:
            self.terminal_failed_count += 1

    def record_enqueued(self) -> None:
        with self._lock:
            self.enqueued_count += 1

    def snapshot(self) -> dict[str, int | None]:
        with self._lock:
            return {
                "max_items": self.max_items,
                "claimed_count": self.claimed_count,
                "cloned_count": self.cloned_count,
                "failed_count": self.failed_count,
                "terminal_failed_count": self.terminal_failed_count,
                "enqueued_count": self.enqueued_count,
            }


def run_c1_materialization(
    *,
    run_root: str | Path,
    config: LayerC1Config | None = None,
    input_buffer: str | Path | None = None,
    output_buffer: str | Path | None = None,
    repo_root: str | Path | None = None,
    registry_path: str | Path | None = None,
    log_file: str | Path | None = None,
    max_items: int | None = None,
    concurrency: int | None = None,
    auth_path: str | Path = "auth.json",
    progress_callback: ProgressCallback | None = None,
) -> C1Outputs:
    config = config or LayerC1Config()
    paths = resolve_c1_paths(
        run_root=run_root,
        input_buffer=input_buffer,
        output_buffer=output_buffer,
        repo_root=repo_root,
        registry_path=registry_path,
        log_file=log_file,
    )
    paths.repo_root.mkdir(parents=True, exist_ok=True)

    input_queue = SQLiteBuffer(paths.input_buffer)
    output_queue = SQLiteBuffer(paths.output_buffer)
    registry = LocalRepoRegistry(paths.registry)
    logger = JsonlLogger(paths.log_file)
    worker_count = max(1, int(concurrency if concurrency is not None else config.runtime.concurrency))
    item_limit = max_items if max_items is not None else config.runtime.max_items
    counters = RunCounters(item_limit)
    token_pool = load_optional_token_pool(auth_path)
    started = time.monotonic()
    total_count = sum(input_queue.counts_by_status().values())

    def emit_progress(event: str, **payload: object) -> None:
        if progress_callback is None:
            return
        progress_callback(
            {
                "event": event,
                "elapsed_sec": time.monotonic() - started,
                "total_count": total_count,
                "input_status_counts": input_queue.counts_by_status(),
                "output_status_counts": output_queue.counts_by_status(),
                **counters.snapshot(),
                **payload,
            }
        )

    logger.write(
        "c1_start",
        {
            "run_root": str(paths.run_root),
            "input_buffer": str(paths.input_buffer),
            "output_buffer": str(paths.output_buffer),
            "repo_root": str(paths.repo_root),
            "registry": str(paths.registry),
            "concurrency": worker_count,
            "max_items": item_limit,
            "clone_depth": config.materialization.clone_depth,
            "max_attempts": config.materialization.max_attempts,
            "github_token_count": len(token_pool) if token_pool is not None else 0,
            "proxy_configured": proxy_configured(config.materialization),
        },
    )
    emit_progress(
        "start",
        run_root=str(paths.run_root),
        input_buffer=str(paths.input_buffer),
        output_buffer=str(paths.output_buffer),
        repo_root=str(paths.repo_root),
        registry=str(paths.registry),
        concurrency=worker_count,
        clone_depth=config.materialization.clone_depth,
        max_attempts=config.materialization.max_attempts,
    )
    try:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(
                    worker_loop,
                    worker_id=f"c1-worker-{index + 1}",
                    run_id=paths.run_root.name,
                    input_queue=input_queue,
                    output_queue=output_queue,
                    registry=registry,
                    repo_root=paths.repo_root,
                    materialization=config.materialization,
                    token_pool=token_pool,
                    counters=counters,
                    logger=logger,
                    progress_callback=emit_progress,
                )
                for index in range(worker_count)
            ]
            for future in futures:
                future.result()
    finally:
        logger.write(
            "finish",
            {
                "claimed_count": counters.claimed_count,
                "cloned_count": counters.cloned_count,
                "failed_count": counters.failed_count,
                "terminal_failed_count": counters.terminal_failed_count,
                "enqueued_count": counters.enqueued_count,
            },
        )
        emit_progress("finish")
        logger.close()

    return C1Outputs(
        input_buffer=paths.input_buffer,
        output_buffer=paths.output_buffer,
        repo_root=paths.repo_root,
        registry=paths.registry,
        log_file=paths.log_file,
        claimed_count=counters.claimed_count,
        cloned_count=counters.cloned_count,
        failed_count=counters.failed_count,
        terminal_failed_count=counters.terminal_failed_count,
        enqueued_count=counters.enqueued_count,
    )


def worker_loop(
    *,
    worker_id: str,
    run_id: str,
    input_queue: SQLiteBuffer,
    output_queue: SQLiteBuffer,
    registry: LocalRepoRegistry,
    repo_root: Path,
    materialization: MaterializationConfig,
    token_pool: GitHubTokenPool | None,
    counters: RunCounters,
    logger: JsonlLogger,
    progress_callback: ProgressEmitter | None = None,
) -> None:
    while counters.can_claim():
        item = input_queue.claim_next(worker_id, lease_seconds=materialization.lease_seconds)
        if item is None:
            return
        if not counters.record_claimed():
            input_queue.requeue_pending(
                str(item["item_id"]),
                error="max_items_reached_after_claim",
                priority=int(item.get("priority") or materialization.retry_priority),
            )
            if progress_callback is not None:
                progress_callback(
                    "max_items_reached",
                    worker_id=worker_id,
                    item_id=item["item_id"],
                    repo_key=item["repo_key"],
                )
            return
        logger.write("item_claimed", {"worker_id": worker_id, "item_id": item["item_id"], "repo_key": item["repo_key"]})
        if progress_callback is not None:
            progress_callback(
                "item_claimed",
                worker_id=worker_id,
                item_id=item["item_id"],
                repo_key=item["repo_key"],
                attempts=item.get("attempts"),
            )
        try:
            result = materialize_item(
                item,
                run_id=run_id,
                repo_root=repo_root,
                registry=registry,
                materialization=materialization,
                token_pool=token_pool,
                logger=logger,
                worker_id=worker_id,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            error = str(exc)
            registry.upsert(
                failed_record_from_item(
                    item,
                    run_id=run_id,
                    repo_root=repo_root,
                    materialization=materialization,
                    error=error,
                )
            )
            counters.record_failed()
            logger.write(
                "clone_failed",
                {
                    "worker_id": worker_id,
                    "item_id": item["item_id"],
                    "repo_key": item["repo_key"],
                    "error": error,
                    "attempts": item.get("attempts"),
                    "max_attempts": materialization.max_attempts,
                },
            )
            retryable = should_retry_clone_failure(item, materialization)
            if retryable:
                input_queue.requeue_pending(str(item["item_id"]), error=error, priority=materialization.retry_priority)
                logger.write(
                    "clone_retry_scheduled",
                    {
                        "worker_id": worker_id,
                        "item_id": item["item_id"],
                        "repo_key": item["repo_key"],
                        "attempts": item.get("attempts"),
                        "max_attempts": materialization.max_attempts,
                    },
                )
                if progress_callback is not None:
                    progress_callback(
                        "clone_retry_scheduled",
                        worker_id=worker_id,
                        item_id=item["item_id"],
                        repo_key=item["repo_key"],
                        attempts=item.get("attempts"),
                        max_attempts=materialization.max_attempts,
                        error=error,
                    )
            else:
                input_queue.mark_failed(str(item["item_id"]), error)
                counters.record_terminal_failed()
                logger.write(
                    "clone_permanently_failed",
                    {
                        "worker_id": worker_id,
                        "item_id": item["item_id"],
                        "repo_key": item["repo_key"],
                        "attempts": item.get("attempts"),
                        "max_attempts": materialization.max_attempts,
                    },
                )
                if progress_callback is not None:
                    progress_callback(
                        "clone_permanently_failed",
                        worker_id=worker_id,
                        item_id=item["item_id"],
                        repo_key=item["repo_key"],
                        attempts=item.get("attempts"),
                        max_attempts=materialization.max_attempts,
                        error=error,
                    )
            continue

        if output_queue.insert_item(result.output_item):
            counters.record_enqueued()
            logger.write(
                "c1_to_c2_inserted",
                {"worker_id": worker_id, "item_id": result.output_item.item_id, "repo_key": item["repo_key"]},
            )
            if progress_callback is not None:
                progress_callback(
                    "c1_to_c2_inserted",
                    worker_id=worker_id,
                    item_id=result.output_item.item_id,
                    repo_key=item["repo_key"],
                )
        input_queue.mark_done(str(item["item_id"]))
        counters.record_cloned()
        logger.write(
            "item_done",
            {
                "worker_id": worker_id,
                "item_id": item["item_id"],
                "repo_key": item["repo_key"],
                "local_path": str(result.local_path),
                "checkout_sha": result.checkout_sha,
            },
        )
        if progress_callback is not None:
            progress_callback(
                "item_done",
                worker_id=worker_id,
                item_id=item["item_id"],
                repo_key=item["repo_key"],
                local_path=str(result.local_path),
                checkout_sha=result.checkout_sha,
            )


@dataclass(frozen=True)
class MaterializationResult:
    local_path: Path
    checkout_sha: str
    output_item: BufferItem


def materialize_item(
    item: dict[str, Any],
    *,
    run_id: str,
    repo_root: Path,
    registry: LocalRepoRegistry,
    materialization: MaterializationConfig,
    token_pool: GitHubTokenPool | None,
    logger: JsonlLogger,
    worker_id: str,
    progress_callback: ProgressEmitter | None = None,
) -> MaterializationResult:
    repo_id = str(item["repo_id"])
    repo_key = str(item["repo_key"])
    repo_url = str(item["repo_url"])
    clone_url = clone_url_from_item(item)
    token = token_pool.next_token() if token_pool is not None and is_github_https_url(clone_url) else None
    local_path = local_repo_path(repo_root, repo_key=repo_key, repo_url=repo_url, item_id=str(item["item_id"]))
    checkout_ref = str((item.get("payload_json") or {}).get("github_default_branch") or "")

    logger.write(
        "clone_start",
        {
            "worker_id": worker_id,
            "item_id": item["item_id"],
            "repo_key": repo_key,
            "clone_url": clone_url,
            "local_path": str(local_path),
            "auth_label": token.label if token is not None else "",
            "proxy_configured": proxy_configured(materialization),
        },
    )
    if progress_callback is not None:
        progress_callback(
            "clone_start",
            worker_id=worker_id,
            item_id=item["item_id"],
            repo_key=repo_key,
            local_path=str(local_path),
            attempts=item.get("attempts"),
            max_attempts=materialization.max_attempts,
        )
    ensure_cloned(clone_url, local_path, materialization, token_value=token.token if token is not None else "")
    checkout_sha = git_output(["git", "-C", str(local_path), "rev-parse", "--verify", "HEAD"], timeout=60)
    stats = directory_stats(local_path)
    manifest = manifest_payload(
        item,
        run_id=run_id,
        local_path=local_path,
        checkout_sha=checkout_sha,
        clone_depth=materialization.clone_depth,
    )
    write_manifest(local_path, manifest)
    registry.upsert(
        LocalRepoRecord(
            repo_id=repo_id,
            repo_key=repo_key,
            full_name=str(item.get("repo_full_name") or repo_key),
            repo_url=repo_url,
            clone_url=clone_url,
            run_id=run_id,
            buffer_item_id=str(item["item_id"]),
            local_path=str(local_path),
            clone_status="cloned",
            checkout_ref=checkout_ref,
            checkout_sha=checkout_sha,
            clone_depth=materialization.clone_depth,
            submodules_enabled=materialization.submodules,
            lfs_enabled=materialization.lfs,
            disk_bytes=stats["disk_bytes"],
            file_count=stats["file_count"],
            error_message="",
        )
    )
    output_item = build_c1_to_c2_item(
        item,
        run_id=run_id,
        local_path=local_path,
        checkout_sha=checkout_sha,
        clone_depth=materialization.clone_depth,
        disk_bytes=stats["disk_bytes"],
        file_count=stats["file_count"],
    )
    logger.write(
        "clone_done",
        {
            "worker_id": worker_id,
            "item_id": item["item_id"],
            "repo_key": repo_key,
            "checkout_sha": checkout_sha,
            "disk_bytes": stats["disk_bytes"],
            "file_count": stats["file_count"],
        },
    )
    return MaterializationResult(local_path=local_path, checkout_sha=checkout_sha, output_item=output_item)


def ensure_cloned(
    clone_url: str,
    local_path: Path,
    materialization: MaterializationConfig,
    *,
    token_value: str = "",
) -> None:
    if repository_has_valid_head(local_path):
        return
    if local_path.exists():
        shutil.rmtree(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    command = ["git", "clone", "--no-tags"]
    if materialization.clone_depth > 0:
        command.extend(["--depth", str(materialization.clone_depth)])
    if materialization.submodules:
        command.append("--recurse-submodules")
    command.extend([clone_url, str(local_path)])
    env = git_clone_env(materialization)
    if token_value:
        completed = run_git_clone_with_askpass(command, env, token_value, timeout=materialization.clone_timeout_seconds)
    else:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=materialization.clone_timeout_seconds,
            env=env,
            check=False,
        )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        detail = stderr or stdout or f"git clone exited {completed.returncode}"
        raise RuntimeError(detail)
    if not repository_has_valid_head(local_path):
        raise RuntimeError("git clone completed but repository has no valid HEAD")


def repository_has_valid_head(path: str | Path) -> bool:
    repo_path = Path(path)
    if not (repo_path / ".git").exists():
        return False
    completed = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", "HEAD"],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    return completed.returncode == 0 and bool(completed.stdout.strip())


def should_retry_clone_failure(item: dict[str, Any], materialization: MaterializationConfig) -> bool:
    attempts = as_int(item.get("attempts"))
    return attempts < materialization.max_attempts


def run_git_clone_with_askpass(command: list[str], env: dict[str, str], token_value: str, *, timeout: int):
    with tempfile.TemporaryDirectory(prefix="c1-git-askpass-") as temp_dir:
        askpass_path = Path(temp_dir) / "askpass.sh"
        askpass_path.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "*Username*) printf '%s\\n' x-access-token ;;\n"
            "*Password*) printf '%s\\n' \"$GITHUB_TOKEN_FOR_ASKPASS\" ;;\n"
            "*) printf '\\n' ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        askpass_path.chmod(0o700)
        child_env = dict(env)
        child_env["GIT_ASKPASS"] = str(askpass_path)
        child_env["GIT_TERMINAL_PROMPT"] = "0"
        child_env["GITHUB_TOKEN_FOR_ASKPASS"] = token_value
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=child_env,
            check=False,
        )


def git_clone_env(materialization: MaterializationConfig) -> dict[str, str]:
    env = os.environ.copy()
    if not materialization.lfs:
        env["GIT_LFS_SKIP_SMUDGE"] = "1"
    proxy_values = {
        "HTTP_PROXY": materialization.http_proxy,
        "HTTPS_PROXY": materialization.https_proxy,
        "ALL_PROXY": materialization.all_proxy,
        "NO_PROXY": materialization.no_proxy,
    }
    for key, value in proxy_values.items():
        if value:
            env[key] = value
            env[key.lower()] = value
    return env


def git_output(command: list[str], *, timeout: int) -> str:
    completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"{command[0]} failed")
    return completed.stdout.strip()


def build_c1_to_c2_item(
    item: dict[str, Any],
    *,
    run_id: str,
    local_path: Path,
    checkout_sha: str,
    clone_depth: int,
    disk_bytes: int,
    file_count: int,
) -> BufferItem:
    payload = {
        "repo_key": item["repo_key"],
        "repo_full_name": item["repo_full_name"],
        "repo_url": item["repo_url"],
        "local_path": str(local_path),
        "checkout_sha": checkout_sha,
        "clone_depth": clone_depth,
        "disk_bytes": disk_bytes,
        "file_count": file_count,
        "b_payload": item.get("payload_json") or {},
        "b_scores": item.get("scores_json") or {},
    }
    evidence = {
        "source_buffer_item_id": item["item_id"],
        "source_layer": item["source_layer"],
        "b_evidence": item.get("evidence_json") or {},
    }
    return BufferItem(
        item_id=str(item["item_id"]),
        repo_id=str(item["repo_id"]),
        repo_key=str(item["repo_key"]),
        repo_full_name=str(item["repo_full_name"]),
        repo_url=str(item["repo_url"]),
        source_layer="C1",
        source_run_id=run_id,
        payload_version="c1_to_c2.v1",
        payload_json=payload,
        scores_json=dict(item.get("scores_json") or {}),
        evidence_json=evidence,
        priority=int(item.get("priority") or 0),
        status="pending",
    )


def failed_record_from_item(
    item: dict[str, Any],
    *,
    run_id: str,
    repo_root: Path,
    materialization: MaterializationConfig,
    error: str,
) -> LocalRepoRecord:
    repo_key = str(item.get("repo_key") or "")
    repo_url = str(item.get("repo_url") or "")
    local_path = local_repo_path(repo_root, repo_key=repo_key, repo_url=repo_url, item_id=str(item.get("item_id") or ""))
    return LocalRepoRecord(
        repo_id=str(item.get("repo_id") or item.get("item_id") or ""),
        repo_key=repo_key,
        full_name=str(item.get("repo_full_name") or repo_key),
        repo_url=repo_url,
        clone_url=clone_url_from_item(item),
        run_id=run_id,
        buffer_item_id=str(item.get("item_id") or ""),
        local_path=str(local_path),
        clone_status="failed",
        clone_depth=materialization.clone_depth,
        submodules_enabled=materialization.submodules,
        lfs_enabled=materialization.lfs,
        error_message=error,
    )


def manifest_payload(
    item: dict[str, Any],
    *,
    run_id: str,
    local_path: Path,
    checkout_sha: str,
    clone_depth: int,
) -> dict[str, object]:
    return {
        "repo_id": item["repo_id"],
        "repo_key": item["repo_key"],
        "full_name": item["repo_full_name"],
        "repo_url": item["repo_url"],
        "checkout_sha": checkout_sha,
        "clone_depth": clone_depth,
        "run_id": run_id,
        "source_buffer": "b_to_c",
        "source_buffer_item_id": item["item_id"],
        "local_path": str(local_path),
    }


def write_manifest(local_path: Path, payload: dict[str, object]) -> None:
    manifest_path = local_path / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def directory_stats(path: Path) -> dict[str, int]:
    file_count = 0
    disk_bytes = 0
    for file_path in path.rglob("*"):
        if not file_path.is_file():
            continue
        try:
            size = file_path.stat().st_size
        except OSError:
            continue
        disk_bytes += size
        if ".git" not in file_path.relative_to(path).parts:
            file_count += 1
    return {"file_count": file_count, "disk_bytes": disk_bytes}


def clone_url_from_item(item: dict[str, Any]) -> str:
    payload = item.get("payload_json") or {}
    if isinstance(payload, dict):
        clone_url = str(payload.get("clone_url") or "").strip()
        if clone_url:
            return clone_url
    return str(item.get("repo_url") or "").strip()


def is_github_https_url(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized.startswith("https://github.com/") or normalized.startswith("http://github.com/")


def load_optional_token_pool(auth_path: str | Path) -> GitHubTokenPool | None:
    tokens = load_github_tokens(auth_path)
    if not tokens:
        return None
    return GitHubTokenPool(tokens)


def proxy_configured(materialization: MaterializationConfig) -> bool:
    return any(
        [
            materialization.http_proxy,
            materialization.https_proxy,
            materialization.all_proxy,
            materialization.no_proxy,
        ]
    )


def local_repo_path(repo_root: str | Path, *, repo_key: str, repo_url: str, item_id: str) -> Path:
    slug = repo_key_slug(repo_key)
    hash8 = stable_hash(repo_url or item_id)[:8]
    return Path(repo_root) / f"{slug}--{hash8}"


def repo_key_slug(repo_key: str) -> str:
    cleaned = []
    for char in repo_key.strip().lower().replace("/", "__"):
        if char.isalnum() or char in {"_", "-", "."}:
            cleaned.append(char)
        else:
            cleaned.append("-")
    slug = "".join(cleaned).strip("-._")
    return slug or "repo"


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def as_int(value: object) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def resolve_c1_paths(
    *,
    run_root: str | Path,
    input_buffer: str | Path | None = None,
    output_buffer: str | Path | None = None,
    repo_root: str | Path | None = None,
    registry_path: str | Path | None = None,
    log_file: str | Path | None = None,
) -> C1Paths:
    root = Path(run_root)
    date = datetime.now(UTC).strftime("%Y%m%d")
    return C1Paths(
        run_root=root,
        input_buffer=Path(input_buffer) if input_buffer is not None else root / "buffers" / "b_to_c.sqlite",
        output_buffer=Path(output_buffer) if output_buffer is not None else root / "buffers" / "c1_to_c2.sqlite",
        repo_root=Path(repo_root) if repo_root is not None else root / "repos",
        registry=Path(registry_path) if registry_path is not None else root / "state" / "local-repos.sqlite",
        log_file=Path(log_file) if log_file is not None else root / "data" / "logs" / f"c1-materialization-{date}.log",
    )
