"""Stage C2 local heuristic screening pipeline."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import threading
import time
from typing import Callable

from migration_task_pipeline.buffers import BufferItem, SQLiteBuffer

from .config import LayerC2Config, LocalScannerConfig
from .scanner import scan_repository
from .schema import C2_CANDIDATE_COLUMNS, normalize_row
from .scoring import score_repository


C2_TO_D_DECISIONS = {"promote", "maybe"}
ProgressCallback = Callable[[dict[str, object]], None]
ProgressEmitter = Callable[..., None]


@dataclass(frozen=True)
class C2Paths:
    run_root: Path
    input_buffer: Path
    output_buffer: Path
    evidence_jsonl: Path
    candidates_csv: Path
    log_file: Path


@dataclass(frozen=True)
class C2Outputs:
    input_buffer: Path
    output_buffer: Path
    evidence_jsonl: Path
    candidates_csv: Path
    log_file: Path
    claimed_count: int
    promoted_count: int
    maybe_count: int
    rejected_count: int
    failed_count: int
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


class ArtifactWriter:
    def __init__(self, *, evidence_jsonl: str | Path, candidates_csv: str | Path) -> None:
        self.evidence_jsonl = Path(evidence_jsonl)
        self.candidates_csv = Path(candidates_csv)
        self.evidence_jsonl.parent.mkdir(parents=True, exist_ok=True)
        self.candidates_csv.parent.mkdir(parents=True, exist_ok=True)
        self._evidence_handle = self.evidence_jsonl.open("a", encoding="utf-8")
        self._csv_handle = self.candidates_csv.open("a", encoding="utf-8", newline="")
        self._csv_writer = csv.DictWriter(self._csv_handle, fieldnames=C2_CANDIDATE_COLUMNS, extrasaction="ignore")
        if not file_has_content(self.candidates_csv):
            self._csv_writer.writeheader()
            self._csv_handle.flush()
        self._lock = threading.Lock()

    def write(self, evidence: dict[str, object], scored: dict[str, object]) -> None:
        evidence_row = dict(evidence)
        evidence_row["scores"] = scored
        with self._lock:
            self._evidence_handle.write(json.dumps(evidence_row, ensure_ascii=True, sort_keys=True, default=str))
            self._evidence_handle.write("\n")
            self._evidence_handle.flush()
            self._csv_writer.writerow(normalize_row(scored, C2_CANDIDATE_COLUMNS))
            self._csv_handle.flush()

    def close(self) -> None:
        with self._lock:
            self._evidence_handle.close()
            self._csv_handle.close()


class RunCounters:
    def __init__(self, max_items: int | None) -> None:
        self.max_items = max_items
        self.claimed_count = 0
        self.promoted_count = 0
        self.maybe_count = 0
        self.rejected_count = 0
        self.failed_count = 0
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

    def record_decision(self, decision: str) -> None:
        with self._lock:
            if decision == "promote":
                self.promoted_count += 1
            elif decision == "maybe":
                self.maybe_count += 1
            elif decision == "reject":
                self.rejected_count += 1

    def record_failed(self) -> None:
        with self._lock:
            self.failed_count += 1

    def record_enqueued(self) -> None:
        with self._lock:
            self.enqueued_count += 1

    def snapshot(self) -> dict[str, int | None]:
        with self._lock:
            return {
                "max_items": self.max_items,
                "claimed_count": self.claimed_count,
                "promoted_count": self.promoted_count,
                "maybe_count": self.maybe_count,
                "rejected_count": self.rejected_count,
                "failed_count": self.failed_count,
                "enqueued_count": self.enqueued_count,
            }


def run_c2_local_screening(
    *,
    run_root: str | Path,
    config: LayerC2Config | None = None,
    input_buffer: str | Path | None = None,
    output_buffer: str | Path | None = None,
    evidence_jsonl: str | Path | None = None,
    candidates_csv: str | Path | None = None,
    log_file: str | Path | None = None,
    max_items: int | None = None,
    concurrency: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> C2Outputs:
    config = config or LayerC2Config()
    paths = resolve_c2_paths(
        run_root=run_root,
        input_buffer=input_buffer,
        output_buffer=output_buffer,
        evidence_jsonl=evidence_jsonl,
        candidates_csv=candidates_csv,
        log_file=log_file,
    )
    input_queue = SQLiteBuffer(paths.input_buffer)
    output_queue = SQLiteBuffer(paths.output_buffer)
    writer = ArtifactWriter(evidence_jsonl=paths.evidence_jsonl, candidates_csv=paths.candidates_csv)
    logger = JsonlLogger(paths.log_file)
    worker_count = max(1, int(concurrency if concurrency is not None else config.runtime.concurrency))
    item_limit = max_items if max_items is not None else config.runtime.max_items
    counters = RunCounters(item_limit)
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
        "c2_start",
        {
            "run_root": str(paths.run_root),
            "input_buffer": str(paths.input_buffer),
            "output_buffer": str(paths.output_buffer),
            "evidence_jsonl": str(paths.evidence_jsonl),
            "candidates_csv": str(paths.candidates_csv),
            "concurrency": worker_count,
            "max_items": item_limit,
            "scanner": scanner_config_payload(config.scanner),
            "total_count": total_count,
        },
    )
    emit_progress(
        "start",
        run_root=str(paths.run_root),
        input_buffer=str(paths.input_buffer),
        output_buffer=str(paths.output_buffer),
        evidence_jsonl=str(paths.evidence_jsonl),
        candidates_csv=str(paths.candidates_csv),
        concurrency=worker_count,
    )
    try:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(
                    worker_loop,
                    worker_id=f"c2-worker-{index + 1}",
                    run_id=paths.run_root.name,
                    input_queue=input_queue,
                    output_queue=output_queue,
                    writer=writer,
                    logger=logger,
                    config=config,
                    counters=counters,
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
                "promoted_count": counters.promoted_count,
                "maybe_count": counters.maybe_count,
                "rejected_count": counters.rejected_count,
                "failed_count": counters.failed_count,
                "enqueued_count": counters.enqueued_count,
            },
        )
        emit_progress("finish")
        writer.close()
        logger.close()

    return C2Outputs(
        input_buffer=paths.input_buffer,
        output_buffer=paths.output_buffer,
        evidence_jsonl=paths.evidence_jsonl,
        candidates_csv=paths.candidates_csv,
        log_file=paths.log_file,
        claimed_count=counters.claimed_count,
        promoted_count=counters.promoted_count,
        maybe_count=counters.maybe_count,
        rejected_count=counters.rejected_count,
        failed_count=counters.failed_count,
        enqueued_count=counters.enqueued_count,
    )


def worker_loop(
    *,
    worker_id: str,
    run_id: str,
    input_queue: SQLiteBuffer,
    output_queue: SQLiteBuffer,
    writer: ArtifactWriter,
    logger: JsonlLogger,
    config: LayerC2Config,
    counters: RunCounters,
    progress_callback: ProgressEmitter | None = None,
) -> None:
    while counters.can_claim():
        item = input_queue.claim_next(worker_id, lease_seconds=config.runtime.lease_seconds)
        if item is None:
            return
        if not counters.record_claimed():
            input_queue.requeue_pending(
                str(item["item_id"]),
                error="max_items_reached_after_claim",
                priority=int(item.get("priority") or 0),
            )
            emit(progress_callback, "max_items_reached", worker_id=worker_id, item_id=item["item_id"])
            return

        repo_key = str(item.get("repo_key") or "")
        logger.write("item_claimed", {"worker_id": worker_id, "item_id": item["item_id"], "repo_key": repo_key})
        emit(progress_callback, "item_claimed", worker_id=worker_id, item_id=item["item_id"], repo_key=repo_key)
        try:
            evidence = scan_repository(item, config.scanner)
            scored = score_repository(item, evidence, config=config.scoring)
            writer.write(evidence, scored)
            decision = str(scored.get("c2_decision") or "")
            logger.write(
                "repo_scored",
                {
                    "worker_id": worker_id,
                    "item_id": item["item_id"],
                    "repo_key": repo_key,
                    "decision": decision,
                    "c2_score": scored.get("c2_score"),
                    "reasons": scored.get("c2_reasons", []),
                },
            )
            emit(
                progress_callback,
                "repo_scanned",
                worker_id=worker_id,
                item_id=item["item_id"],
                repo_key=repo_key,
                decision=decision,
                c2_score=scored.get("c2_score"),
            )
            if decision in C2_TO_D_DECISIONS:
                if output_queue.insert_item(build_c2_to_d_item(item, scored, evidence, source_run_id=run_id)):
                    counters.record_enqueued()
                input_queue.mark_done(str(item["item_id"]))
                counters.record_decision(decision)
                emit(
                    progress_callback,
                    "item_done",
                    worker_id=worker_id,
                    item_id=item["item_id"],
                    repo_key=repo_key,
                    decision=decision,
                    c2_score=scored.get("c2_score"),
                )
            else:
                reason = ";".join(str(reason) for reason in scored.get("c2_reasons") or [])
                input_queue.mark_rejected(str(item["item_id"]), reason)
                counters.record_decision("reject")
                emit(
                    progress_callback,
                    "item_rejected",
                    worker_id=worker_id,
                    item_id=item["item_id"],
                    repo_key=repo_key,
                    decision=decision,
                    c2_score=scored.get("c2_score"),
                )
        except Exception as exc:
            error = str(exc)
            input_queue.mark_failed(str(item["item_id"]), error)
            counters.record_failed()
            logger.write(
                "item_failed",
                {"worker_id": worker_id, "item_id": item["item_id"], "repo_key": repo_key, "error": error},
            )
            emit(
                progress_callback,
                "item_failed",
                worker_id=worker_id,
                item_id=item["item_id"],
                repo_key=repo_key,
                error=error,
            )


def build_c2_to_d_item(
    item: dict[str, object],
    scored: dict[str, object],
    evidence: dict[str, object],
    *,
    source_run_id: str,
) -> BufferItem:
    payload = item.get("payload_json") or {}
    output_payload = {
        "repo_key": item["repo_key"],
        "repo_full_name": item["repo_full_name"],
        "repo_url": item["repo_url"],
        "local_path": evidence.get("local_path", ""),
        "checkout_sha": evidence.get("checkout_sha", ""),
        "disk_bytes": evidence.get("disk_bytes", ""),
        "file_count": evidence.get("file_count", ""),
        "c2_decision": scored.get("c2_decision", ""),
        "c2_reasons": scored.get("c2_reasons", []),
        "c1_payload": payload if isinstance(payload, dict) else {},
    }
    output_evidence = {
        "source_buffer_item_id": item["item_id"],
        "source_layer": item["source_layer"],
        "c2_evidence": evidence,
    }
    return BufferItem(
        item_id=str(item["item_id"]),
        repo_id=str(item["repo_id"]),
        repo_key=str(item["repo_key"]),
        repo_full_name=str(item["repo_full_name"]),
        repo_url=str(item["repo_url"]),
        source_layer="C2",
        source_run_id=source_run_id,
        payload_version="c2_to_d.v1",
        payload_json=output_payload,
        scores_json=dict(scored),
        evidence_json=output_evidence,
        priority=c2_to_d_priority(scored),
        status="pending",
    )


def c2_to_d_priority(scored: dict[str, object]) -> int:
    decision = str(scored.get("c2_decision") or "").strip().lower()
    base = 300_000 if decision == "promote" else 150_000
    return base + round(as_float(scored.get("c2_score")) * 10_000)


def resolve_c2_paths(
    *,
    run_root: str | Path,
    input_buffer: str | Path | None = None,
    output_buffer: str | Path | None = None,
    evidence_jsonl: str | Path | None = None,
    candidates_csv: str | Path | None = None,
    log_file: str | Path | None = None,
) -> C2Paths:
    root = Path(run_root)
    date = datetime.now(UTC).strftime("%Y%m%d")
    return C2Paths(
        run_root=root,
        input_buffer=Path(input_buffer) if input_buffer is not None else root / "buffers" / "c1_to_c2.sqlite",
        output_buffer=Path(output_buffer) if output_buffer is not None else root / "buffers" / "c2_to_d.sqlite",
        evidence_jsonl=(
            Path(evidence_jsonl)
            if evidence_jsonl is not None
            else root / "data" / "interim" / f"local-heuristic-evidence-{date}.jsonl"
        ),
        candidates_csv=(
            Path(candidates_csv)
            if candidates_csv is not None
            else root / "data" / "processed" / "repo-candidates-c2.csv"
        ),
        log_file=Path(log_file) if log_file is not None else root / "data" / "logs" / f"c2-local-screening-{date}.log",
    )


def scanner_config_payload(config: LocalScannerConfig) -> dict[str, object]:
    return {
        "max_file_size_bytes": config.max_file_size_bytes,
        "max_files_per_repo": config.max_files_per_repo,
        "max_repo_bytes": config.max_repo_bytes,
        "max_hits_per_repo": config.max_hits_per_repo,
        "max_paths_per_group": config.max_paths_per_group,
        "skip_dirs": config.skip_dirs,
    }


def emit(progress_callback: ProgressEmitter | None, event: str, **payload: object) -> None:
    if progress_callback is not None:
        progress_callback(event, **payload)


def file_has_content(path: str | Path) -> bool:
    return Path(path).exists() and Path(path).stat().st_size > 0


def as_float(value: object) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0

