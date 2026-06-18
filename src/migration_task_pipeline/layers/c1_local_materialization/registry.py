"""SQLite registry for locally materialized repositories."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from migration_task_pipeline.buffers import utc_now


@dataclass(frozen=True)
class LocalRepoRecord:
    repo_id: str
    repo_key: str
    full_name: str
    repo_url: str
    clone_url: str
    run_id: str
    buffer_item_id: str
    local_path: str
    clone_status: str
    checkout_ref: str = ""
    checkout_sha: str = ""
    clone_depth: int = 1
    submodules_enabled: bool = False
    lfs_enabled: bool = False
    disk_bytes: int = 0
    file_count: int = 0
    error_message: str = ""
    github_repo_id: str = ""
    github_node_id: str = ""


class LocalRepoRegistry:
    """Durable source of truth for Stage C1 local clone state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def upsert(self, record: LocalRepoRecord) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO local_repos (
                  repo_id, github_repo_id, github_node_id, repo_key, full_name,
                  repo_url, clone_url, run_id, buffer_item_id, local_path,
                  clone_status, checkout_ref, checkout_sha, clone_depth,
                  submodules_enabled, lfs_enabled, disk_bytes, file_count,
                  created_at, updated_at, last_checked_at, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_id) DO UPDATE SET
                  github_repo_id = excluded.github_repo_id,
                  github_node_id = excluded.github_node_id,
                  repo_key = excluded.repo_key,
                  full_name = excluded.full_name,
                  repo_url = excluded.repo_url,
                  clone_url = excluded.clone_url,
                  run_id = excluded.run_id,
                  buffer_item_id = excluded.buffer_item_id,
                  local_path = excluded.local_path,
                  clone_status = excluded.clone_status,
                  checkout_ref = excluded.checkout_ref,
                  checkout_sha = excluded.checkout_sha,
                  clone_depth = excluded.clone_depth,
                  submodules_enabled = excluded.submodules_enabled,
                  lfs_enabled = excluded.lfs_enabled,
                  disk_bytes = excluded.disk_bytes,
                  file_count = excluded.file_count,
                  updated_at = excluded.updated_at,
                  last_checked_at = excluded.last_checked_at,
                  error_message = excluded.error_message
                """,
                (
                    record.repo_id,
                    record.github_repo_id,
                    record.github_node_id,
                    record.repo_key,
                    record.full_name,
                    record.repo_url,
                    record.clone_url,
                    record.run_id,
                    record.buffer_item_id,
                    record.local_path,
                    record.clone_status,
                    record.checkout_ref,
                    record.checkout_sha,
                    int(record.clone_depth),
                    int(record.submodules_enabled),
                    int(record.lfs_enabled),
                    int(record.disk_bytes),
                    int(record.file_count),
                    now,
                    now,
                    now,
                    record.error_message,
                ),
            )

    def get(self, repo_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM local_repos WHERE repo_id = ?", (repo_id,)).fetchone()
        return dict(row) if row is not None else None

    def counts_by_status(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute("SELECT clone_status, COUNT(*) AS count FROM local_repos GROUP BY clone_status")
        return {str(row["clone_status"]): int(row["count"]) for row in rows}

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_repos (
                  repo_id TEXT PRIMARY KEY,
                  github_repo_id TEXT NOT NULL DEFAULT '',
                  github_node_id TEXT NOT NULL DEFAULT '',
                  repo_key TEXT NOT NULL,
                  full_name TEXT NOT NULL,
                  repo_url TEXT NOT NULL,
                  clone_url TEXT NOT NULL,
                  run_id TEXT NOT NULL,
                  buffer_item_id TEXT NOT NULL,
                  local_path TEXT NOT NULL,
                  clone_status TEXT NOT NULL,
                  checkout_ref TEXT NOT NULL DEFAULT '',
                  checkout_sha TEXT NOT NULL DEFAULT '',
                  clone_depth INTEGER NOT NULL DEFAULT 1,
                  submodules_enabled INTEGER NOT NULL DEFAULT 0,
                  lfs_enabled INTEGER NOT NULL DEFAULT 0,
                  disk_bytes INTEGER NOT NULL DEFAULT 0,
                  file_count INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  last_checked_at TEXT NOT NULL,
                  error_message TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_local_repos_status
                ON local_repos(clone_status)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_local_repos_repo_key
                ON local_repos(repo_key)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection
