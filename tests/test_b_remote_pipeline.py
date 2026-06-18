import csv
import json

import requests

from migration_task_pipeline.buffers import SQLiteBuffer
from migration_task_pipeline.layers.b_remote_code_search.config import RemoteCodeSearchConfig
from migration_task_pipeline.layers.b_remote_code_search.github_client import GitHubAccessError, GitHubRateLimitError
from migration_task_pipeline.layers.b_remote_code_search.pipeline import RemoteScanIncomplete, run_remote_code_screening
from migration_task_pipeline.layers.b_remote_code_search.schema import B_CANDIDATE_COLUMNS


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


class TransientThenOkClient(FakeGitHubRemoteClient):
    def __init__(self):
        self.search_calls = 0

    def search_code(self, query, *, per_page=5, page=1):
        self.search_calls += 1
        if self.search_calls == 1:
            raise requests.exceptions.SSLError("temporary SSL EOF")
        return {
            "items": [
                {"path": "src/cuda_a.py", "html_url": "https://example/src/cuda_a.py"},
                {"path": "src/cuda_b.py", "html_url": "https://example/src/cuda_b.py"},
                {"path": "src/cuda_c.py", "html_url": "https://example/src/cuda_c.py"},
                {"path": "src/cuda_d.py", "html_url": "https://example/src/cuda_d.py"},
            ]
        }


class AlwaysTransientClient(FakeGitHubRemoteClient):
    def search_code(self, query, *, per_page=5, page=1):
        raise requests.exceptions.ConnectionError("temporary connection reset")


class AccessDeniedClient(FakeGitHubRemoteClient):
    def search_code(self, query, *, per_page=5, page=1):
        raise GitHubAccessError("GitHub API access error: HTTP 403")


class RecordingGitHubRemoteClient(FakeGitHubRemoteClient):
    def __init__(self):
        self.tree_repos = []
        self.search_queries = []

    def get_tree(self, repo_key, ref):
        self.tree_repos.append(repo_key)
        return super().get_tree(repo_key, ref)

    def search_code(self, query, *, per_page=5, page=1):
        self.search_queries.append(query)
        return super().search_code(query, per_page=per_page, page=page)


class DecisionGitHubRemoteClient:
    def get_tree(self, repo_key, ref):
        if repo_key == "owner/promote":
            return {
                "tree": [
                    {"type": "blob", "path": "pyproject.toml"},
                    {"type": "blob", "path": "src/train.py"},
                    {"type": "blob", "path": "tests/test_devices.py"},
                    {"type": "blob", "path": "examples/infer.py"},
                ]
            }
        if repo_key == "owner/maybe":
            return {"tree": [{"type": "blob", "path": "src/model.py"}]}
        return {"tree": [{"type": "blob", "path": "README.md"}]}

    def search_code(self, query, *, per_page=5, page=1):
        if "repo:owner/promote" in query:
            if any(term in query for term in ["torch.cuda", ".cuda(", 'device=\\"cuda\\"', "CUDAExtension"]):
                return {
                    "items": [
                        {"path": f"src/cuda_{index}.py", "html_url": f"https://example/src/cuda_{index}.py"}
                        for index in range(4)
                    ]
                }
            if "console_scripts" in query or "argparse.ArgumentParser" in query:
                return {"items": [{"path": "pyproject.toml", "html_url": "https://example/pyproject.toml"}]}
            if "--device cpu" in query or "reference" in query:
                return {
                    "items": [{"path": "tests/test_devices.py", "html_url": "https://example/tests/test_devices.py"}]
                }
        if "repo:owner/maybe" in query and "torch.cuda" in query:
            return {
                "items": [
                    {"path": f"src/cuda_{index}.py", "html_url": f"https://example/src/cuda_{index}.py"}
                    for index in range(4)
                ]
            }
        return {"items": []}


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


def test_remote_code_screening_writes_promote_and_maybe_to_b2c_buffer(tmp_path):
    seed_csv = write_seed_csv_for_repos(tmp_path, ["owner/promote", "owner/maybe", "owner/reject"])

    outputs = run_remote_code_screening(
        seed_csv,
        output_root=tmp_path,
        run_date="20260617",
        github_client=DecisionGitHubRemoteClient(),
        config=RemoteCodeSearchConfig(per_page=5, max_code_queries_per_repo=24),
    )

    assert outputs.b2c_buffer is not None
    assert outputs.b2c_buffer_inserted_count == 2
    buffer = SQLiteBuffer(outputs.b2c_buffer)
    assert buffer.counts_by_status() == {"pending": 2}
    first = buffer.claim_next("test")
    second = buffer.claim_next("test")
    assert first is not None
    assert second is not None
    assert first["repo_key"] == "owner/promote"
    assert first["payload_json"]["b_decision"] == "promote"
    assert second["repo_key"] == "owner/maybe"
    assert second["payload_json"]["b_decision"] == "maybe"


def test_remote_code_screening_resumes_from_existing_candidate_csv_by_default(tmp_path):
    seed_csv = write_seed_csv_for_repos(tmp_path, ["owner/repo", "other/repo"])
    candidates_path = tmp_path / "processed" / "repo-candidates-b.csv"
    candidates_path.parent.mkdir(parents=True)
    with candidates_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=B_CANDIDATE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerow({"repo_key": "owner/repo", "repo_url": "https://github.com/owner/repo", "b_decision": "promote"})
    client = RecordingGitHubRemoteClient()

    outputs = run_remote_code_screening(
        seed_csv,
        output_root=tmp_path,
        run_date="20260617",
        github_client=client,
        config=RemoteCodeSearchConfig(per_page=2, max_code_queries_per_repo=1),
    )

    rows = list(csv.DictReader(candidates_path.open(encoding="utf-8", newline="")))
    assert outputs.resumed_count == 1
    assert outputs.scanned_count == 2
    assert [row["repo_key"] for row in rows] == ["owner/repo", "other/repo"]
    assert client.tree_repos == ["other/repo"]
    assert all("repo:owner/repo" not in query for query in client.search_queries)
    assert '"event": "repo_skipped_resume"' in outputs.log_file.read_text(encoding="utf-8")


