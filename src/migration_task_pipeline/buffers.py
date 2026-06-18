"""Persistent SQLite-backed pipeline buffers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any


BUFFER_STATUSES = {"pending", "in_progress", "done", "failed", "rejected"}


@dataclass(frozen=True)
class BufferItem:
    item_id: str
    repo_id: str
    repo_key: str
    repo_full_name: str
    repo_url: str
    source_layer: str
    source_run_id: str
    payload_version: str
    payload_json: dict[str, Any] = field(default_factory=dict)
    scores_json: dict[str, Any] = field(default_factory=dict)
    evidence_json: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    status: str = "pending"
    attempts: int = 0
    worker_id: str = ""
    leased_at: str = ""
    lease_expires_at: str = ""
    last_error: str = ""


class SQLiteBuffer:
    """Durable work buffer with lease-based claiming."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def insert_item(self, item: BufferItem) -> bool:
        validate_status(item.status)
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO buffer_items (
                  item_id, repo_id, repo_key, repo_full_name, repo_url,
                  source_layer, source_run_id, payload_version,
                  payload_json, scores_json, evidence_json,
                  priority, status, attempts, worker_id,
                  leased_at, lease_expires_at, created_at, updated_at, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.item_id,
                    item.repo_id,
                    item.repo_key,
                    item.repo_full_name,
                    item.repo_url,
                    item.source_layer,
                    item.source_run_id,
                    item.payload_version,
                    json_text(item.payload_json),
                    json_text(item.scores_json),
                    json_text(item.evidence_json),
                    int(item.priority),
                    item.status,
                    int(item.attempts),
                    item.worker_id,
                    item.leased_at,
                    item.lease_expires_at,
                    now,
                    now,
                    item.last_error,
                ),
            )
            return cursor.rowcount == 1

    def has_item(self, item_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT 1 FROM buffer_items WHERE item_id = ?", (item_id,)).fetchone()
        return row is not None

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM buffer_items WHERE item_id = ?", (item_id,)).fetchone()
        return row_to_dict(row)

    def counts_by_status(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM buffer_items GROUP BY status").fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def claim_next(self, worker_id: str, lease_seconds: int = 900) -> dict[str, Any] | None:
        now = utc_now()
        expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM buffer_items
                WHERE status = 'pending'
                   OR (status = 'in_progress' AND lease_expires_at != '' AND lease_expires_at < ?)
                ORDER BY priority DESC, created_at ASC, item_id ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            item_id = str(row["item_id"])
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

    def mark_done(self, item_id: str) -> None:
        self._mark_status(item_id, status="done", last_error="")

    def mark_failed(self, item_id: str, error: str) -> None:
        self._mark_status(item_id, status="failed", last_error=error)

    def mark_rejected(self, item_id: str, reason: str) -> None:
        self._mark_status(item_id, status="rejected", last_error=reason)

    def requeue_pending(self, item_id: str, *, error: str, priority: int = 0) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE buffer_items
                SET status = 'pending',
                    priority = ?,
                    worker_id = '',
                    leased_at = '',
                    lease_expires_at = '',
                    created_at = ?,
                    updated_at = ?,
                    last_error = ?
                WHERE item_id = ?
                """,
                (int(priority), now, now, error, item_id),
            )

    def _mark_status(self, item_id: str, *, status: str, last_error: str) -> None:
        validate_status(status)
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE buffer_items
                SET status = ?,
                    worker_id = '',
                    leased_at = '',
                    lease_expires_at = '',
                    updated_at = ?,
                    last_error = ?
                WHERE item_id = ?
                """,
                (status, now, last_error, item_id),
            )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS buffer_items (
                  item_id TEXT PRIMARY KEY,
                  repo_id TEXT NOT NULL,
                  repo_key TEXT NOT NULL,
                  repo_full_name TEXT NOT NULL,
                  repo_url TEXT NOT NULL,
                  source_layer TEXT NOT NULL,
                  source_run_id TEXT NOT NULL,
                  payload_version TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  scores_json TEXT NOT NULL,
                  evidence_json TEXT NOT NULL,
                  priority INTEGER NOT NULL DEFAULT 0,
                  status TEXT NOT NULL,
                  attempts INTEGER NOT NULL DEFAULT 0,
                  worker_id TEXT NOT NULL DEFAULT '',
                  leased_at TEXT NOT NULL DEFAULT '',
                  lease_expires_at TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_buffer_items_status_priority
                ON buffer_items(status, priority DESC, created_at ASC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_buffer_items_repo_key
                ON buffer_items(repo_key)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def validate_status(status: str) -> None:
    if status not in BUFFER_STATUSES:
        raise ValueError(f"Invalid buffer item status: {status}")


def json_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    record = dict(row)
    for key in ("payload_json", "scores_json", "evidence_json"):
        record[key] = json.loads(str(record[key] or "{}"))
    return record


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
