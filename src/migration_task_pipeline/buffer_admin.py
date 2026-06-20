"""Administrative helpers for inspecting and resetting pipeline buffers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
from typing import Any

from migration_task_pipeline.buffers import BUFFER_STATUSES, row_to_dict, utc_now


KNOWN_BUFFERS = {
    "b_to_c": Path("buffers/b_to_c.sqlite"),
    "c1_to_c2": Path("buffers/c1_to_c2.sqlite"),
    "c2_to_d": Path("buffers/c2_to_d.sqlite"),
}


@dataclass(frozen=True)
class BufferItemSelector:
    statuses: set[str] = field(default_factory=set)
    repo_keys: set[str] = field(default_factory=set)
    item_ids: set[str] = field(default_factory=set)
    decisions: set[str] = field(default_factory=set)
    repo_contains: str = ""
    limit: int | None = None


def resolve_buffer_path(
    *,
    run_root: str | Path | None = None,
    buffer_name: str | None = None,
    buffer_path: str | Path | None = None,
) -> Path:
    if buffer_path is not None:
        return Path(buffer_path)
    if run_root is None:
        raise ValueError("--run-root is required when --buffer-path is not provided")
    name = (buffer_name or "c2_to_d").strip()
    if name not in KNOWN_BUFFERS:
        valid = ", ".join(sorted(KNOWN_BUFFERS))
        raise ValueError(f"Unknown buffer name '{name}'. Use one of: {valid}")
    return Path(run_root) / KNOWN_BUFFERS[name]


def counts_by_status(path: str | Path) -> dict[str, int]:
    with connect(path) as connection:
        rows = connection.execute("SELECT status, COUNT(*) AS count FROM buffer_items GROUP BY status").fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def counts_by_status_and_decision(path: str | Path) -> list[dict[str, object]]:
    rows = iter_buffer_items(path)
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (str(row.get("status") or ""), item_decision(row))
        counts[key] = counts.get(key, 0) + 1
    return [
        {"status": status, "decision": decision, "count": count}
        for (status, decision), count in sorted(counts.items())
    ]


def iter_buffer_items(path: str | Path) -> list[dict[str, Any]]:
    with connect(path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM buffer_items
            ORDER BY priority DESC, created_at ASC, item_id ASC
            """
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = row_to_dict(row)
        if item is not None:
            items.append(item)
    return items


def select_buffer_items(path: str | Path, selector: BufferItemSelector) -> list[dict[str, Any]]:
    validate_selector(selector)
    result: list[dict[str, Any]] = []
    for item in iter_buffer_items(path):
        if not item_matches(item, selector):
            continue
        result.append(item)
        if selector.limit is not None and len(result) >= selector.limit:
            break
    return result


def reset_buffer_items(
    path: str | Path,
    item_ids: list[str],
    *,
    reset_attempts: bool = True,
    last_error: str = "reset_by_buffer_admin",
) -> int:
    if not item_ids:
        return 0
    now = utc_now()
    placeholders = ",".join("?" for _ in item_ids)
    attempts_sql = "attempts = 0," if reset_attempts else ""
    with connect(path) as connection:
        cursor = connection.execute(
            f"""
            UPDATE buffer_items
            SET status = 'pending',
                {attempts_sql}
                worker_id = '',
                leased_at = '',
                lease_expires_at = '',
                created_at = ?,
                updated_at = ?,
                last_error = ?
            WHERE item_id IN ({placeholders})
            """,
            (now, now, last_error, *item_ids),
        )
    return int(cursor.rowcount)


def item_matches(item: dict[str, Any], selector: BufferItemSelector) -> bool:
    if selector.statuses and str(item.get("status") or "") not in selector.statuses:
        return False
    if selector.repo_keys and str(item.get("repo_key") or "") not in selector.repo_keys:
        return False
    if selector.item_ids and str(item.get("item_id") or "") not in selector.item_ids:
        return False
    if selector.decisions and item_decision(item) not in selector.decisions:
        return False
    if selector.repo_contains:
        needle = selector.repo_contains.lower()
        if needle not in str(item.get("repo_key") or "").lower():
            return False
    return True


def item_decision(item: dict[str, Any]) -> str:
    scores = item.get("scores_json") if isinstance(item.get("scores_json"), dict) else {}
    payload = item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}
    return str(scores.get("c2_decision") or payload.get("c2_decision") or "").strip().lower()


def validate_selector(selector: BufferItemSelector) -> None:
    unknown_statuses = selector.statuses - BUFFER_STATUSES
    if unknown_statuses:
        raise ValueError(f"Unknown buffer statuses: {', '.join(sorted(unknown_statuses))}")
    if selector.limit is not None and selector.limit < 1:
        raise ValueError("limit must be positive")


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    if not db_path.exists():
        raise FileNotFoundError(f"Buffer database does not exist: {db_path}")
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection
