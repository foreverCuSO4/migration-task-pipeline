from migration_task_pipeline.layers.c1_local_materialization.dashboard import dashboard_lines


def test_c1_dashboard_lines_show_core_progress_fields():
    lines = dashboard_lines(
        {
            "event": "clone_retry_scheduled",
            "total_count": 10,
            "input_status_counts": {
                "pending": 4,
                "in_progress": 1,
                "done": 4,
                "failed": 1,
            },
            "output_status_counts": {"pending": 4},
            "elapsed_sec": 120,
            "claimed_count": 6,
            "cloned_count": 4,
            "failed_count": 2,
            "terminal_failed_count": 1,
            "enqueued_count": 4,
            "worker_id": "c1-worker-1",
            "repo_key": "owner/repo",
            "attempts": 2,
            "max_attempts": 3,
        },
        width=100,
    )

    text = "\n".join(lines)
    assert "Layer C1 local materialization" in text
    assert "Progress   5/10" in text
    assert "Rate 2.50 repos/min" in text
    assert "claimed 6" in text
    assert "pending 4" in text
    assert "c1-worker-1 owner/repo" in text
    assert "clone failed, retry queued" in text
    assert "Attempts   2/3" in text
