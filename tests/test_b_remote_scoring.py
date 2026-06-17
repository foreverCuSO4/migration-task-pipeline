from migration_task_pipeline.layers.b_remote_code_search.scoring import (
    CodeHit,
    interface_signal_score,
    repo_manageability_score,
    score_repository,
)


def test_manageability_score_uses_metadata_size_buckets():
    assert repo_manageability_score(50 * 1024) == 1.0
    assert repo_manageability_score(150 * 1024) == 0.7
    assert repo_manageability_score(300 * 1024) == 0.3
    assert repo_manageability_score(500 * 1024) == 0.0


def test_interface_score_rewards_stable_entrypoints():
    score = interface_signal_score(
        [CodeHit(group="interface", term="console_scripts", path="setup.cfg")],
        ["setup.cfg", "mace/cli/eval_configs.py", "examples/run.py", "README.md"],
    )

    assert score >= 0.70


def test_score_repository_promotes_strong_remote_evidence():
    row = {
        "repo_key": "owner/repo",
        "repo_url": "https://github.com/owner/repo",
        "github_size_kb": 50 * 1024,
        "github_archived": "false",
    }
    hits = [
        CodeHit("cuda", "torch.cuda", "src/train.py"),
        CodeHit("cuda", ".cuda(", "src/model.py"),
        CodeHit("cuda", "CUDAExtension", "setup.py"),
        CodeHit("interface", "console_scripts", "setup.cfg"),
        CodeHit("interface", "argparse.ArgumentParser", "src/train.py"),
        CodeHit("reference", "--device cpu", "tests/test_devices.py"),
        CodeHit("reference", "fixture", "tests/fixtures.py"),
    ]
    tree_paths = [
        "pyproject.toml",
        "setup.cfg",
        "requirements.txt",
        "src/train.py",
        "src/model.py",
        "tests/test_devices.py",
        "examples/infer.py",
    ]

    scored = score_repository(row, code_hits=hits, tree_paths=tree_paths, errors=[])

    assert scored["b_decision"] == "promote"
    assert scored["b_score"] >= 0.65
    assert scored["executable_cuda_score"] > 0.3


def test_docs_only_cuda_signal_is_rejected():
    row = {
        "repo_key": "owner/repo",
        "repo_url": "https://github.com/owner/repo",
        "github_size_kb": 50 * 1024,
        "github_archived": "false",
    }
    scored = score_repository(
        row,
        code_hits=[CodeHit("cuda", "torch.cuda", "README.md")],
        tree_paths=["README.md", "docs/usage.md"],
        errors=[],
    )

    assert scored["b_decision"] == "reject"
    assert "docs_only_cuda_signal" in scored["b_reasons"]

