import csv
import json
from pathlib import Path

from migration_task_pipeline.buffers import BufferItem, SQLiteBuffer
from migration_task_pipeline.layers.c2_local_heuristic_screening.config import (
    C2RuntimeConfig,
    LayerC2Config,
    LocalScannerConfig,
)
from migration_task_pipeline.layers.c2_local_heuristic_screening.pipeline import run_c2_local_screening


def make_c1_item(repo_key: str, local_path: Path, *, priority: int = 100) -> BufferItem:
    return BufferItem(
        item_id=f"github-url:{repo_key}",
        repo_id=f"github-url:{repo_key}",
        repo_key=repo_key,
        repo_full_name=repo_key,
        repo_url=f"https://github.com/{repo_key}",
        source_layer="C1",
        source_run_id="run",
        payload_version="c1_to_c2.v1",
        payload_json={
            "repo_key": repo_key,
            "repo_full_name": repo_key,
            "repo_url": f"https://github.com/{repo_key}",
            "local_path": str(local_path),
            "checkout_sha": "abc123",
            "disk_bytes": 100,
            "file_count": 4,
        },
        scores_json={"b_score": 0.7},
        evidence_json={"source": "test"},
        priority=priority,
    )


def test_c2_pipeline_writes_artifacts_and_downstream_buffer(tmp_path):
    run_root = tmp_path / "runs" / "example"
    input_buffer = SQLiteBuffer(run_root / "buffers" / "c1_to_c2.sqlite")
    strong_repo = make_strong_repo(tmp_path / "strong")
    weak_repo = tmp_path / "weak"
    weak_repo.mkdir()
    (weak_repo / "README.md").write_text("cuda docs only\n", encoding="utf-8")
    input_buffer.insert_item(make_c1_item("owner/strong", strong_repo, priority=20))
    input_buffer.insert_item(make_c1_item("owner/weak", weak_repo, priority=10))

    outputs = run_c2_local_screening(
        run_root=run_root,
        config=LayerC2Config(
            scanner=LocalScannerConfig(),
            runtime=C2RuntimeConfig(concurrency=2),
        ),
    )

    assert outputs.claimed_count == 2
    assert outputs.promoted_count == 1
    assert outputs.rejected_count == 1
    assert outputs.enqueued_count == 1
    assert input_buffer.counts_by_status() == {"done": 1, "rejected": 1}
    output_buffer = SQLiteBuffer(run_root / "buffers" / "c2_to_d.sqlite")
    assert output_buffer.counts_by_status() == {"pending": 1}
    claimed = output_buffer.claim_next("test")
    assert claimed is not None
    assert claimed["source_layer"] == "C2"
    assert claimed["payload_json"]["local_path"]
    assert claimed["scores_json"]["c2_decision"] == "promote"

    with outputs.candidates_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert {row["c2_decision"] for row in rows} == {"promote", "reject"}

    with outputs.evidence_jsonl.open("r", encoding="utf-8") as handle:
        evidence_rows = [json.loads(line) for line in handle if line.strip()]
    assert len(evidence_rows) == 2
    assert all("scores" in row for row in evidence_rows)


def make_strong_repo(path: Path) -> Path:
    (path / "src").mkdir(parents=True)
    (path / "tests").mkdir()
    (path / "pyproject.toml").write_text(
        """
[project]
name = "strong"
[project.scripts]
strong = "strong.cli:main"
""",
        encoding="utf-8",
    )
    (path / "src" / "train.py").write_text(
        """
import argparse
import torch
import triton

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cuda")
    available = torch.cuda.is_available()
    tensor = torch.zeros(1).cuda()
    other = torch.empty(1, device="cuda")
    compiled = triton.jit
    if parser.parse_args().device == "cpu":
        tensor = tensor.cpu()

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    (path / "tests" / "test_smoke.py").write_text("import pytest\n", encoding="utf-8")
    return path
