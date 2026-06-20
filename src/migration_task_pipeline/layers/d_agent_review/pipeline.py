"""Stage D OpenCode agent review pipeline."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable, Protocol

from migration_task_pipeline.buffers import SQLiteBuffer, row_to_dict, utc_now

from .auth import load_opencode_api_key
from .config import LayerDConfig
from .opencode_runner import (
    OpenCodeCompleted,
    OpenCodeRequest,
    SubprocessOpenCodeRunner,
    build_opencode_request,
)
from .schema import parse_and_validate_review_card
from .workspace import ReviewWorkspace, create_review_workspace, repo_slug


ProgressCallback = Callable[[dict[str, object]], None]
ProgressEmitter = Callable[..., None]


@dataclass(frozen=True)
class DReviewPaths:
    run_root: Path
    input_buffer: Path
    workspace_root: Path
    logs_dir: Path
    candidate_cards_dir: Path
    stage_log_file: Path
    mace_reference_path: Path
    agent_prompt_path: Path


@dataclass(frozen=True)
class DReviewOutputs:
    input_buffer: Path
    workspace_root: Path
    logs_dir: Path
    candidate_cards_dir: Path
    stage_log_file: Path
    claimed_count: int
    reviewed_count: int
    failed_count: int
    skipped_count: int


class OpenCodeRunner(Protocol):
    def run(self, request: OpenCodeRequest) -> OpenCodeCompleted:
        ...


class JsonlLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, event: str, payload: dict[str, object] | None = None) -> None:
        record = {"ts": datetime.now(UTC).isoformat(), "event": event, **(payload or {})}
        with self._lock:
            self._handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True, default=str))
            self._handle.write("\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._handle.close()


class DualReviewLogger:
    """Per-repository JSONL and human-readable logs."""

    def __init__(self, *, jsonl_path: str | Path, text_path: str | Path) -> None:
        self.jsonl_path = Path(jsonl_path)
        self.text_path = Path(text_path)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self.text_path.parent.mkdir(parents=True, exist_ok=True)
        self._jsonl = self.jsonl_path.open("a", encoding="utf-8")
        self._text = self.text_path.open("a", encoding="utf-8")

    def write(self, event: str, payload: dict[str, object] | None = None) -> None:
        record = {"ts": datetime.now(UTC).isoformat(), "event": event, **(payload or {})}
        self._jsonl.write(json.dumps(record, ensure_ascii=True, sort_keys=True, default=str))
        self._jsonl.write("\n")
        self._jsonl.flush()
        self._text.write(format_text_log_record(record))
        self._text.write("\n")
        self._text.flush()

    def write_opencode_stream(self, *, stdout: str, stderr: str) -> None:
        for stream_name, text in (("stdout", stdout), ("stderr", stderr)):
            for index, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                parsed = parse_json_line(line)
                payload: dict[str, object]
                if parsed is None:
                    payload = {"stream": stream_name, "line_number": index, "line": line}
                else:
                    payload = {"stream": stream_name, "line_number": index, "json": parsed}
                self.write("opencode_stream", payload)

    def close(self) -> None:
        self._jsonl.close()
        self._text.close()


class RunCounters:
    def __init__(self, max_items: int | None) -> None:
        self.max_items = max_items
        self.claimed_count = 0
        self.reviewed_count = 0
        self.failed_count = 0
        self.skipped_count = 0
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

    def record_reviewed(self) -> None:
        with self._lock:
            self.reviewed_count += 1

    def record_failed(self) -> None:
        with self._lock:
            self.failed_count += 1

    def record_skipped(self) -> None:
        with self._lock:
            self.skipped_count += 1

    def snapshot(self) -> dict[str, int | None]:
        with self._lock:
            return {
                "max_items": self.max_items,
                "claimed_count": self.claimed_count,
                "reviewed_count": self.reviewed_count,
                "failed_count": self.failed_count,
                "skipped_count": self.skipped_count,
            }


def run_d_agent_review(
    *,
    run_root: str | Path,
    config: LayerDConfig | None = None,
    auth_path: str | Path = "auth.json",
    input_buffer: str | Path | None = None,
    max_items: int | None = None,
    concurrency: int | None = None,
    timeout_seconds: int | None = None,
    opencode_runner: OpenCodeRunner | None = None,
    progress_callback: ProgressCallback | None = None,
) -> DReviewOutputs:
    config = config or LayerDConfig()
    paths = resolve_d_paths(run_root=run_root, config=config, input_buffer=input_buffer)
    paths.workspace_root.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    paths.candidate_cards_dir.mkdir(parents=True, exist_ok=True)

    queue = SQLiteBuffer(paths.input_buffer)
    logger = JsonlLogger(paths.stage_log_file)
    runner = opencode_runner or SubprocessOpenCodeRunner()
    api_key = load_opencode_api_key(auth_path, provider_id=config.opencode.provider_id)
    agent_prompt = paths.agent_prompt_path.read_text(encoding="utf-8")
    worker_count = max(1, int(concurrency if concurrency is not None else config.runtime.concurrency))
    item_limit = max_items if max_items is not None else config.runtime.max_items
    timeout = max(1, int(timeout_seconds if timeout_seconds is not None else config.runtime.timeout_seconds))
    decisions = {decision.lower() for decision in config.selection.decisions}
    counters = RunCounters(item_limit)
    started = time.monotonic()
    total_count = sum(queue.counts_by_status().values())

    def emit_progress(event: str, **payload: object) -> None:
        if progress_callback is None:
            return
        progress_callback(
            {
                "event": event,
                "elapsed_sec": time.monotonic() - started,
                "total_count": total_count,
                "input_status_counts": queue.counts_by_status(),
                **counters.snapshot(),
                **payload,
            }
        )

    logger.write(
        "d_start",
        {
            "run_root": str(paths.run_root),
            "input_buffer": str(paths.input_buffer),
            "workspace_root": str(paths.workspace_root),
            "logs_dir": str(paths.logs_dir),
            "candidate_cards_dir": str(paths.candidate_cards_dir),
            "concurrency": worker_count,
            "max_items": item_limit,
            "decisions": sorted(decisions),
            "provider_id": config.opencode.provider_id,
            "model": config.opencode.model,
        },
    )
    emit_progress("start", concurrency=worker_count, max_items=item_limit, decisions=sorted(decisions))

    try:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(
                    worker_loop,
                    worker_id=f"d-worker-{index + 1}",
                    run_id=paths.run_root.name,
                    queue=queue,
                    paths=paths,
                    config=config,
                    api_key=api_key,
                    agent_prompt=agent_prompt,
                    timeout_seconds=timeout,
                    decisions=decisions,
                    counters=counters,
                    logger=logger,
                    runner=runner,
                    progress_callback=emit_progress,
                )
                for index in range(worker_count)
            ]
            for future in futures:
                future.result()
    finally:
        logger.write("d_finish", {**counters.snapshot(), "elapsed_sec": time.monotonic() - started})
        logger.close()

    return DReviewOutputs(
        input_buffer=paths.input_buffer,
        workspace_root=paths.workspace_root,
        logs_dir=paths.logs_dir,
        candidate_cards_dir=paths.candidate_cards_dir,
        stage_log_file=paths.stage_log_file,
        claimed_count=counters.claimed_count,
        reviewed_count=counters.reviewed_count,
        failed_count=counters.failed_count,
        skipped_count=counters.skipped_count,
    )


def worker_loop(
    *,
    worker_id: str,
    run_id: str,
    queue: SQLiteBuffer,
    paths: DReviewPaths,
    config: LayerDConfig,
    api_key: str,
    agent_prompt: str,
    timeout_seconds: int,
    decisions: set[str],
    counters: RunCounters,
    logger: JsonlLogger,
    runner: OpenCodeRunner,
    progress_callback: ProgressEmitter | None,
) -> None:
    while counters.can_claim():
        item = claim_next_for_decisions(
            queue.path,
            worker_id=worker_id,
            lease_seconds=config.runtime.lease_seconds,
            decisions=decisions,
        )
        if item is None:
            return
        if not counters.record_claimed():
            queue.requeue_pending(str(item["item_id"]), error="max_items_reached", priority=int(item["priority"]))
            return

        repo_key_value = str(item.get("repo_key") or "")
        logger.write("item_claimed", {"worker_id": worker_id, "item_id": item["item_id"], "repo_key": repo_key_value})
        emit(progress_callback, "item_claimed", worker_id=worker_id, item_id=item["item_id"], repo_key=repo_key_value)
        try:
            review_one_item(
                item=item,
                run_id=run_id,
                worker_id=worker_id,
                paths=paths,
                config=config,
                api_key=api_key,
                agent_prompt=agent_prompt,
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
            queue.mark_done(str(item["item_id"]))
            counters.record_reviewed()
            logger.write("item_done", {"worker_id": worker_id, "item_id": item["item_id"], "repo_key": repo_key_value})
            emit(progress_callback, "item_done", worker_id=worker_id, item_id=item["item_id"], repo_key=repo_key_value)
        except Exception as exc:
            error = str(exc)
            if int(item.get("attempts") or 0) < config.runtime.max_attempts:
                queue.requeue_pending(str(item["item_id"]), error=error, priority=int(item["priority"]))
                logger.write(
                    "item_requeued",
                    {"worker_id": worker_id, "item_id": item["item_id"], "repo_key": repo_key_value, "error": error},
                )
                emit(
                    progress_callback,
                    "item_requeued",
                    worker_id=worker_id,
                    item_id=item["item_id"],
                    repo_key=repo_key_value,
                    error=error,
                )
            else:
                queue.mark_failed(str(item["item_id"]), error)
                counters.record_failed()
                logger.write(
                    "item_failed",
                    {"worker_id": worker_id, "item_id": item["item_id"], "repo_key": repo_key_value, "error": error},
                )
                emit(
                    progress_callback,
                    "item_failed",
                    worker_id=worker_id,
                    item_id=item["item_id"],
                    repo_key=repo_key_value,
                    error=error,
                )


def review_one_item(
    *,
    item: dict[str, Any],
    run_id: str,
    worker_id: str,
    paths: DReviewPaths,
    config: LayerDConfig,
    api_key: str,
    agent_prompt: str,
    timeout_seconds: int,
    runner: OpenCodeRunner,
) -> None:
    repo_key_value = str(item.get("repo_key") or "")
    slug = repo_slug(repo_key_value)
    repo_path = resolve_repo_path(item)
    review_input = build_review_input(item=item, run_id=run_id, repo_path=repo_path)
    workspace = create_review_workspace(
        workspace_root=paths.workspace_root,
        item=item,
        repo_path=repo_path,
        mace_reference_path=paths.mace_reference_path,
        review_input=review_input,
    )
    card_path = paths.candidate_cards_dir / f"{slug}.yaml"
    jsonl_log_path = paths.logs_dir / f"{slug}.jsonl"
    text_log_path = paths.logs_dir / f"{slug}.log"
    repo_logger = DualReviewLogger(jsonl_path=jsonl_log_path, text_path=text_log_path)
    try:
        prompt = build_user_prompt(repo_key_value)
        request = build_opencode_request(
            config=config.opencode,
            api_key=api_key,
            workspace_dir=workspace.root,
            prompt_text=prompt,
            agent_prompt=agent_prompt,
            external_allow_paths=[repo_path, paths.mace_reference_path],
            timeout_seconds=timeout_seconds,
            base_env=os.environ,
        )
        repo_logger.write(
            "review_start",
            {
                "worker_id": worker_id,
                "item_id": item["item_id"],
                "repo_key": repo_key_value,
                "workspace": str(workspace.root),
                "candidate_repo": str(repo_path),
                "mace_reference": str(paths.mace_reference_path),
                "card_path": str(card_path),
                "command": request.display_command,
                "provider_id": config.opencode.provider_id,
                "model": config.opencode.model,
            },
        )
        completed = runner.run(request)
        repo_logger.write(
            "opencode_complete",
            {
                "returncode": completed.returncode,
                "stdout_bytes": len(completed.stdout.encode("utf-8")),
                "stderr_bytes": len(completed.stderr.encode("utf-8")),
            },
        )
        repo_logger.write_opencode_stream(stdout=completed.stdout, stderr=completed.stderr)
        if completed.returncode != 0:
            raise RuntimeError(f"OpenCode exited with status {completed.returncode}: {summarize_stderr(completed.stderr)}")

        final_text = extract_final_text(completed.stdout)
        card = parse_and_validate_review_card(final_text)
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card_path.write_text(card.yaml_text, encoding="utf-8")
        repo_logger.write(
            "review_card_written",
            {
                "card_path": str(card_path),
                "verdict": card.payload.get("verdict", {}).get("status"),
                "confidence": card.payload.get("verdict", {}).get("confidence"),
            },
        )
    finally:
        repo_logger.close()


def claim_next_for_decisions(
    buffer_path: str | Path,
    *,
    worker_id: str,
    lease_seconds: int,
    decisions: set[str],
) -> dict[str, Any] | None:
    now = utc_now()
    expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
    path = Path(buffer_path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT *
            FROM buffer_items
            WHERE status = 'pending'
               OR (status = 'in_progress' AND lease_expires_at != '' AND lease_expires_at < ?)
            ORDER BY priority DESC, created_at ASC, item_id ASC
            """,
            (now,),
        ).fetchall()
        selected: dict[str, Any] | None = None
        for row in rows:
            record = row_to_dict(row)
            if record is None:
                continue
            if decisions and item_decision(record) not in decisions:
                continue
            selected = record
            break
        if selected is None:
            connection.commit()
            return None
        item_id = str(selected["item_id"])
        connection.execute(
            """
            UPDATE buffer_items
            SET status = 'in_progress',
                worker_id = ?,
                leased_at = ?,
                lease_expires_at = ?,
                attempts = attempts + 1,
                updated_at = ?,
                last_error = ''
            WHERE item_id = ?
            """,
            (worker_id, now, expires, now, item_id),
        )
        updated = connection.execute("SELECT * FROM buffer_items WHERE item_id = ?", (item_id,)).fetchone()
        connection.commit()
    return row_to_dict(updated)


