from migration_task_pipeline.layers.b_remote_code_search.dashboard import dashboard_lines


def test_dashboard_lines_show_core_progress_fields():
    lines = dashboard_lines(
        {
            "event": "code_search_done",
            "total_count": 100,
            "scanned_count": 25,
            "promoted_count": 2,
            "maybe_count": 3,
            "rejected_count": 20,
            "current_index": 26,
            "current_repo_key": "owner/repo",
            "elapsed_sec": 300,
            "term": "torch.cuda",
            "hit_count": 4,
        },
        width=100,
    )
    text = "\n".join(lines)

    assert "25/100" in text
    assert "25.0%" in text
    assert "5.00 repos/min" in text
    assert "promote 2" in text
    assert "#26 owner/repo" in text
    assert "code search: torch.cuda (4 hits)" in text


def test_dashboard_rate_excludes_resumed_repos():
    lines = dashboard_lines(
        {
            "event": "repo_done",
            "total_count": 100,
            "scanned_count": 25,
            "resumed_count": 20,
            "promoted_count": 2,
            "maybe_count": 3,
            "rejected_count": 20,
            "current_index": 26,
            "current_repo_key": "owner/repo",
            "elapsed_sec": 300,
        },
        width=100,
    )
    text = "\n".join(lines)

    assert "25/100" in text
    assert "1.00 repos/min" in text
