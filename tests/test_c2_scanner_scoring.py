from migration_task_pipeline.buffers import BufferItem
from migration_task_pipeline.layers.c2_local_heuristic_screening.config import LocalScannerConfig
from migration_task_pipeline.layers.c2_local_heuristic_screening.scanner import scan_repository
from migration_task_pipeline.layers.c2_local_heuristic_screening.scoring import score_repository


def make_c1_item(repo_key: str, local_path) -> BufferItem:
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
            "repo_url": f"https://github.com/{repo_key}",
            "local_path": str(local_path),
            "checkout_sha": "abc123",
            "disk_bytes": 100,
            "file_count": 4,
        },
        scores_json={"b_score": 0.7},
        evidence_json={"source": "test"},
        priority=100,
    )


def test_c2_scanner_and_scoring_promote_strong_local_candidate(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pyproject.toml").write_text(
        """
[project]
name = "demo"
[project.scripts]
demo = "demo.cli:main"
""",
        encoding="utf-8",
    )
    (repo / "src" / "train.py").write_text(
        """
import argparse
import torch
import triton

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cuda")
    available = torch.cuda.is_available()
    x = torch.zeros(1).cuda()
    y = torch.empty(1, device="cuda")
    compiled = triton.jit
    if parser.parse_args().device == "cpu":
        x = x.cpu()

if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    (repo / "tests" / "test_smoke.py").write_text("import pytest\n", encoding="utf-8")
    item = make_c1_item("owner/repo", repo)

    evidence = scan_repository(dict(item.__dict__), LocalScannerConfig())
    scored = score_repository(dict(item.__dict__), evidence)

    assert "torch.cuda" in evidence["matched_terms"] or ".cuda(" in evidence["matched_terms"]
    assert scored["c2_decision"] == "promote"
    assert scored["local_cuda_score"] >= 0.30
    assert scored["interface_contract_score"] >= 0.30
    assert scored["installability_score"] > 0


def test_c2_scoring_rejects_docs_only_cuda_signal(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("This project mentions torch.cuda in docs only.\n", encoding="utf-8")
    item = make_c1_item("owner/docs", repo)

    evidence = scan_repository(dict(item.__dict__), LocalScannerConfig())
    scored = score_repository(dict(item.__dict__), evidence)

    assert scored["c2_decision"] == "reject"
    assert "docs_only_cuda_signal" in scored["c2_reasons"]


def test_c2_scanner_skips_configured_dirs_and_large_files(tmp_path):
    repo = tmp_path / "repo"
    (repo / "vendor").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "vendor" / "ignored.py").write_text("torch.cuda\n", encoding="utf-8")
    (repo / "src" / "big.py").write_text("torch.cuda\n" * 100, encoding="utf-8")
    (tmp_path / "outside.py").write_text("torch.cuda\n", encoding="utf-8")
    (repo / "src" / "outside_link.py").symlink_to(tmp_path / "outside.py")
    item = make_c1_item("owner/large", repo)

    evidence = scan_repository(
        dict(item.__dict__),
        LocalScannerConfig(max_file_size_bytes=10, skip_dirs=["vendor"]),
    )

    assert evidence["skipped_dir_count"] == 1
    assert evidence["skipped_large_file_count"] == 1
    assert evidence["skipped_symlink_file_count"] == 1
    assert "vendor/ignored.py" not in evidence["tree_paths"]
    assert "src/outside_link.py" not in evidence["tree_paths"]