def item_decision(item: dict[str, Any]) -> str:
    scores = item.get("scores_json") if isinstance(item.get("scores_json"), dict) else {}
    payload = item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}
    return str(scores.get("c2_decision") or payload.get("c2_decision") or "").strip().lower()


def build_review_input(*, item: dict[str, Any], run_id: str, repo_path: Path) -> dict[str, Any]:
    return {
        "schema_version": "d_review_input.v1",
        "run_id": run_id,
        "item_id": item.get("item_id"),
        "repo_id": item.get("repo_id"),
        "repo_key": item.get("repo_key"),
        "repo_full_name": item.get("repo_full_name"),
        "repo_url": item.get("repo_url"),
        "local_path": str(repo_path),
        "source_layer": item.get("source_layer"),
        "source_run_id": item.get("source_run_id"),
        "payload": item.get("payload_json") or {},
        "scores": item.get("scores_json") or {},
        "evidence": item.get("evidence_json") or {},
        "output_schema": "g4_review.v1",
        "required_verdicts": ["pilot", "hold", "reject"],
    }


def resolve_repo_path(item: dict[str, Any]) -> Path:
    payload = item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}
    local_path = payload.get("local_path") if isinstance(payload, dict) else ""
    if not local_path:
        evidence = item.get("evidence_json") if isinstance(item.get("evidence_json"), dict) else {}
        c2_evidence = evidence.get("c2_evidence") if isinstance(evidence.get("c2_evidence"), dict) else {}
        local_path = c2_evidence.get("local_path", "")
    if not local_path:
        raise ValueError(f"Missing local_path for item {item.get('item_id')}")
    return Path(str(local_path)).resolve()


