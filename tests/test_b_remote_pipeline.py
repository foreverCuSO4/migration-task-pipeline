import csv
import json

from migration_task_pipeline.layers.b_remote_code_search.config import RemoteCodeSearchConfig
from migration_task_pipeline.layers.b_remote_code_search.pipeline import run_remote_code_screening


class FakeGitHubRemoteClient:
    def get_tree(self, repo_key, ref):
        return {
            "tree": [
                {"type": "blob", "path": "pyproject.toml"},
                {"type": "blob", "path": "setup.cfg"},
                {"type": "blob", "path": "src/train.py"},
                {"type": "blob", "path": "src/model.py"},
                {"type": "blob", "path": "tests/test_devices.py"},
                {"type": "blob", "path": "examples/infer.py"},
            ]
        }

    def search_code(self, query, *, per_page=5, page=1):
        items = []
        if "torch.cuda" in query:
            items.append({"path": "src/model.py", "html_url": "https://example/src/model.py"})
        if "console_scripts" in query:
            items.append({"path": "setup.cfg", "html_url": "https://example/setup.cfg"})
        if "--device cpu" in query:
            items.append({"path": "tests/test_devices.py", "html_url": "https://example/tests/test_devices.py"})
        return {"items": items}


def test_remote_code_screening_writes_outputs(tmp_path):
    seed_csv = tmp_path / "repo-seeds-v0.csv"
    with seed_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "repo_key",
                "repo_url",
                "repo_owner",
                "repo_name",
                "github_size_kb",
                "github_archived",
                "github_default_branch",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "repo_key": "owner/repo",
                "repo_url": "https://github.com/owner/repo",
                "repo_owner": "owner",
                "repo_name": "repo",
                "github_size_kb": str(50 * 1024),
                "github_archived": "false",
                "github_default_branch": "main",
            }
        )

    outputs = run_remote_code_screening(
        seed_csv,
        output_root=tmp_path,
        run_date="20260617",
        github_client=FakeGitHubRemoteClient(),
        config=RemoteCodeSearchConfig(per_page=2, max_code_queries_per_repo=5),
    )

    assert outputs.scanned_count == 1
    assert outputs.candidates_csv.exists()
    assert outputs.signals_jsonl.exists()
    assert outputs.log_file.exists()
    csv_text = outputs.candidates_csv.read_text(encoding="utf-8")
    assert "repo-candidates-b" not in csv_text
    assert "owner/repo" in csv_text
    evidence = json.loads(outputs.signals_jsonl.read_text(encoding="utf-8").splitlines()[0])
    assert evidence["repo_key"] == "owner/repo"
    assert evidence["code_hits"]
    log_text = outputs.log_file.read_text(encoding="utf-8")
    assert '"event": "repo_done"' in log_text
