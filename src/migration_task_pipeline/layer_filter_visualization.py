"""Build a standalone HTML visualization for one pipeline run."""

from __future__ import annotations

from collections import Counter
import csv
from datetime import UTC, datetime
import html
import json
from pathlib import Path
import sqlite3
from statistics import mean, median
from typing import Any


COLORS = {
    "seed": "#4b5563",
    "selected": "#2f80ed",
    "done": "#2f80ed",
    "promote": "#1f9d68",
    "pilot": "#1f9d68",
    "maybe": "#d89023",
    "hold": "#d89023",
    "reject": "#c94c4c",
    "rejected": "#c94c4c",
    "failed": "#9b2c2c",
    "pending": "#7c5cb8",
    "cards": "#2a9d8f",
    "other": "#64748b",
}


def collect_visualization_data(run_root: str | Path) -> dict[str, Any]:
    """Collect layer counts and decision distributions from a run directory."""
    root = Path(run_root)
    processed = root / "data" / "processed"
    buffers = root / "buffers"

    seeds_csv = processed / "repo-seeds-v0.csv"
    b_csv = processed / "repo-candidates-b.csv"
    c2_csv = processed / "repo-candidates-c2.csv"

    seed_count = count_csv_rows(seeds_csv)
    b_decisions = count_csv_values(b_csv, "b_decision")
    c2_decisions = count_csv_values(c2_csv, "c2_decision")

    b_to_c = inspect_buffer(buffers / "b_to_c.sqlite")
    c1_to_c2 = inspect_buffer(buffers / "c1_to_c2.sqlite")
    c2_to_d = inspect_buffer(buffers / "c2_to_d.sqlite")
    d_verdicts = inspect_d_cards(root / "candidate_cards")

    b_selected = b_to_c["total"] or sum(b_decisions.get(key, 0) for key in ("promote", "maybe"))
    c1_selected = c1_to_c2["total"]
    c2_processed = count_csv_rows(c2_csv) or c1_to_c2["status_counts"].get("done", 0) + c1_to_c2[
        "status_counts"
    ].get("rejected", 0)
    c2_selected = c2_to_d["total"]

    pipeline = [
        {
            "key": "seed",
            "label": "A Seed pool",
            "subtitle": "Initial repository universe",
            "strategy": "Collect and deduplicate repository URLs, enrich GitHub metadata, then write repo-seeds-v0.csv.",
            "output": "Seed CSV",
            "count": seed_count,
            "color_key": "seed",
        },
        {
            "key": "b_selected",
            "label": "Layer B remote code screening",
            "subtitle": "Promote/maybe remote evidence",
            "strategy": "GitHub remote code search plus tree-path signals score CUDA/GPU evidence, runnable interfaces, install/test/reference signals, manageability, and risk.",
            "output": "promote/maybe -> b_to_c.sqlite; reject stops",
            "count": b_selected,
            "status_counts": b_to_c["status_counts"],
            "color_key": "selected",
        },
        {
            "key": "c1_materialized",
            "label": "C1 Materialization",
            "subtitle": "Local checkout gate",
            "strategy": "Shallow clone selected repositories with bounded concurrency. Clone failures are operational and requeued, not candidate rejects.",
            "output": "successful checkout -> c1_to_c2.sqlite",
            "count": c1_selected,
            "status_counts": c1_to_c2["status_counts"],
            "color_key": "done",
        },
        {
            "key": "c2_processed",
            "label": "C2 Local screening",
            "subtitle": "Heuristic scan rows",
            "strategy": "Local-only bounded scan of file trees and text; no repository code execution. Checks CUDA/GPU assumptions, interfaces, installability, tests/examples, CPU/reference hints, risk, and size.",
            "output": "all processed rows -> repo-candidates-c2.csv",
            "count": c2_processed,
            "color_key": "done",
        },
        {
            "key": "c2_selected",
            "label": "C2 to D",
            "subtitle": "Promote/maybe local evidence",
            "strategy": "Promote/maybe decisions with enough local evidence are queued for deep review; local rejects remain recorded in C2 outputs.",
            "output": "promote/maybe -> c2_to_d.sqlite",
            "count": c2_selected,
            "status_counts": c2_to_d["status_counts"],
            "color_key": "selected",
        },
        {
            "key": "d_cards",
            "label": "D Agent review",
            "subtitle": "Final review cards",
            "strategy": "OpenCode agent review reads repository evidence and writes structured G4 review cards with verdict, confidence, score, task feasibility, and risks.",
            "output": "pilot/hold/reject YAML cards",
            "count": d_verdicts["total"],
            "color_key": "cards",
        },
    ]

    return {
        "run_name": root.name,
        "run_root": str(root),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "pipeline": add_retention(pipeline),
        "transitions": build_transitions(pipeline),
        "b_decisions": dict(b_decisions),
        "c2_decisions": dict(c2_decisions),
        "b_scores": summarize_csv_scores(b_csv, "b_score"),
        "c2_scores": summarize_csv_scores(c2_csv, "c2_score"),
        "buffers": {
            "b_to_c": b_to_c,
            "c1_to_c2": c1_to_c2,
            "c2_to_d": c2_to_d,
        },
        "d_verdicts": d_verdicts,
    }


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return sum(1 for _ in reader)


