import json
from pathlib import Path

from migration_task_pipeline.buffers import BufferItem, SQLiteBuffer
from migration_task_pipeline.layers.d_agent_review.auth import load_opencode_api_key
from migration_task_pipeline.layers.d_agent_review.config import (
    DPathConfig,
    DRuntimeConfig,
    LayerDConfig,
    OpenCodeConfig,
    load_layer_d_config,
)
from migration_task_pipeline.layers.d_agent_review.opencode_runner import (
    OpenCodeCompleted,
    OpenCodeRequest,
    build_opencode_request,
)
from migration_task_pipeline.layers.d_agent_review.pipeline import run_d_agent_review
from migration_task_pipeline.layers.d_agent_review.schema import parse_and_validate_review_card


class FakeOpenCodeRunner:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.requests: list[OpenCodeRequest] = []

    def run(self, request: OpenCodeRequest) -> OpenCodeCompleted:
        self.requests.append(request)
        return OpenCodeCompleted(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


def make_c2_item(repo_key: str, local_path: Path, *, decision: str = "promote", priority: int = 100) -> BufferItem:
    return BufferItem(
        item_id=f"github-url:{repo_key}",
        repo_id=f"github-url:{repo_key}",
        repo_key=repo_key,
        repo_full_name=repo_key,
        repo_url=f"https://github.com/{repo_key}",
        source_layer="C2",
        source_run_id="run",
        payload_version="c2_to_d.v1",
        payload_json={
            "repo_key": repo_key,
            "repo_full_name": repo_key,
            "repo_url": f"https://github.com/{repo_key}",
            "local_path": str(local_path),
            "checkout_sha": "abc123",
            "c2_decision": decision,
        },
        scores_json={"c2_decision": decision, "c2_score": 0.8},
        evidence_json={"c2_evidence": {"local_path": str(local_path), "hits": []}},
        priority=priority,
    )


def make_layer_d_config(tmp_path: Path, *, max_items: int | None = 1) -> LayerDConfig:
    return LayerDConfig(
        opencode=OpenCodeConfig(
            provider_id="d-reviewer",
            provider_name="D Reviewer",
            base_url="https://llm.example.com/v1",
            model="review-model",
            agent_prompt_path=str(tmp_path / "prompt.md"),
            opencode_binary="opencode",
        ),
        runtime=DRuntimeConfig(concurrency=1, max_items=max_items, timeout_seconds=30, max_attempts=1),
        paths=DPathConfig(
            candidate_cards_root=str(tmp_path / "candidate_cards"),
            card_run_name="{date}-test",
            workspace_root=str(tmp_path / "workspaces"),
            logs_dir=str(tmp_path / "logs"),
            mace_reference_path=str(tmp_path / "mace"),
        ),
    )


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "README.md").write_text("# repo\nUses torch.cuda in executable code.\n", encoding="utf-8")
    return path


def make_mace(path: Path) -> Path:
    (path / "tests").mkdir(parents=True)
    (path / "task-spec.md").write_text("MACE spec\n", encoding="utf-8")
    (path / "instruction.md").write_text("MACE instruction\n", encoding="utf-8")
    (path / "provenance.lock").write_text("locked\n", encoding="utf-8")
    (path / "tests" / "evaluate.py").write_text("print('evaluate')\n", encoding="utf-8")
    return path


def valid_review_yaml(repo_key: str = "owner/repo") -> str:
    return f"""
schema_version: g4_review.v1
repo:
  key: "{repo_key}"
  local_path: "/tmp/repo"
  repo_url: "https://github.com/{repo_key}"
  checkout_sha: "abc123"
verdict:
  status: hold
  confidence: medium
  summary: "Potential candidate, needs probe."
  main_reason: "Static evidence suggests a fixed interface may exist."
project_summary:
  what_it_does: "Example project."
  primary_language: "Python"
  package_or_app_shape: "package"
migration_surface:
  overall_assessment: "CUDA appears in executable code."
  depth: moderate
  executable_cuda_signals:
    - path: "candidate_repo/src/train.py"
      lines: "1-10"
      claim: "Uses torch.cuda."
  likely_migration_points:
    - device_dispatch
task_sketch:
  task_shape: slice
  level_tags:
    - L2
  fixed_interface:
    type: cli
    command_or_entrypoint: "python -m repo.train"
    evidence:
      - path: "candidate_repo/README.md"
        lines: "1-2"
        claim: "Documents command."
  allowed_modification_scope: "source only"
  non_goals: []
verifier_feasibility:
  offline_feasible: unclear
  controlled_inputs: []
  required_artifacts: []
  cpu_or_reference_strategy: "Needs probe."
  numerical_or_semantic_outputs: []
  npu_evidence_strategy: "Trace tensor devices."
  estimated_verifier_runtime_minutes: 5
  evidence:
    - path: "candidate_repo/README.md"
      lines: "1-2"
      claim: "Small run may be possible."
hidden_case_plan:
  cases:
    - name: "batch"
      capability_tested: "shape generalization"
      input_variation: "batch size"
      expected_signal: "matching outputs"
  orthogonality_assessment: "Needs a second case."
benchmark_value:
  expected_difficulty: medium
  why_not_trivial: "Device handling is distributed."
  expected_agent_score_spread: "medium"
  likely_failure_modes: []
risks:
  - type: dependency
    severity: medium
    description: "Dependencies need confirmation."
    evidence:
      - path: "candidate_repo/README.md"
        lines: "1-2"
        claim: "Install path is documented."
    mitigation: "Manual probe."
manual_probe:
  first_probe: "Inspect CLI help."
  success_criterion: "CLI loads offline."
  commands_to_consider:
    - "python -m repo.train --help"
  blockers_to_resolve: []
reviewer_notes:
  zh:
    overall_opinion: "需要进一步确认。"
    task_design_comments: "接口可能可固定。"
    comparison_to_known_tasks: "参考 MACE 的离线验证强度。"
    concerns_or_alternatives: "依赖风险。"
    confidence_rationale: "静态证据有限。"
  en:
    overall_opinion: "Needs further confirmation."
    task_design_comments: "The interface may be fixed."
    comparison_to_known_tasks: "Compare against MACE's verifier strength."
    concerns_or_alternatives: "Dependency risk."
    confidence_rationale: "Static evidence is limited."
open_questions: []
"""


