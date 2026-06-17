"""Terminal dashboard for Layer B screening progress."""

from __future__ import annotations

import shutil
import sys
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

    def __call__(self, snapshot: dict[str, object]) -> None:
        self._last_snapshot = {**self._last_snapshot, **snapshot}
        event = str(snapshot.get("event") or "")
        now = time.monotonic()
        force = event in {"start", "repo_done", "rate_limit_retry", "rate_limit_sleep", "finish", "error"}
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
        if self._line_count:
            self.stream.write("\n")
            self.stream.flush()


def dashboard_lines(snapshot: dict[str, object], *, width: int) -> list[str]:
    width = max(60, width)
    total = as_int(snapshot.get("total_count"))
    scanned = as_int(snapshot.get("scanned_count"))
    promoted = as_int(snapshot.get("promoted_count"))
    maybe = as_int(snapshot.get("maybe_count"))
    rejected = as_int(snapshot.get("rejected_count"))
    resumed = as_int(snapshot.get("resumed_count"))
    elapsed = as_float(snapshot.get("elapsed_sec"))
    new_scanned = max(0, scanned - resumed)
    rate_per_min = new_scanned / (elapsed / 60.0) if elapsed > 0 else 0.0
    eta = ((total - scanned) / rate_per_min * 60.0) if total > scanned and rate_per_min > 0 else None

    phase = phase_text(snapshot)
    current_index = as_int(snapshot.get("current_index"))
    current_repo = str(snapshot.get("current_repo_key") or "")
    decision = str(snapshot.get("decision") or "")
    b_score = snapshot.get("b_score")

    progress = progress_text(scanned, total)
    lines = [
        "Layer B remote code screening",
        f"Progress   {progress}",
        f"Elapsed    {format_duration(elapsed)}   Rate {rate_per_min:.2f} repos/min   ETA {format_duration(eta)}",
        f"Decisions  promote {promoted}   maybe {maybe}   reject {rejected}",
        fit_line(f"Current    #{current_index} {current_repo}", width),
        fit_line(f"Phase      {phase}", width),
    ]
    if decision:
        lines.append(f"Last       {decision}   score {format_score(b_score)}")
    if snapshot.get("sleep_remaining_sec") is not None:
        remaining = as_float(snapshot.get("sleep_remaining_sec"))
        label = "Network" if str(snapshot.get("event") or "") == "transient_error_sleep" else "RateLimit"
        lines.append(f"{label:<10} waiting {format_duration(remaining)} before retry")
    return [fit_line(line, width) for line in lines]


def phase_text(snapshot: dict[str, object]) -> str:
    event = str(snapshot.get("event") or "")
    term = str(snapshot.get("term") or "")
    hit_count = snapshot.get("hit_count")
    retry_count = snapshot.get("retry_count")

    if event == "start":
        return "starting"
    if event == "repo_start":
        return "scanning repo"
    if event == "repo_skipped_resume":
        return "already complete, skipped"
    if event == "tree_done":
        return f"tree fetched ({as_int(snapshot.get('path_count'))} paths)"
    if event == "tree_error":
        return "tree fetch error"
    if event == "code_search_start":
        return f"code search: {term}"
    if event == "code_search_done":
        return f"code search: {term} ({as_int(hit_count)} hits)"
    if event == "code_search_error":
        return f"code search error: {term}"
    if event == "rate_limit_retry":
        return f"rate limited, retry {as_int(retry_count)}"
    if event == "rate_limit_sleep":
        return f"waiting for rate limit reset, retry {as_int(retry_count)}"
    if event == "transient_error_retry":
        return f"network error, retry {as_int(retry_count)}"
    if event == "transient_error_sleep":
        return f"waiting after network error, retry {as_int(retry_count)}"
    if event == "repo_done":
        return "repo complete"
    if event == "limit_reached":
        return "limit reached"
    if event == "finish":
        return "finished"
    if event == "error":
        return "failed"
    return event or "running"


def progress_text(scanned: int, total: int) -> str:
    if total <= 0:
        return f"{scanned}/?"
    percent = min(100.0, max(0.0, scanned / total * 100.0))
    bar_width = 24
    filled = min(bar_width, int(bar_width * scanned / total))
    bar = "#" * filled + "-" * (bar_width - filled)
    return f"{scanned}/{total} ({percent:5.1f}%) [{bar}]"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h{minutes:02d}m{secs:02d}s"
    return f"{minutes:d}m{secs:02d}s"


def format_score(value: object) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "n/a"


def fit_line(line: str, width: int) -> str:
    if len(line) <= width:
        return line
    if width <= 3:
        return line[:width]
    return line[: width - 3] + "..."


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
