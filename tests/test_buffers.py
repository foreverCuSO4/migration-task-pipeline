from migration_task_pipeline.buffers import BufferItem, SQLiteBuffer


def make_item(item_id="item-1", repo_key="owner/repo", priority=10):
    return BufferItem(
        item_id=item_id,
        repo_id=item_id,
        repo_key=repo_key,
        repo_full_name=repo_key,
        repo_url=f"https://github.com/{repo_key}",
        source_layer="B",
        source_run_id="run",
        payload_version="test.v1",
        payload_json={"repo_key": repo_key},
        scores_json={"b_score": 0.5},
        evidence_json={"source": "test"},
        priority=priority,
    )


def test_sqlite_buffer_initializes_and_inserts_idempotently(tmp_path):
    buffer = SQLiteBuffer(tmp_path / "b_to_c.sqlite")

    assert buffer.insert_item(make_item()) is True
    assert buffer.insert_item(make_item(priority=999)) is False

    assert buffer.counts_by_status() == {"pending": 1}
    assert buffer.has_item("item-1") is True


def test_sqlite_buffer_claims_highest_priority_item(tmp_path):
    buffer = SQLiteBuffer(tmp_path / "b_to_c.sqlite")
    buffer.insert_item(make_item("low", "owner/low", priority=10))
    buffer.insert_item(make_item("high", "owner/high", priority=20))

    claimed = buffer.claim_next("worker-a")

    assert claimed is not None
    assert claimed["item_id"] == "high"
    assert claimed["status"] == "in_progress"
    assert claimed["attempts"] == 1
    assert claimed["worker_id"] == "worker-a"


def test_sqlite_buffer_reclaims_expired_lease(tmp_path):
    buffer = SQLiteBuffer(tmp_path / "b_to_c.sqlite")
    buffer.insert_item(make_item())
    first_claim = buffer.claim_next("worker-a", lease_seconds=-1)
    assert first_claim is not None

    second_claim = buffer.claim_next("worker-b")

    assert second_claim is not None
    assert second_claim["item_id"] == "item-1"
    assert second_claim["attempts"] == 2
    assert second_claim["worker_id"] == "worker-b"


def test_sqlite_buffer_marks_terminal_statuses(tmp_path):
    buffer = SQLiteBuffer(tmp_path / "b_to_c.sqlite")
    buffer.insert_item(make_item("done"))
    buffer.insert_item(make_item("failed"))
    buffer.insert_item(make_item("rejected"))

    buffer.mark_done("done")
    buffer.mark_failed("failed", "temporary")
    buffer.mark_rejected("rejected", "not suitable")

    assert buffer.counts_by_status() == {"done": 1, "failed": 1, "rejected": 1}


def test_sqlite_buffer_requeues_pending_at_lower_priority(tmp_path):
    buffer = SQLiteBuffer(tmp_path / "b_to_c.sqlite")
    buffer.insert_item(make_item("retry", priority=100))

    claimed = buffer.claim_next("worker-a")
    assert claimed is not None
    buffer.requeue_pending("retry", error="clone failed", priority=0)

    item = buffer.get_item("retry")
    assert item is not None
    assert item["status"] == "pending"
    assert item["priority"] == 0
    assert item["worker_id"] == ""
    assert item["last_error"] == "clone failed"