def test_load_opencode_api_key_from_auth_json(tmp_path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps({"opencode_api_keys": {"d-reviewer": "secret-key"}}),
        encoding="utf-8",
    )

    assert load_opencode_api_key(auth_file, provider_id="d-reviewer") == "secret-key"


def test_layer_d_config_defaults_to_one_promote_item(tmp_path):
    config_file = tmp_path / "layer-d.yaml"
    config_file.write_text(
        """
opencode:
  base_url: https://llm.example.com/v1
  model: review-model
""",
        encoding="utf-8",
    )

    config = load_layer_d_config(config_file)

    assert config.runtime.concurrency == 1
    assert config.runtime.max_items == 1
    assert config.selection.decisions == ["promote"]


def test_build_opencode_request_keeps_api_key_out_of_command(tmp_path):
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("review", encoding="utf-8")
    config = OpenCodeConfig(base_url="https://llm.example.com/v1", model="review-model")

    request = build_opencode_request(
        config=config,
        api_key="secret-key",
        workspace_dir=tmp_path,
        prompt_text="review this",
        agent_prompt="review",
        external_allow_paths=[tmp_path],
        timeout_seconds=10,
        base_env={},
    )

    assert "secret-key" not in " ".join(request.command)
    assert request.env["MTP_OPENCODE_API_KEY"] == "secret-key"
    assert "{env:MTP_OPENCODE_API_KEY}" in request.env["OPENCODE_CONFIG_CONTENT"]


def test_parse_and_validate_review_card_accepts_valid_yaml():
    card = parse_and_validate_review_card(valid_review_yaml())

    assert card.payload["schema_version"] == "g4_review.v1"
    assert card.payload["verdict"]["status"] == "hold"


def test_d_pipeline_writes_card_logs_and_marks_done(tmp_path):
    run_root = tmp_path / "runs" / "example"
    repo = make_repo(tmp_path / "repo")
    make_mace(tmp_path / "mace")
    (tmp_path / "prompt.md").write_text("review prompt", encoding="utf-8")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps({"opencode_api_keys": {"d-reviewer": "secret-key"}}), encoding="utf-8")
    buffer = SQLiteBuffer(run_root / "buffers" / "c2_to_d.sqlite")
    buffer.insert_item(make_c2_item("owner/repo", repo))
    fake = FakeOpenCodeRunner(stdout=json.dumps({"type": "message", "content": valid_review_yaml("owner/repo")}))

    outputs = run_d_agent_review(
        run_root=run_root,
        config=make_layer_d_config(tmp_path),
        auth_path=auth_file,
        opencode_runner=fake,
    )

    assert outputs.claimed_count == 1
    assert outputs.reviewed_count == 1
    assert buffer.counts_by_status() == {"done": 1}
    cards = list((tmp_path / "candidate_cards").glob("*/*.yaml"))
    assert len(cards) == 1
    assert "schema_version: g4_review.v1" in cards[0].read_text(encoding="utf-8")
    assert (tmp_path / "logs" / "owner__repo.jsonl").exists()
    assert (tmp_path / "logs" / "owner__repo.log").exists()
    assert "secret-key" not in (tmp_path / "logs" / "owner__repo.log").read_text(encoding="utf-8")
    workspace = tmp_path / "workspaces" / "owner__repo"
    assert (workspace / "candidate_repo").is_symlink()
    assert (workspace / "mace_reference").is_symlink()
    assert fake.requests


def test_d_pipeline_defaults_do_not_process_maybe(tmp_path):
    run_root = tmp_path / "runs" / "example"
    repo = make_repo(tmp_path / "repo")
    make_mace(tmp_path / "mace")
    (tmp_path / "prompt.md").write_text("review prompt", encoding="utf-8")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps({"opencode_api_keys": {"d-reviewer": "secret-key"}}), encoding="utf-8")
    buffer = SQLiteBuffer(run_root / "buffers" / "c2_to_d.sqlite")
    buffer.insert_item(make_c2_item("owner/repo", repo, decision="maybe", priority=999))
    fake = FakeOpenCodeRunner(stdout=valid_review_yaml("owner/repo"))

    outputs = run_d_agent_review(
        run_root=run_root,
        config=make_layer_d_config(tmp_path),
        auth_path=auth_file,
        opencode_runner=fake,
    )

    assert outputs.claimed_count == 0
    assert buffer.counts_by_status() == {"pending": 1}
    assert fake.requests == []
