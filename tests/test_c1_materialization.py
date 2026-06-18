import json
from pathlib import Path
import shutil
import subprocess

import pytest

from migration_task_pipeline.buffers import BufferItem, SQLiteBuffer
from migration_task_pipeline.layers.c1_local_materialization.config import load_layer_c1_config
from migration_task_pipeline.layers.c1_local_materialization.pipeline import (
    MANIFEST_FILENAME,
    ensure_cloned,
    git_clone_env,
    local_repo_path,
    load_optional_token_pool,
    repo_key_slug,
    run_c1_materialization,
)
from migration_task_pipeline.layers.c1_local_materialization.registry import LocalRepoRecord, LocalRepoRegistry
from scripts.materialize_repos_c1 import build_layer_config


def make_b_item(repo_key: str, repo_url: str, *, priority: int = 1000) -> BufferItem:
    return BufferItem(
        item_id=f"github-url:{repo_key}",
        repo_id=f"github-url:{repo_key}",
        repo_key=repo_key,
        repo_full_name=repo_key,
        repo_url=repo_url,
        source_layer="B",
        source_run_id="run",
        payload_version="b_to_c.v1",
        payload_json={"repo_key": repo_key, "repo_url": repo_url, "b_decision": "promote"},
        scores_json={"b_score": 0.7, "b_decision": "promote"},
        evidence_json={"source": "test"},
        priority=priority,
    )


def test_load_layer_c1_config_and_cli_overrides(tmp_path):
    config_path = tmp_path / "layer-c1.yaml"
    config_path.write_text(
        """
materialization:
  clone_depth: 2
  clone_timeout_seconds: 30
  lease_seconds: 45
  retry_priority: -5
  submodules: true
  lfs: true
  proxy:
    http: http://proxy.local:8080
    https: https://proxy.local:8443
    all: socks5://proxy.local:1080
    no_proxy: localhost,127.0.0.1
runtime:
  concurrency: 3
  max_items: 9
""",
        encoding="utf-8",
    )

    config = load_layer_c1_config(config_path)

    assert config.materialization.clone_depth == 2
    assert config.materialization.clone_timeout_seconds == 30
    assert config.materialization.lease_seconds == 45
    assert config.materialization.retry_priority == -5
    assert config.materialization.submodules is True
    assert config.materialization.lfs is True
    assert config.materialization.http_proxy == "http://proxy.local:8080"
    assert config.materialization.https_proxy == "https://proxy.local:8443"
    assert config.materialization.all_proxy == "socks5://proxy.local:1080"
    assert config.materialization.no_proxy == "localhost,127.0.0.1"
    assert config.runtime.concurrency == 3
    assert config.runtime.max_items == 9

    args = type(
        "Args",
        (),
        {
            "clone_depth": 1,
            "clone_timeout": None,
            "lease_seconds": None,
            "retry_priority": 0,
            "submodules": False,
            "lfs": None,
            "http_proxy": "",
            "https_proxy": "https://override.local:9443",
            "all_proxy": None,
            "no_proxy": None,
        },
    )()
    overridden = build_layer_config(config, args)
    assert overridden.materialization.clone_depth == 1
    assert overridden.materialization.clone_timeout_seconds == 30
    assert overridden.materialization.retry_priority == 0
    assert overridden.materialization.submodules is False
    assert overridden.materialization.lfs is True
    assert overridden.materialization.http_proxy == ""
    assert overridden.materialization.https_proxy == "https://override.local:9443"
    assert overridden.materialization.all_proxy == "socks5://proxy.local:1080"
    assert overridden.materialization.no_proxy == "localhost,127.0.0.1"


def test_c1_loads_clone_tokens_from_auth_file(tmp_path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        '{"github_tokens": [{"name": "a", "token": "tok-a"}, {"name": "b", "token": "tok-b"}]}',
        encoding="utf-8",
    )

    pool = load_optional_token_pool(auth_file)

    assert pool is not None
    assert [pool.next_token().token for _ in range(3)] == ["tok-a", "tok-b", "tok-a"]


