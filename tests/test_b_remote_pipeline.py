import csv
import json

from migration_task_pipeline.layers.b_remote_code_search.config import RemoteCodeSearchConfig
from migration_task_pipeline.layers.b_remote_code_search.github_client import GitHubAccessError, GitHubRateLimitError
from migration_task_pipeline.layers.b_remote_code_search.pipeline import RemoteScanIncomplete, run_remote_code_screening


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


class RateLimitedThenOkClient(FakeGitHubRemoteClient):
    def __init__(self):
        self.search_calls = 0

    def search_code(self, query, *, per_page=5, page=1):
        self.search_calls += 1
        if self.search_calls == 1:
            raise GitHubRateLimitError("GitHub rate limit for all tokens", retry_after_seconds=0)
        return {
            "items": [
                {"path": "src/cuda_a.py", "html_url": "https://example/src/cuda_a.py"},
                {"path": "src/cuda_b.py", "html_url": "https://example/src/cuda_b.py"},
                {"path": "src/cuda_c.py", "html_url": "https://example/src/cuda_c.py"},
                {"path": "src/cuda_d.py", "html_url": "https://example/src/cuda_d.py"},
            ]
        }


class AlwaysRateLimitedClient(FakeGitHubRemoteClient):
    def search_code(self, query, *, per_page=5, page=1):
        raise GitHubRateLimitError("GitHub rate limit for all tokens", retry_after_seconds=0)


class AccessDeniedClient(FakeGitHubRemoteClient):
    def search_code(self, query, *, per_page=5, page=1):
        raise GitHubAccessError("GitHub API access error: HTTP 403")


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


def test_remote_code_screening_emits_progress_events(tmp_path):
    seed_csv = write_seed_csv(tmp_path)
    events = []

    outputs = run_remote_code_screening(
        seed_csv,
        output_root=tmp_path,
        run_date="20260617",
        github_client=FakeGitHubRemoteClient(),
        config=RemoteCodeSearchConfig(per_page=2, max_code_queries_per_repo=2),
        progress_callback=events.append,
    )

    event_names = [event["event"] for event in events]
    assert outputs.scanned_count == 1
    assert event_names[0] == "start"
    assert "repo_start" in event_names
    assert "code_search_done" in event_names
    assert event_names[-1] == "finish"
    assert events[-1]["scanned_count"] == 1
    assert events[-1]["total_count"] == 1


def test_remote_code_screening_retries_rate_limit_before_scoring(tmp_path):
    seed_csv = write_seed_csv(tmp_path)
    client = RateLimitedThenOkClient()

    outputs = run_remote_code_screening(
        seed_csv,
        output_root=tmp_path,
        run_date="20260617",
        github_client=client,
        config=RemoteCodeSearchConfig(
            per_page=5,
            max_code_queries_per_repo=1,
            rate_limit_max_retries=1,
            rate_limit_retry_sleep_seconds=0,
            rate_limit_max_sleep_seconds=0,
        ),
    )

    rows = list(csv.DictReader(outputs.candidates_csv.open(encoding="utf-8", newline="")))
    assert outputs.scanned_count == 1
    assert client.search_calls == 2
    assert rows[0]["b_decision"] == "maybe"
    assert rows[0]["b_errors"] == ""
    assert '"event": "rate_limit_retry"' in outputs.log_file.read_text(encoding="utf-8")


def test_remote_code_screening_does_not_write_reject_when_rate_limit_retries_are_exhausted(tmp_path):
    seed_csv = write_seed_csv(tmp_path)

    try:
        run_remote_code_screening(
            seed_csv,
            output_root=tmp_path,
            run_date="20260617",
            github_client=AlwaysRateLimitedClient(),
            config=RemoteCodeSearchConfig(
                per_page=5,
                max_code_queries_per_repo=1,
                rate_limit_max_retries=1,
                rate_limit_retry_sleep_seconds=0,
                rate_limit_max_sleep_seconds=0,
            ),
        )
    except RemoteScanIncomplete:
        pass
    else:
        raise AssertionError("expected RemoteScanIncomplete")

    rows = list(csv.DictReader((tmp_path / "processed" / "repo-candidates-b.csv").open(encoding="utf-8", newline="")))
    assert rows == []


def test_remote_code_screening_does_not_write_reject_on_access_error(tmp_path):
    seed_csv = write_seed_csv(tmp_path)

    try:
        run_remote_code_screening(
            seed_csv,
            output_root=tmp_path,
            run_date="20260617",
            github_client=AccessDeniedClient(),
            config=RemoteCodeSearchConfig(per_page=5, max_code_queries_per_repo=1),
        )
    except GitHubAccessError:
        pass
    else:
        raise AssertionError("expected GitHubAccessError")

    rows = list(csv.DictReader((tmp_path / "processed" / "repo-candidates-b.csv").open(encoding="utf-8", newline="")))
    assert rows == []


def write_seed_csv(tmp_path):
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
    return seed_csv