def count_csv_values(path: Path, field: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or field not in reader.fieldnames:
            return {}
        for row in reader:
            value = str(row.get(field) or "").strip().lower() or "(blank)"
            counts[value] += 1
    return dict(counts)


def summarize_csv_scores(path: Path, field: str) -> dict[str, float | int] | None:
    if not path.exists():
        return None
    values: list[float] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or field not in reader.fieldnames:
            return None
        for row in reader:
            try:
                values.append(float(str(row.get(field) or "").strip()))
            except ValueError:
                continue
    return summarize_values(values)


def summarize_values(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    return {
        "count": len(values),
        "min": round(min(values), 4),
        "median": round(median(values), 4),
        "mean": round(mean(values), 4),
        "max": round(max(values), 4),
    }


def inspect_buffer(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "total": 0, "status_counts": {}, "decision_counts": {}, "score_summary": None}

    status_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    scores: list[float] = []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT status, scores_json FROM buffer_items").fetchall()

    for row in rows:
        status_counts[str(row["status"] or "(blank)").lower()] += 1
        scores_json = row["scores_json"]
        try:
            scores_payload = json.loads(scores_json)
        except (TypeError, json.JSONDecodeError):
            scores_payload = {}
        for field in ("b_decision", "c2_decision"):
            if field in scores_payload:
                value = str(scores_payload.get(field) or "(blank)").strip().lower() or "(blank)"
                decision_counts[value] += 1
        for field in ("b_score", "c2_score"):
            if field in scores_payload:
                try:
                    scores.append(float(scores_payload[field]))
                except (TypeError, ValueError):
                    pass

    return {
        "path": str(path),
        "total": sum(status_counts.values()),
        "status_counts": dict(status_counts),
        "decision_counts": dict(decision_counts),
        "score_summary": summarize_values(scores),
    }


def inspect_d_cards(card_root: Path) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()
    scores: list[float] = []
    card_paths = sorted(card_root.glob("**/*.yaml")) if card_root.exists() else []

    for path in card_paths:
        verdict = read_verdict_fields(path)
        if "status" in verdict:
            status_counts[verdict["status"]] += 1
        if "confidence" in verdict:
            confidence_counts[verdict["confidence"]] += 1
        if "overall_score" in verdict:
            try:
                scores.append(float(verdict["overall_score"]))
            except ValueError:
                pass

    return {
        "total": len(card_paths),
        "status": dict(status_counts),
        "confidence": dict(confidence_counts),
        "score_summary": summarize_values(scores),
    }


def read_verdict_fields(path: Path) -> dict[str, str]:
    verdict: dict[str, str] = {}
    in_verdict = False
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("verdict:"):
                in_verdict = True
                continue
            if in_verdict and line and not line.startswith(" "):
                break
            if not in_verdict:
                continue
            stripped = line.strip()
            for field in ("status", "confidence", "overall_score"):
                prefix = f"{field}:"
                if stripped.startswith(prefix):
                    value = stripped.split(":", 1)[1].strip().strip("\"'").lower()
                    verdict[field] = value
    return verdict


def add_retention(pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seed_count = pipeline[0]["count"] if pipeline else 0
    previous_count = 0
    enriched = []
    for index, stage in enumerate(pipeline):
        count = int(stage["count"])
        item = dict(stage)
        item["retention_from_seed"] = ratio(count, seed_count)
        item["retention_from_previous"] = None if index == 0 else ratio(count, previous_count)
        item["dropped_from_previous"] = None if index == 0 else max(previous_count - count, 0)
        previous_count = count
        enriched.append(item)
    return enriched


def build_transitions(pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transitions = []
    for current, next_stage in zip(pipeline, pipeline[1:]):
        input_count = int(current["count"])
        next_count = int(next_stage["count"])
        passed = min(next_count, input_count)
        dropped = max(input_count - passed, 0)
        transitions.append(
            {
                "from": current["label"],
                "to": next_stage["label"],
                "input": input_count,
                "passed": passed,
                "dropped": dropped,
                "passed_ratio": ratio(passed, input_count),
                "dropped_ratio": ratio(dropped, input_count),
            }
        )
    return transitions


def ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def render_html(data: dict[str, Any]) -> str:
    max_count = max((int(stage["count"]) for stage in data["pipeline"]), default=1)
    pilot_count = int((data["d_verdicts"].get("status") or {}).get("pilot", 0))
    hold_count = int((data["d_verdicts"].get("status") or {}).get("hold", 0))
    reject_count = int((data["d_verdicts"].get("status") or {}).get("reject", 0))
    seed_count = int(data["pipeline"][0]["count"]) if data["pipeline"] else 0
    pilot_rate = format_percent(ratio(pilot_count, seed_count))

    score_rows = render_score_rows(
        [
            ("B score", data.get("b_scores")),
            ("C2 score", data.get("c2_scores")),
            ("D overall score", data["d_verdicts"].get("score_summary")),
        ]
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(data["run_name"])} layered filtering visualization</title>
  <style>
    :root {{
      color-scheme: light;
      --text: #172033;
      --muted: #5b6472;
      --line: #d9dee7;
      --paper: #ffffff;
      --soft: #f4f6f8;
      --ink-soft: #334155;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: #eef2f5;
    }}
    .figure {{
      max-width: 1500px;
      margin: 0 auto;
      padding: 24px;
    }}
    .card {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 24px;
      box-shadow: 0 18px 48px rgba(23, 32, 51, 0.08);
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: flex-end;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
      line-height: 1.12;
      letter-spacing: 0;
    }}
    .subtle {{
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }}
    .metric-strip {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      margin: 20px 0;
    }}
    .metric-cell {{
      min-width: 0;
      padding: 13px 14px;
      border-right: 1px solid var(--line);
      background: #fbfcfd;
    }}
    .metric-cell:last-child {{ border-right: 0; }}
    .metric-label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .metric-value {{
      margin-top: 8px;
      font-size: 24px;
      font-weight: 750;
    }}
    .flow {{
      display: grid;
      grid-template-columns: repeat(6, minmax(145px, 1fr));
      gap: 12px;
      align-items: stretch;
      margin: 18px 0 20px;
    }}
    .stage-node {{
      position: relative;
      min-height: 254px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 13px;
      background: #fbfcfd;
      overflow: hidden;
    }}
    .stage-node::before {{
      content: "";
      position: absolute;
      left: 0;
      bottom: 0;
      height: 8px;
      width: var(--fill);
      background: var(--stage-color);
    }}
    .stage-node:not(:last-child)::after {{
      content: ">";
      position: absolute;
      top: 17px;
      right: -10px;
      width: 18px;
      height: 18px;
      border-radius: 50%;
      border: 1px solid var(--line);
      background: var(--paper);
      color: var(--muted);
      font-size: 13px;
      line-height: 16px;
      text-align: center;
      z-index: 2;
    }}
    .stage-kicker {{
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 6px;
    }}
    .stage-title {{
      font-size: 14px;
      font-weight: 720;
    }}
    .stage-subtitle {{
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
      min-height: 32px;
    }}
    .stage-count {{
      margin-top: 12px;
      font-size: 29px;
      font-weight: 780;
      line-height: 1;
    }}
    .stage-meta {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}
    .strategy {{
      margin-top: 10px;
      color: var(--ink-soft);
      font-size: 12px;
      line-height: 1.45;
    }}
    .output {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.4;
    }}
    .section-title {{
      margin: 18px 0 10px;
      font-size: 16px;
      font-weight: 740;
    }}
    .visual-funnel {{
      display: grid;
      grid-template-columns: repeat(6, minmax(140px, 1fr));
      gap: 12px;
      margin: 14px 0 22px;
      align-items: stretch;
    }}
    .funnel-node {{
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 13px;
      background: #fbfcfd;
    }}
    .node-label {{
      min-height: 36px;
      color: var(--text);
      font-size: 14px;
      line-height: 1.25;
      font-weight: 740;
    }}
    .node-tag {{
      display: inline-block;
      margin-top: 6px;
      padding: 3px 7px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.2;
      background: #ffffff;
    }}
    .node-count {{
      margin-top: 12px;
      font-size: 30px;
      line-height: 1;
      font-weight: 780;
    }}
    .ratio-bar {{
      height: 16px;
      display: flex;
      overflow: hidden;
      margin-top: 13px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--soft);
    }}
    .ratio-pass {{
      background: #1f9d68;
      min-width: 2px;
    }}
    .ratio-drop {{
      background: #c94c4c;
      min-width: 2px;
    }}
    .ratio-labels {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-top: 8px;
      font-size: 12px;
      line-height: 1.35;
    }}
    .ratio-pass-text {{ color: #16754f; }}
    .ratio-drop-text {{ color: #a53a3a; text-align: right; }}
    .final-bar {{
      margin-top: 13px;
      height: 16px;
      border-radius: 999px;
      background: #2a9d8f;
    }}
    .agent-verdicts {{
      margin-top: 12px;
    }}
    .agent-title {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.3;
    }}
    .agent-stack {{
      height: 16px;
      display: flex;
      overflow: hidden;
      margin-top: 7px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--soft);
    }}
    .agent-segment {{
      min-width: 2px;
      background: var(--segment-color);
    }}
    .agent-legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px 9px;
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.3;
    }}
    .distribution-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
    }}
    .distribution {{
      min-width: 0;
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }}
    .distribution-title {{
      margin-bottom: 9px;
      color: var(--text);
      font-size: 13px;
      font-weight: 720;
    }}
    .stack {{
      height: 22px;
      display: flex;
      overflow: hidden;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: var(--soft);
    }}
    .segment {{
      min-width: 2px;
      background: var(--segment-color);
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 7px 11px;
      margin-top: 10px;
    }}
    .legend-item {{
      display: flex;
      gap: 8px;
      align-items: center;
      color: var(--muted);
      font-size: 13px;
    }}
    .swatch {{
      width: 10px;
      height: 10px;
      border-radius: 3px;
      background: var(--swatch-color);
      flex: 0 0 auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      padding: 9px 10px;
      border-bottom: 1px solid var(--line);
      text-align: right;
    }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{
      color: var(--muted);
      font-weight: 650;
      background: #f7f8fa;
    }}
    .sources {{
      margin-top: 16px;
      padding-top: 12px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      line-height: 1.6;
    }}
    @media (max-width: 980px) {{
      .figure {{ padding: 18px; }}
      .card {{ padding: 16px; }}
      header {{ display: block; }}
      .metric-strip {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .metric-cell {{ border-bottom: 1px solid var(--line); }}
      .flow {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .visual-funnel {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .stage-node:not(:last-child)::after {{ display: none; }}
      .distribution-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main class="figure">
    <article class="card" aria-label="single card layered filtering visualization">
      <header>
        <div>
          <h1>逐层筛选卡片图</h1>
          <div class="subtle">{escape(data["run_name"])} · generated {escape(data["generated_at"])}</div>
        </div>
        <div class="subtle">{escape(data["run_root"])}</div>
      </header>

      <section class="metric-strip" aria-label="summary metrics">
        <div class="metric-cell"><div class="metric-label">Seed pool</div><div class="metric-value">{format_int(seed_count)}</div></div>
        <div class="metric-cell"><div class="metric-label">B selected</div><div class="metric-value">{format_int(data["buffers"]["b_to_c"]["total"])}</div></div>
        <div class="metric-cell"><div class="metric-label">Selected for D</div><div class="metric-value">{format_int(data["buffers"]["c2_to_d"]["total"])}</div></div>
        <div class="metric-cell"><div class="metric-label">D pilot / hold / reject</div><div class="metric-value">{format_int(pilot_count)} / {format_int(hold_count)} / {format_int(reject_count)}</div></div>
        <div class="metric-cell"><div class="metric-label">Seed to pilot</div><div class="metric-value">{escape(pilot_rate)}</div></div>
      </section>

      <div class="section-title">过滤路径</div>
      {render_visual_funnel(data["pipeline"], data["transitions"], data["d_verdicts"]["status"])}

      <div class="section-title">关键分布</div>
      <section class="distribution-grid" aria-label="decision and status distributions">
        {render_distribution("Layer B decisions", data["b_decisions"])}
        {render_distribution("C1 status", data["buffers"]["c1_to_c2"]["status_counts"])}
        {render_distribution("Layer C2 decisions", data["c2_decisions"])}
        {render_distribution("Stage D verdicts", data["d_verdicts"]["status"])}
      </section>

      <div class="section-title">Score summary</div>
      {score_rows}

      <section class="sources">
        Data sources: data/processed/repo-seeds-v0.csv, data/processed/repo-candidates-b.csv,
        data/processed/repo-candidates-c2.csv, buffers/*.sqlite, candidate_cards/**/*.yaml.
        Buffer pending and failed counts are operational state, not candidate rejection.
      </section>
    </article>
  </main>
</body>
</html>
"""


def render_visual_funnel(
    pipeline: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    d_verdict_counts: dict[str, int],
) -> str:
    transition_by_from = {item["from"]: item for item in transitions}
    nodes = []
    for stage in pipeline:
        transition = transition_by_from.get(stage["label"])
        node_body = ""
        if transition is not None:
            pass_ratio = transition["passed_ratio"] or 0
            drop_ratio = transition["dropped_ratio"] or 0
            node_body = f"""
          <div class="ratio-bar" aria-label="passed and dropped ratio">
            <span class="ratio-pass" style="width:{pass_ratio * 100:.4f}%"></span>
            <span class="ratio-drop" style="width:{drop_ratio * 100:.4f}%"></span>
          </div>
          <div class="ratio-labels">
            <span class="ratio-pass-text">进入 {format_int(int(transition["passed"]))}<br>{escape(format_percent(pass_ratio))}</span>
            <span class="ratio-drop-text">过滤 {format_int(int(transition["dropped"]))}<br>{escape(format_percent(drop_ratio))}</span>
          </div>
            """
        else:
            node_body = render_agent_verdicts(d_verdict_counts)
        nodes.append(
            f"""
        <section class="funnel-node">
          <div class="node-label">{escape(short_stage_label(stage["label"]))}</div>
          <span class="node-tag">{escape(short_stage_strategy(stage["key"]))}</span>
          <div class="node-count">{format_int(int(stage["count"]))}</div>
          {node_body}
        </section>
            """
        )
    return f'<section class="visual-funnel" aria-label="visual pass and drop funnel">{"".join(nodes)}</section>'


def render_agent_verdicts(counts: dict[str, int]) -> str:
    total = sum(int(value) for value in counts.values())
    if total <= 0:
        return '<div class="final-bar" aria-label="final layer"></div>'

    ordered_keys = [key for key in ("pilot", "hold", "reject") if int(counts.get(key, 0)) > 0]
    ordered_keys.extend(sorted(key for key, value in counts.items() if key not in ordered_keys and int(value) > 0))

    segments = []
    legend = []
    for key in ordered_keys:
        value = int(counts[key])
        width = value / total * 100
        segments.append(
            f'<span class="agent-segment" style="width:{width:.4f}%; --segment-color:{color_for_key(key)}"></span>'
        )
        legend.append(f"<span>{escape(key)} {format_int(value)} {escape(format_percent(value / total))}</span>")

    return f"""
          <div class="agent-verdicts">
            <div class="agent-title">Agent评审结果</div>
            <div class="agent-stack" aria-label="agent review verdict distribution">{''.join(segments)}</div>
            <div class="agent-legend">{''.join(legend)}</div>
          </div>
    """


def short_stage_label(label: str) -> str:
    labels = {
        "A Seed pool": "A Seeds",
        "Layer B remote code screening": "B Remote",
        "C1 Materialization": "C1 Clone",
        "C2 Local screening": "C2 Scan",
        "C2 to D": "D Queue",
        "D Agent review": "D Review",
    }
    return labels.get(label, label)


def short_stage_strategy(key: str) -> str:
    strategies = {
        "seed": "去重+元数据",
        "b_selected": "远程代码搜索",
        "c1_materialized": "浅克隆",
        "c2_processed": "本地扫描",
        "c2_selected": "promote/maybe",
        "d_cards": "Agent评审",
    }
    return strategies.get(key, key)


def render_card_pipeline(pipeline: list[dict[str, Any]], max_count: int) -> str:
    stages = []
    for index, stage in enumerate(pipeline, start=1):
        color = COLORS.get(str(stage.get("color_key")), COLORS["other"])
        fill = 0 if max_count <= 0 else max(3, round(int(stage["count"]) / max_count * 100, 2))
        retention = format_percent(stage.get("retention_from_previous"))
        seed_retention = format_percent(stage.get("retention_from_seed"))
        dropped = stage.get("dropped_from_previous")
        dropped_text = "start" if dropped is None else f"drop {format_int(int(dropped))}"
        status_counts = stage.get("status_counts") or {}
        status_line = ""
        if status_counts:
            bits = ", ".join(f"{escape(key)} {format_int(value)}" for key, value in sorted(status_counts.items()))
            status_line = f"<div>{bits}</div>"
        stages.append(
            f"""
        <section class="stage-node" style="--fill: {fill}%; --stage-color: {color}">
          <div class="stage-kicker">Layer {index}</div>
          <div class="stage-title">{escape(stage["label"])}</div>
          <div class="stage-subtitle">{escape(stage["subtitle"])}</div>
          <div class="stage-count">{format_int(int(stage["count"]))}</div>
          <div class="stage-meta">
            <div>prev {escape(retention)} · seed {escape(seed_retention)}</div>
            <div>{escape(dropped_text)}</div>
            {status_line}
          </div>
          <div class="strategy">{escape(stage.get("strategy", ""))}</div>
          <div class="output">{escape(stage.get("output", ""))}</div>
        </section>
            """
        )
    return f'<section class="flow" aria-label="layer strategy flow">{"".join(stages)}</section>'


def render_distribution(title: str, counts: dict[str, int]) -> str:
    return f"""
        <section class="distribution">
          <div class="distribution-title">{escape(title)}</div>
          {render_stacked_bar(counts)}
        </section>
    """


def render_pipeline(pipeline: list[dict[str, Any]], max_count: int) -> str:
    stages = []
    for stage in pipeline:
        color = COLORS.get(str(stage.get("color_key")), COLORS["other"])
        fill = 0 if max_count <= 0 else max(3, round(int(stage["count"]) / max_count * 100, 2))
        retention = format_percent(stage.get("retention_from_previous"))
        seed_retention = format_percent(stage.get("retention_from_seed"))
        dropped = stage.get("dropped_from_previous")
        dropped_line = "" if dropped is None else f"<div>prev drop {format_int(int(dropped))}</div>"
        status_counts = stage.get("status_counts") or {}
        status_line = ""
        if status_counts:
            bits = ", ".join(f"{escape(key)} {format_int(value)}" for key, value in sorted(status_counts.items()))
            status_line = f"<div>{bits}</div>"
        stages.append(
            f"""
      <div class="stage" style="--fill: {fill}%; --stage-color: {color}">
        <div class="stage-title">{escape(stage["label"])}</div>
        <div class="stage-subtitle">{escape(stage["subtitle"])}</div>
        <div class="stage-count">{format_int(int(stage["count"]))}</div>
        <div class="stage-meta">
          <div>prev {escape(retention)} · seed {escape(seed_retention)}</div>
          {dropped_line}
          {status_line}
        </div>
      </div>
            """
        )
    return f"""
    <section class="panel">
      <h2>Pipeline funnel</h2>
      <div class="pipeline">
        {''.join(stages)}
      </div>
    </section>
"""


def render_stack_section(title: str, counts: dict[str, int]) -> str:
    return f"""
    <section class="panel">
      <h2>{escape(title)}</h2>
      {render_stacked_bar(counts)}
    </section>
"""


def render_status_section(title: str, counts: dict[str, int]) -> str:
    return render_stack_section(title, counts)


def render_stacked_bar(counts: dict[str, int]) -> str:
    total = sum(int(value) for value in counts.values())
    if total <= 0:
        return '<div class="subtle">No data</div>'
    order = sorted(counts, key=lambda key: (-int(counts[key]), key))
    segments = []
    legend = []
    for key in order:
        value = int(counts[key])
        width = round(value / total * 100, 4)
        color = color_for_key(key)
        segments.append(f'<span class="segment" style="width:{width}%; --segment-color:{color}"></span>')
        legend.append(
            f"""
        <div class="legend-item">
          <span class="swatch" style="--swatch-color:{color}"></span>
          <span>{escape(key)} {format_int(value)} ({escape(format_percent(value / total))})</span>
        </div>
            """
        )
    return f"""
      <div class="stack">{''.join(segments)}</div>
      <div class="legend">{''.join(legend)}</div>
"""


def color_for_key(key: str) -> str:
    normalized = key.strip().lower()
    return COLORS.get(normalized, COLORS["other"])


def render_score_rows(rows: list[tuple[str, dict[str, Any] | None]]) -> str:
    body = []
    for label, summary in rows:
        if not summary:
            continue
        body.append(
            "<tr>"
            f"<td>{escape(label)}</td>"
            f"<td>{format_int(int(summary['count']))}</td>"
            f"<td>{format_number(summary['min'])}</td>"
            f"<td>{format_number(summary['median'])}</td>"
            f"<td>{format_number(summary['mean'])}</td>"
            f"<td>{format_number(summary['max'])}</td>"
            "</tr>"
        )
    if not body:
        return '<div class="subtle">No score data</div>'
    return (
        "<table><thead><tr><th>Metric</th><th>N</th><th>Min</th><th>Median</th><th>Mean</th><th>Max</th>"
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def format_int(value: int) -> str:
    return f"{value:,}"


def format_number(value: Any) -> str:
    try:
        return f"{float(value):.4g}"
    except (TypeError, ValueError):
        return escape(str(value))


def format_percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)
