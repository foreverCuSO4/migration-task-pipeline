"""CSV and JSONL helpers for Layer B."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Iterator

from .schema import normalize_row


def ensure_parent(path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def iter_csv(path: str | Path) -> Iterator[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def count_csv_records(path: str | Path) -> int:
    count = 0
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        for _ in reader:
            count += 1
    return count


def file_has_content(path: str | Path) -> bool:
    return Path(path).exists() and Path(path).stat().st_size > 0


def write_jsonl_row(handle, row: dict[str, object]) -> None:
    handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True, default=str))
    handle.write("\n")
    handle.flush()


def write_csv(path: str | Path, rows: Iterable[dict[str, object]], columns: list[str]) -> int:
    output_path = ensure_parent(path)
    count = 0
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(normalize_row(row, columns))
            count += 1
    return count


def write_jsonl(path: str | Path, rows: Iterable[dict[str, object]]) -> int:
    output_path = ensure_parent(path)
    count = 0
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True, default=str))
            handle.write("\n")
            count += 1
    return count