def test_remote_code_screening_resume_backfills_b2c_buffer_from_existing_csv(tmp_path):
    seed_csv = write_seed_csv_for_repos(tmp_path, ["owner/repo"])
    candidates_path = tmp_path / "processed" / "repo-candidates-b.csv"
    candidates_path.parent.mkdir(parents=True)
    with candidates_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=B_CANDIDATE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(
            {
                "repo_key": "owner/repo",
                "repo_url": "https://github.com/owner/repo",
                "repo_owner": "owner",
                "repo_name": "repo",
                "b_score": "0.7000",
                "b_decision": "promote",
                "b_reasons": "strong_remote_evidence",
            }
        )

    outputs = run_remote_code_screening(
        seed_csv,
        output_root=tmp_path,
        run_date="20260617",
        github_client=RecordingGitHubRemoteClient(),
        config=RemoteCodeSearchConfig(per_page=2, max_code_queries_per_repo=1),
        limit=0,
    )

    assert outputs.b2c_buffer_backfilled_count == 1
    buffer = SQLiteBuffer(outputs.b2c_buffer)
    claimed = buffer.claim_next("test")
    assert claimed is not None
    assert claimed["repo_key"] == "owner/repo"
    assert claimed["evidence_json"]["resume_source"] == "csv_only"
    assert claimed["evidence_json"]["candidate_csv_row"]["b_decision"] == "promote"


def test_remote_code_screening_resume_backfills_b2c_buffer_from_existing_jsonl(tmp_path):
    seed_csv = write_seed_csv_for_repos(tmp_path, ["owner/repo"])
    signals_path = tmp_path / "interim" / "github-code-signals-20260617.jsonl"
    signals_path.parent.mkdir(parents=True)
    signals_path.write_text(
        json.dumps(
            {
                "repo_key": "owner/repo",
                "repo_url": "https://github.com/owner/repo",
                "github_default_branch": "main",
                "tree_paths": ["src/model.py"],
                "code_hits": [{"group": "cuda", "term": "torch.cuda", "path": "src/model.py"}],
                "errors": [],
                "scores": {
                    "repo_key": "owner/repo",
                    "repo_url": "https://github.com/owner/repo",
                    "repo_owner": "owner",
                    "repo_name": "repo",
                    "b_score": 0.52,
                    "b_decision": "maybe",
                    "b_reasons": ["partial_remote_evidence"],
                },
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    outputs = run_remote_code_screening(
        seed_csv,
        output_root=tmp_path,
        run_date="20260617",
        github_client=RecordingGitHubRemoteClient(),
        config=RemoteCodeSearchConfig(per_page=2, max_code_queries_per_repo=1),
        limit=0,
    )

    assert outputs.b2c_buffer_backfilled_count == 1
    buffer = SQLiteBuffer(outputs.b2c_buffer)
    claimed = buffer.claim_next("test")
    assert claimed is not None
    assert claimed["repo_key"] == "owner/repo"
    assert claimed["payload_json"]["b_decision"] == "maybe"
    assert claimed["evidence_json"]["resume_source"] == "jsonl_only"


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


def test_remote_code_screening_retries_transient_errors_before_scoring(tmp_path):
    seed_csv = write_seed_csv(tmp_path)
    client = TransientThenOkClient()

    outputs = run_remote_code_screening(
        seed_csv,
        output_root=tmp_path,
        run_date="20260617",
        github_client=client,
        config=RemoteCodeSearchConfig(
            per_page=5,
            max_code_queries_per_repo=1,
            transient_error_max_retries=1,
            transient_error_retry_sleep_seconds=0,
            transient_error_max_sleep_seconds=0,
        ),
    )

    rows = list(csv.DictReader(outputs.candidates_csv.open(encoding="utf-8", newline="")))
    assert outputs.scanned_count == 1
    assert client.search_calls == 2
    assert rows[0]["b_decision"] == "maybe"
    assert rows[0]["b_errors"] == ""
    assert '"event": "transient_error_retry"' in outputs.log_file.read_text(encoding="utf-8")


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


def test_remote_code_screening_does_not_write_reject_when_transient_retries_are_exhausted(tmp_path):
    seed_csv = write_seed_csv(tmp_path)

    try:
        run_remote_code_screening(
            seed_csv,
            output_root=tmp_path,
            run_date="20260617",
            github_client=AlwaysTransientClient(),
            config=RemoteCodeSearchConfig(
                per_page=5,
                max_code_queries_per_repo=1,
                transient_error_max_retries=1,
                transient_error_retry_sleep_seconds=0,
                transient_error_max_sleep_seconds=0,
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
    return write_seed_csv_for_repos(tmp_path, ["owner/repo"])


def write_seed_csv_for_repos(tmp_path, repo_keys):
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
        for repo_key in repo_keys:
            owner, repo_name = repo_key.split("/", 1)
            writer.writerow(
                {
                    "repo_key": repo_key,
                    "repo_url": f"https://github.com/{repo_key}",
                    "repo_owner": owner,
                    "repo_name": repo_name,
                    "github_size_kb": str(50 * 1024),
                    "github_archived": "false",
                    "github_default_branch": "main",
                }
            )
    return seed_csv
