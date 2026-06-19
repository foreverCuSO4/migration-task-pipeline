from migration_task_pipeline.layers.c2_local_heuristic_screening.dashboard import dashboard_lines


def test_c2_dashboard_lines_show_core_progress_fields():
    lines = dashboard_lines(
        {
            "event": "repo_scanned",
            "total_count": 10,
            "input_status_counts": {
                "pending": 4,
                "in_progress": 1,
                "done": 3,
                "rejected": 2,
            },
            "output_status_counts": {"pending": 4},
            "elapsed_sec": 120,
            "promoted_count": 2,
            "maybe_count": 2,
            "rejected_count": 1,
            "failed_count": 0,
            "worker_id": "c2-worker-1",
            "repo_key": "owner/repo",
            "decision": "promote",
            "c2_score": 0.75,
        },
        width=100,
    )

    text = "\n".join(lines)
    assert "Layer C2 local heuristic screening" in text
    assert "Progress   5/10" in text
    assert "Rate 2.50 repos/min" in text
    assert "promote 2" in text
    assert "c2-worker-1 owner/repo" in text
    assert "repo scanned" in text
    assert "promote   score 0.7500" in text

