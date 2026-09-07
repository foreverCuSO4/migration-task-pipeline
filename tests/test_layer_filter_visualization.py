from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from migration_task_pipeline.layer_filter_visualization import collect_visualization_data, render_html


def test_collect_visualization_data_counts_pipeline_layers(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "example-run"
    processed = run_root / "data" / "processed"
    buffers = run_root / "buffers"
    cards = run_root / "candidate_cards" / "screening"
    processed.mkdir(parents=True)
    buffers.mkdir(parents=True)
    cards.mkdir(parents=True)

    write_csv(processed / "repo-seeds-v0.csv", ["repo_key"], [["a"], ["b"], ["c"], ["d"]])
    write_csv(
        processed / "repo-candidates-b.csv",
        ["repo_key", "b_decision", "b_score"],
        [["a", "promote", "0.9"], ["b", "maybe", "0.5"], ["c", "reject", "0.1"], ["d", "reject", "0.0"]],
    )
    write_csv(
        processed / "repo-candidates-c2.csv",
        ["repo_key", "c2_decision", "c2_score"],
        [["a", "promote", "0.8"], ["b", "reject", "0.2"]],
    )
    create_buffer(
        buffers / "b_to_c.sqlite",
        "B",
        [("a", "done", '{"b_decision":"promote","b_score":0.9}'), ("b", "pending", '{"b_decision":"maybe"}')],
    )
    create_buffer(
        buffers / "c1_to_c2.sqlite",
        "C1",
        [("a", "done", '{"b_decision":"promote"}'), ("b", "rejected", '{"b_decision":"maybe"}')],
    )
    create_buffer(
        buffers / "c2_to_d.sqlite",
        "C2",
        [("a", "done", '{"c2_decision":"promote","c2_score":0.8}')],
    )
    (cards / "a.yaml").write_text(
        "schema_version: g4_review.v1\n"
        "verdict:\n"
        "  status: pilot\n"
        "  confidence: high\n"
        "  overall_score: 75\n",
        encoding="utf-8",
    )
    (cards / "b.yaml").write_text(
        "schema_version: g4_review.v1\n"
        "verdict:\n"
        "  status: reject\n"
        "  confidence: medium\n"
        "  overall_score: 12\n",
        encoding="utf-8",
    )

    data = collect_visualization_data(run_root)

    assert data["run_name"] == "example-run"
    assert data["pipeline"][0]["count"] == 4
    assert data["pipeline"][1]["count"] == 2
    assert data["pipeline"][1]["status_counts"] == {"done": 1, "pending": 1}
    assert data["pipeline"][2]["status_counts"] == {"done": 1, "rejected": 1}
    assert data["pipeline"][4]["count"] == 1
    assert data["b_decisions"] == {"promote": 1, "maybe": 1, "reject": 2}
    assert data["c2_decisions"] == {"promote": 1, "reject": 1}
    assert data["d_verdicts"]["status"] == {"pilot": 1, "reject": 1}
    assert data["d_verdicts"]["confidence"] == {"high": 1, "medium": 1}
    assert "GitHub remote code search" in data["pipeline"][1]["strategy"]
    assert data["transitions"][0] == {
        "from": "A Seed pool",
        "to": "Layer B remote code screening",
        "input": 4,
        "passed": 2,
        "dropped": 2,
        "passed_ratio": 0.5,
        "dropped_ratio": 0.5,
    }

    html = render_html(data)

    assert 'class="card"' in html
    assert 'class="visual-funnel"' in html
    assert "Layer B" in html
    assert "进入 2" in html
    assert "过滤 2" in html
    assert "50.0%" in html
    assert 'class="agent-verdicts"' in html
    assert "Agent评审结果" in html
    assert "pilot 1" in html
    assert "reject 1" in html
    assert "GitHub remote code search plus tree-path signals" not in html


def write_csv(path: Path, fieldnames: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        writer.writerows(rows)


def create_buffer(path: Path, source_layer: str, rows: list[tuple[str, str, str]]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE buffer_items (
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
        for repo_key, status, scores_json in rows:
            connection.execute(
                """
                INSERT INTO buffer_items (
                  item_id, repo_id, repo_key, repo_full_name, repo_url,
                  source_layer, source_run_id, payload_version,
                  payload_json, scores_json, evidence_json,
                  priority, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    repo_key,
                    repo_key,
                    repo_key,
                    repo_key,
                    f"https://example.test/{repo_key}",
                    source_layer,
                    "run",
                    "test.v1",
                    "{}",
                    scores_json,
                    "{}",
                    0,
                    status,
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
