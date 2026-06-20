from pathlib import Path

from migration_task_pipeline.buffer_admin import (
    BufferItemSelector,
    reset_buffer_items,
    resolve_buffer_path,
    select_buffer_items,
)
from migration_task_pipeline.buffers import BufferItem, SQLiteBuffer


def make_item(item_id: str, repo_key: str, *, decision: str = "promote", priority: int = 10) -> BufferItem:
    return BufferItem(
        item_id=item_id,
        repo_id=item_id,
        repo_key=repo_key,
        repo_full_name=repo_key,
        repo_url=f"https://github.com/{repo_key}",
        source_layer="C2",
        source_run_id="run",
        payload_version="c2_to_d.v1",
        payload_json={"repo_key": repo_key, "c2_decision": decision},
        scores_json={"c2_decision": decision, "c2_score": 0.8},
        evidence_json={"source": "test"},
        priority=priority,
    )


def test_resolve_known_buffer_under_run_root():
    assert resolve_buffer_path(run_root="runs/example", buffer_name="c2_to_d") == Path(
        "runs/example/buffers/c2_to_d.sqlite"
    )
    assert resolve_buffer_path(buffer_path="custom.sqlite") == Path("custom.sqlite")


def test_select_buffer_items_filters_by_status_decision_and_repo(tmp_path):
    buffer = SQLiteBuffer(tmp_path / "c2_to_d.sqlite")
    buffer.insert_item(make_item("done", "owner/done", decision="promote", priority=30))
    buffer.insert_item(make_item("maybe", "owner/maybe", decision="maybe", priority=20))
    buffer.insert_item(make_item("pending", "other/pending", decision="promote", priority=10))
    buffer.mark_done("done")
    buffer.mark_done("maybe")

    matches = select_buffer_items(
        buffer.path,
        BufferItemSelector(statuses={"done"}, decisions={"promote"}, repo_contains="owner/"),
    )

    assert [item["item_id"] for item in matches] == ["done"]


def test_reset_buffer_items_sets_pending_and_clears_runtime_fields(tmp_path):
    buffer = SQLiteBuffer(tmp_path / "c2_to_d.sqlite")
    buffer.insert_item(make_item("repo", "owner/repo", priority=50))
    claimed = buffer.claim_next("worker-a")
    assert claimed is not None
    buffer.mark_done("repo")

    updated = reset_buffer_items(buffer.path, ["repo"], last_error="manual_reset")

    assert updated == 1
    item = buffer.get_item("repo")
    assert item is not None
    assert item["status"] == "pending"
    assert item["attempts"] == 0
    assert item["worker_id"] == ""
    assert item["leased_at"] == ""
    assert item["lease_expires_at"] == ""
    assert item["last_error"] == "manual_reset"