def resolve_d_paths(
    *,
    run_root: str | Path,
    config: LayerDConfig,
    input_buffer: str | Path | None = None,
) -> DReviewPaths:
    root = Path(run_root)
    date = datetime.now(UTC).strftime("%Y%m%d")
    candidate_run_name = config.paths.card_run_name.replace("{date}", date).replace("{run}", root.name)
    return DReviewPaths(
        run_root=root,
        input_buffer=Path(input_buffer) if input_buffer is not None else root / "buffers" / "c2_to_d.sqlite",
        workspace_root=resolve_run_relative(root, config.paths.workspace_root),
        logs_dir=resolve_run_relative(root, config.paths.logs_dir),
        candidate_cards_dir=Path(config.paths.candidate_cards_root) / candidate_run_name,
        stage_log_file=resolve_run_relative(root, config.paths.logs_dir) / f"d-review-{date}.log",
        mace_reference_path=Path(config.paths.mace_reference_path),
        agent_prompt_path=Path(config.opencode.agent_prompt_path),
    )


def resolve_run_relative(run_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else run_root / path


def build_user_prompt(repo_key_value: str) -> str:
    return (
        f"Review candidate repository {repo_key_value}. "
        "Read review-input.json first, then inspect candidate_repo/ and mace_reference/. "
        "Output only one YAML object matching schema_version: g4_review.v1."
    )


def extract_final_text(stdout: str) -> str:
    candidates: list[str] = []
    for line in stdout.splitlines():
        parsed = parse_json_line(line)
        if parsed is None:
            if line.strip():
                candidates.append(line)
            continue
        candidates.extend(find_text_values(parsed))
    for candidate in reversed(candidates):
        if "schema_version:" in candidate:
            return candidate
    return "\n".join(candidates).strip() or stdout.strip()


def parse_json_line(line: str) -> Any | None:
    try:
        return json.loads(line)
    except Exception:
        return None


def find_text_values(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, str):
        if value.strip():
            result.append(value)
    elif isinstance(value, list):
        for item in value:
            result.extend(find_text_values(item))
    elif isinstance(value, dict):
        for key in ("output", "message", "content", "text", "final", "result", "data"):
            if key in value:
                result.extend(find_text_values(value[key]))
        if not result:
            for item in value.values():
                result.extend(find_text_values(item))
    return result


def summarize_stderr(stderr: str) -> str:
    text = " ".join(line.strip() for line in stderr.splitlines() if line.strip())
    if not text:
        return "no stderr"
    return text[:500]


def format_text_log_record(record: dict[str, object]) -> str:
    ts = record.get("ts", "")
    event = record.get("event", "")
    payload = {key: value for key, value in record.items() if key not in {"ts", "event"}}
    return f"[{ts}] {event}\n{json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True, default=str)}\n"


def emit(progress_callback: ProgressEmitter | None, event: str, **payload: object) -> None:
    if progress_callback is not None:
        progress_callback(event, **payload)