def test_git_clone_env_applies_proxy_overrides(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://system.proxy")
    config = load_layer_c1_config(Path("configs/layer-c1.example.yaml")).materialization
    env = git_clone_env(config)
    assert env["HTTPS_PROXY"] == "https://system.proxy"

    overridden = type(
        "Config",
        (),
        {
            **config.__dict__,
            "https_proxy": "https://configured.proxy",
        },
    )()
    env = git_clone_env(overridden)
    assert env["HTTPS_PROXY"] == "https://configured.proxy"
    assert env["https_proxy"] == "https://configured.proxy"


def test_ensure_cloned_uses_askpass_for_token_without_putting_token_in_command(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, text, capture_output, timeout, env, check):
        calls.append({"command": command, "env": env})
        (tmp_path / "clone" / ".git").mkdir(parents=True)
        return type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    config = load_layer_c1_config(Path("configs/layer-c1.example.yaml")).materialization

    ensure_cloned("https://github.com/owner/repo", tmp_path / "clone", config, token_value="secret-token")

    assert calls
    assert "secret-token" not in " ".join(calls[0]["command"])
    assert calls[0]["env"]["GITHUB_TOKEN_FOR_ASKPASS"] == "secret-token"
    assert calls[0]["env"]["GIT_ASKPASS"]


def test_local_repo_registry_upserts_records(tmp_path):
    registry = LocalRepoRegistry(tmp_path / "local-repos.sqlite")
    registry.upsert(
        LocalRepoRecord(
            repo_id="repo-1",
            repo_key="owner/repo",
            full_name="owner/repo",
            repo_url="https://github.com/owner/repo",
            clone_url="https://github.com/owner/repo",
            run_id="run",
            buffer_item_id="repo-1",
            local_path="/tmp/repo",
            clone_status="cloned",
            checkout_sha="abc",
            disk_bytes=10,
            file_count=1,
        )
    )
    registry.upsert(
        LocalRepoRecord(
            repo_id="repo-1",
            repo_key="owner/repo",
            full_name="owner/repo",
            repo_url="https://github.com/owner/repo",
            clone_url="https://github.com/owner/repo",
            run_id="run",
            buffer_item_id="repo-1",
            local_path="/tmp/repo",
            clone_status="failed",
            error_message="temporary",
        )
    )

    row = registry.get("repo-1")
    assert row is not None
    assert row["clone_status"] == "failed"
    assert row["error_message"] == "temporary"
    assert registry.counts_by_status() == {"failed": 1}


def test_local_repo_path_is_stable_and_readable(tmp_path):
    first = local_repo_path(tmp_path, repo_key="Owner/Repo.Name", repo_url="https://github.com/owner/repo", item_id="x")
    second = local_repo_path(tmp_path, repo_key="Owner/Repo.Name", repo_url="https://github.com/owner/repo", item_id="x")

    assert first == second
    assert first.name.startswith("owner__repo.name--")
    assert repo_key_slug("Owner/Repo Name") == "owner__repo-name"


def test_c1_materializes_multiple_repos_concurrently(tmp_path):
    require_git()
    run_root = tmp_path / "runs" / "example"
    input_buffer = SQLiteBuffer(run_root / "buffers" / "b_to_c.sqlite")
    source_one = create_git_repo(tmp_path / "source-one", "one.py")
    source_two = create_git_repo(tmp_path / "source-two", "two.py")
    input_buffer.insert_item(make_b_item("owner/one", source_one.as_uri(), priority=20))
    input_buffer.insert_item(make_b_item("owner/two", source_two.as_uri(), priority=10))

    outputs = run_c1_materialization(run_root=run_root, concurrency=2)

    assert outputs.claimed_count == 2
    assert outputs.cloned_count == 2
    assert outputs.failed_count == 0
    assert outputs.enqueued_count == 2
    assert input_buffer.counts_by_status() == {"done": 2}
    output_buffer = SQLiteBuffer(run_root / "buffers" / "c1_to_c2.sqlite")
    assert output_buffer.counts_by_status() == {"pending": 2}

    first = output_buffer.claim_next("test")
    assert first is not None
    payload = first["payload_json"]
    local_path = Path(payload["local_path"])
    assert local_path.exists()
    assert (local_path / MANIFEST_FILENAME).exists()
    assert payload["checkout_sha"]
    assert payload["clone_depth"] == 1
    manifest = json.loads((local_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["source_buffer"] == "b_to_c"

    registry = LocalRepoRegistry(outputs.registry)
    assert registry.counts_by_status() == {"cloned": 2}


def test_c1_requeues_failed_clone_with_retry_priority(tmp_path):
    require_git()
    run_root = tmp_path / "runs" / "example"
    input_buffer = SQLiteBuffer(run_root / "buffers" / "b_to_c.sqlite")
    input_buffer.insert_item(make_b_item("owner/missing", (tmp_path / "missing").as_uri(), priority=100))

    outputs = run_c1_materialization(run_root=run_root, max_items=1)

    assert outputs.claimed_count == 1
    assert outputs.cloned_count == 0
    assert outputs.failed_count == 1
    assert SQLiteBuffer(run_root / "buffers" / "c1_to_c2.sqlite").counts_by_status() == {}
    item = input_buffer.get_item("github-url:owner/missing")
    assert item is not None
    assert item["status"] == "pending"
    assert item["priority"] == 0
    assert item["last_error"]
    registry = LocalRepoRegistry(outputs.registry)
    assert registry.counts_by_status() == {"failed": 1}


def require_git():
    if shutil.which("git") is None:
        pytest.skip("git executable is required")


def create_git_repo(path: Path, filename: str) -> Path:
    path.mkdir(parents=True)
    run_git(["git", "init"], cwd=path)
    run_git(["git", "config", "user.email", "test@example.com"], cwd=path)
    run_git(["git", "config", "user.name", "Test User"], cwd=path)
    (path / filename).write_text("print('hello')\n", encoding="utf-8")
    run_git(["git", "add", filename], cwd=path)
    run_git(["git", "commit", "-m", "initial"], cwd=path)
    return path


def run_git(command: list[str], *, cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
