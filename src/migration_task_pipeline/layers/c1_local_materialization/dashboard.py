"""Terminal dashboard for Stage C1 local repository materialization."""

from __future__ import annotations

import shutil
import sys
import threading
import time
from typing import TextIO


class TerminalDashboard:
    """Render a compact, refreshing progress dashboard to a terminal stream."""

    def __init__(self, *, stream: TextIO | None = None, refresh_interval_seconds: float = 0.5) -> None:
        self.stream = stream or sys.stderr
        self.refresh_interval_seconds = refresh_interval_seconds
        self._last_render_monotonic = 0.0
        self._line_count = 0
        self._last_snapshot: dict[str, object] = {}
        self._lock = threading.Lock()

    def __call__(self, snapshot: dict[str, object]) -> None:
        with self._lock:
            self._last_snapshot = {**self._last_snapshot, **snapshot}
            event = str(snapshot.get("event") or "")
            now = time.monotonic()
            force = event in {
                "start",
                "item_claimed",
                "clone_start",
                "item_done",
                "clone_retry_scheduled",
                "clone_permanently_failed",
                "finish",
                "error",
            }
            if not force and now - self._last_render_monotonic < self.refresh_interval_seconds:
                return
            self._last_render_monotonic = now
            self.render(self._last_snapshot)

    def render(self, snapshot: dict[str, object]) -> None:
        lines = dashboard_lines(snapshot, width=shutil.get_terminal_size((100, 20)).columns)
        if self._line_count:
            self.stream.write(f"\033[{self._line_count}A")
        for line in lines:
            self.stream.write("\033[2K")
            self.stream.write(line)
            self.stream.write("\n")
        for _ in range(max(0, self._line_count - len(lines))):
            self.stream.write("\033[2K\n")
        self._line_count = len(lines)
        self.stream.flush()

    def close(self) -> None:
        with self._lock:
            if self._line_count:
                self.stream.write("\n")
                self.stream.flush()


def dashboard_lines(snapshot: dict[str, object], *, width: int) -> list[str]:
    width = max(60, width)
    input_counts = as_counts(snapshot.get("input_status_counts"))
    output_counts = as_counts(snapshot.get("output_status_counts"))
    total = as_int(snapshot.get("total_count")) or sum(input_counts.values())
    completed = input_counts.get("done", 0) + input_counts.get("failed", 0) + input_counts.get("rejected", 0)
    elapsed = as_float(snapshot.get("elapsed_sec"))
    run_completed = as_int(snapshot.get("cloned_count")) + as_int(snapshot.get("terminal_failed_count"))
    rate_per_min = run_completed / (elapsed / 60.0) if elapsed > 0 else 0.0
    remaining = max(0, total - completed)
    eta = (remaining / rate_per_min * 60.0) if remaining > 0 and rate_per_min > 0 else None

    progress = progress_text(completed, total)
    phase = phase_text(snapshot)
    current_repo = str(snapshot.get("repo_key") or snapshot.get("current_repo_key") or "")
    worker_id = str(snapshot.get("worker_id") or "")
    attempts = as_int(snapshot.get("attempts"))
    max_attempts = as_int(snapshot.get("max_attempts"))

    lines = [
        "Layer C1 local materialization",
        f"Progress   {progress}",
        f"Elapsed    {format_duration(elapsed)}   Rate {rate_per_min:.2f} repos/min   ETA {format_duration(eta)}",
        (
            "Run        "
            f"claimed {as_int(snapshot.get('claimed_count'))}   "
            f"cloned {as_int(snapshot.get('cloned_count'))}   "
            f"failed attempts {as_int(snapshot.get('failed_count'))}   "
            f"terminal failed {as_int(snapshot.get('terminal_failed_count'))}   "
            f"enqueued {as_int(snapshot.get('enqueued_count'))}"
        ),
        (
            "Input      "
            f"pending {input_counts.get('pending', 0)}   "
            f"in_progress {input_counts.get('in_progress', 0)}   "
            f"done {input_counts.get('done', 0)}   "
            f"failed {input_counts.get('failed', 0)}"
        ),
        f"Output     pending {output_counts.get('pending', 0)}   done {output_counts.get('done', 0)}",
        fit_line(f"Current    {worker_id} {current_repo}".rstrip(), width),
        fit_line(f"Phase      {phase}", width),
    ]
    if attempts or max_attempts:
        lines.append(f"Attempts   {attempts}/{max_attempts if max_attempts else '?'}")
    if snapshot.get("error"):
        lines.append(fit_line(f"Error      {snapshot.get('error')}", width))
    return [fit_line(line, width) for line in lines]


def phase_text(snapshot: dict[str, object]) -> str:
    event = str(snapshot.get("event") or "")
    if event == "start":
        return "starting"
    if event == "item_claimed":
        return "claimed repo"
    if event == "clone_start":
        return "cloning"
    if event == "c1_to_c2_inserted":
        return "enqueued for C2"
    if event == "item_done":
        return "repo complete"
    if event == "clone_retry_scheduled":
        return "clone failed, retry queued"
    if event == "clone_permanently_failed":
        return "clone failed, max attempts reached"
    if event == "max_items_reached":
        return "max item limit reached"
    if event == "finish":
        return "finished"
    if event == "error":
        return "failed"
    return event or "running"


def progress_text(done: int, total: int) -> str:
    if total <= 0:
        return f"{done}/?"
    percent = min(100.0, max(0.0, done / total * 100.0))
    bar_width = 24
    filled = min(bar_width, int(bar_width * done / total))
    bar = "#" * filled + "-" * (bar_width - filled)
    return f"{done}/{total} ({percent:5.1f}%) [{bar}]"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h{minutes:02d}m{secs:02d}s"
    return f"{minutes:d}m{secs:02d}s"


def fit_line(line: str, width: int) -> str:
    if len(line) <= width:
        return line
    if width <= 3:
        return line[:width]
    return line[: width - 3] + "..."


def as_counts(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): as_int(count) for key, count in value.items()}


def as_int(value: object) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def as_float(value: object) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0
