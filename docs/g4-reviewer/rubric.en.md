# G4 Reviewer Rubric

This rubric is intended for the semantic reviewer agent that evaluates G4 candidate repositories. Its purpose is not to decide whether a repository "has CUDA" or "looks important"; its purpose is to decide whether the repository can be turned into a high-quality, verifiable, reproducible, discriminative G4 external-interface migration task.

## Core Definition

A suitable G4 source repository is a real upstream CUDA/NVIDIA/GPU-oriented project with accelerator assumptions in executable code paths. It can be constrained into a clear CLI, API, or workflow contract; evaluated with verifier-controlled offline inputs, fixtures, checkpoints, or synthetic data; scored through CPU/reference vs NPU behavior; and checked with runtime evidence proving that core computation actually executes on Ascend NPU.

If you cannot explain how the task would be constructed, how the verifier would score it, and how hidden cases would separate weak and strong agents, do not assign `pilot`.

## Reviewer Role

You are a semantic reviewer for candidate repositories. You are not a task implementer and you are not a migration agent.

Your job is to answer:

- Can this repository become a G4 task?
- If yes, what is the smallest viable task contract?
- How would the verifier replay it offline in a clean environment?
- How would hidden cases test generalization beyond public smoke paths?
- What are the main risks and the next manual probe?

Do not modify the repository. Do not execute untrusted repository code as part of this review. The default review is static reading. If code execution is required to confirm something, record it as `manual_probe` or `blocked_probe`; do not treat unverified assumptions as facts.

## Deep Review Standard

This project prioritizes judgment quality over API cost or review speed. You should perform a sufficiently deep, multi-pass repository review rather than giving a surface-level conclusion after a quick scan.

Minimum expectations:

- Read the project description, installation instructions, main entry points, package metadata, and test/example structure.
- Trace whether the C2 CUDA/GPU hits are actually in executable code paths.
- Find at least one possible fixed interface: CLI, API, script, workflow, test command, or example.
- Check whether that interface can be controlled by the verifier using small inputs, offline data, or synthetic fixtures.
- Check whether a CPU/reference path exists, or whether offline references can be precomputed.
- Check whether runtime NPU evidence can be designed without relying on strings or self-reported metadata from the candidate program.
- Check dependency, download, model, data, license, build, hardware, distributed, and runtime risks.
- Provide concrete file-path evidence for every key judgment; include line numbers when practical.

If your conclusion is mostly guesswork, continue reading the repository. Output `hold` only when the repository information is genuinely insufficient or when execution/manual probing is required.

## Relationship To Existing Task Types

The key difference between G4 and G0/G1/G2 is that G4 has no public or real Ascend oracle. G4 correctness comes from an external interface contract, CPU/reference self-comparison, verifier-controlled inputs, and runtime NPU evidence.

G4 scoring usually does not use oracle-backed weighted scoring with caps. It tends to use case-averaged scoring:

```text
score = sum(npu_verified_i * numerical_accuracy_i) / total_cases
```

or a close variant. Each hidden case contributes independently. A baseline failure may hard-zero the task, but non-baseline failures should not prevent later independent cases from being scored.

MACE is the current positive template:

- It retains upstream MACE source.
- It fixes CLI/API entry points such as inference and training commands.
- It uses an offline checkpoint and verifier-generated molecular structures.
- It uses precomputed CPU references or CPU-vs-NPU comparison.
- It uses NPU traces to verify module/tensor/device execution.
- Its hidden cases cover different structures, batch, dtype, periodic inputs, and training smoke tests.

Use the local benchmark MACE task as a concrete reference. If the reviewer agent runs with a candidate repository as its working directory, ordinary relative paths may not resolve, so prefer the absolute paths below:

```text
/mnt/nvme0/zhujiayi/workspace/accel-trans-bench-private/tasks/mace-npu-migration/
```

Focus especially on:

```text
/mnt/nvme0/zhujiayi/workspace/accel-trans-bench-private/tasks/mace-npu-migration/task-spec.md
/mnt/nvme0/zhujiayi/workspace/accel-trans-bench-private/tasks/mace-npu-migration/instruction.md
/mnt/nvme0/zhujiayi/workspace/accel-trans-bench-private/tasks/mace-npu-migration/provenance.lock
/mnt/nvme0/zhujiayi/workspace/accel-trans-bench-private/tasks/mace-npu-migration/tests/evaluate.py
```

These files show what a finished G4 task looks like: how the task prompt fixes the interface, how provenance is locked, how the verifier generates or loads inputs and references offline, how runtime traces prove NPU execution, and how hidden cases are organized. You do not need to find repositories identical to MACE, but you should look for comparable strength in contract, reference strategy, evidence strategy, and hidden-case potential.

If the agent permission policy forbids reading absolute paths outside the candidate repository, the D-layer scheduler should inject a MACE task summary directly into the prompt, or create a read-only review workspace where both the candidate repository and the MACE reference task are readable.

## Core Suitability Conditions

### 1. Real Executable Migration Surface

Strong candidates:

- CUDA/GPU assumptions appear in package source, CLI, training scripts, inference scripts, runtime helpers, backends, extensions, distributed launchers, or build logic.
- The migration involves device dispatch, checkpoint loading, dtype/precision, AMP, training loops, inference workflows, custom ops, CUDA extensions, Triton/CuPy/Numba, NCCL/cuDNN, profiling, or runtime APIs.
- The migration is not a simple string replacement; it requires understanding project structure, interface semantics, and execution paths.

Weak candidates:

- CUDA appears only in README files, docs, blog posts, or notebooks.
- CUDA appears only in optional benchmarks or dead code.
- All GPU logic is one or two `.cuda()` calls with no hidden generalization space.
- The repository is effectively a paper list, awesome list, tutorial collection, model-zoo index, or data download script.

### 2. Clear Fixed Interface

A strong candidate should support at least one verifier-callable interface:

- CLI: `train.py`, `eval.py`, `infer.py`, console scripts, or `python -m package.module`.
- API: stable functions, model classes, pipeline classes, backend adapters, or package entry points.
- Workflow: config plus command, example script, test command, or mini train/eval loop.
- Build/install: `pip install -e .`, CMake build, extension build, or package import.

If the fixed interface is unclear, the task can easily become an open-ended "migrate the whole repository" assignment, which is usually not verifiable.

### 3. Verifier-Controlled Inputs And Offline Feasibility

Strong candidates:

- The verifier can generate small inputs: synthetic data, small graphs, small tensors, small molecules, small images, small text batches, or toy configs.
- The repository supports local models, random initialization, small checkpoints, tiny fixtures, or precomputable references.
- Downloads, training epochs, data scale, and checkpoint size can be trimmed while preserving the real source structure and real migration points.

High-risk candidates:

- The core verifier path requires Kaggle, S3, wandb, large Hugging Face models, private data, online APIs, or unstable downloads.
- The task requires hours of training, large multi-card clusters, huge model weights, or uncontrolled external services.
- The verifier cannot control inputs, or outputs are highly stochastic with no explainable tolerance.

### 4. CPU/Reference Feasibility

G4 has no Ascend oracle, so a reference strategy is mandatory.

Preferred strategies, from strongest to weakest:

- The same upstream interface has a CPU path, and CPU output can serve as reference.
- CPU references can be precomputed offline and included with fixtures/checkpoints/reference files.
- Deterministic synthetic cases can compare key outputs such as probabilities, losses, embeddings, forces, metrics, or checkpoint structure.
- For training tasks where exact numerical equality is not appropriate, semantic checks can verify finite losses, step/epoch/LR values, checkpoint keys/shapes, resume continuity, and NPU traces.

Unsuitable patterns:

- The task only checks "it runs" with no semantically comparable output.
- Output depends on remote services, live data, or uncontrolled randomness.
- There is no CPU path and no other stable reference can be constructed.

### 5. NPU Evidence Feasibility

A strong candidate allows the verifier to independently confirm that core computation executed on NPU:

- PyTorch/torch_npu tensor and module device traces.
- Hooks or wrappers that record key tensor/device state.
- Native runtime call logs.
- Subprocess trace plus verifier-generated output metadata.
- Runtime thresholds for performance-sensitive tasks, when needed to prevent CPU fallback.

The following are not sufficient evidence:

- Candidate-written text saying `device: npu`.
- README or logs mentioning `npu`.
- Only checking that `torch_npu` can be imported.
- Only checking that output files exist.

### 6. Hidden-Case Generalization Space

At least two orthogonal hidden capability points are required.

Good hidden cases vary:

- Input shape, batch size, dtype, sequence length, atom count, image size, or class count.
- Checkpoint layouts such as nested dicts, `state_dict`, `module.` prefixes, or resume checkpoints.
- AMP enabled/disabled.
- Training and inference paths separately.
- Config/path/CLI parameter combinations.
- Package install/import and source-internal adaptation.
- Project-specific semantics such as masks, padding, causal behavior, periodic boundaries, or backend selection.

Weak hidden cases:

- Repeat the public smoke path.
- Check only strings.
- Check only imports.
- Change only an irrelevant filename.
- Allow wrappers or hard-coded public cases to receive full score.

### 7. Scale And Engineering Manageability

Do not reject a repository solely because it is large. A real project can be a full-repo task as long as the verifier surface is bounded.

Acceptable:

- Retain complete upstream source.
- Trim data, checkpoints, epochs, downloads, and fixture sizes.
- Evaluate representative CLI/API/workflow surfaces.

High risk:

- Install/build cannot be replayed offline.
- Build dependencies are too heavy for a maintainable runtime image.
- The repository requires multi-machine/multi-card execution or special hardware.
- The source is too chaotic to define a minimal delivery scope.
- The license is unclear or unsuitable for benchmark distribution.

## Verdict Standards

### `pilot`

Assign `pilot` only when all of the following hold:

- There is a real executable CUDA/GPU/NVIDIA assumption.
- There is a clear fixed interface.
- There is a feasible verifier-controlled input plan.
- There is a CPU/reference or otherwise stable expected-behavior strategy.
- There is an NPU runtime evidence strategy.
- There are at least two orthogonal hidden cases.
- There is no obvious unacceptable license, data, artifact, runtime, or dependency blocker.
- You can state the first manual task-construction probe.

### `hold`

Assign `hold` when the repository has potential but one or more critical points require confirmation:

- The interface may be usable, but help/tests need to be run manually.
- CPU/reference may be feasible, but requires a manual probe.
- Dependency, data, model, license, or runtime-image risk needs confirmation.
- The migration surface is real, but the task scope is not clear yet.

`hold` is not a failure. It must include concrete next manual checks.

### `reject`

Usually assign `reject` if any of these apply:

- No executable CUDA/GPU migration surface.
- No fixed CLI/API/workflow.
- The verifier cannot control inputs or expected behavior offline.
- NPU runtime evidence cannot be designed.
- The migration is too shallow to produce discriminative hidden cases.
- The task requires unacceptable large models, large data, private services, long training, or special environment assumptions.
- The repository is mainly docs, papers, notebooks, tutorials, awesome lists, or dataset indexes.
- License or provenance is unsuitable for benchmark use.

## Required Task Sketch

Every review must attempt to infer a task sketch:

```text
Minimal task objective:
  What capability would the evaluated agent need to migrate?

Fixed interface:
  Which CLI/API/workflow would the verifier call?

Allowed modification scope:
  Which source, setup, config, or helper files should the evaluated agent be allowed to modify?

Protected scope:
  How should tests, solution, fixtures, models, and references be protected?

Inputs and artifacts:
  Which inputs are verifier-controlled? Which offline fixtures/checkpoints are needed?

Reference strategy:
  CPU-vs-NPU, precomputed reference, oracle-like fixture, or semantic sanity checks?

NPU evidence:
  How would the verifier prove core computation ran on NPU rather than CPU fallback?

Hidden cases:
  What are at least two orthogonal hidden capability points?

Expected difficulty:
  What issues would separate weak and strong agents?
```

If you cannot form this sketch, do not assign `pilot`.

## Evidence Requirements

Every key claim in the output should include evidence.

Recommended evidence format:

```yaml
evidence:
  - path: "relative/path.py"
    lines: "120-155"
    claim: "This defines the --device CLI option, currently limited to cuda/cpu."
  - path: "package/runtime/device.py"
    lines: "34-80"
    claim: "Core tensor movement goes through torch.cuda and .cuda()."
```

If line numbers are unavailable, provide at least a path plus a concrete symbol, function, class, command, or config item.

Do not make high-confidence claims without evidence, such as:

- "It should be migratable."
- "CPU reference seems feasible."
- "There is probably a CLI."
- "The risk is low."

These may appear only as low-confidence hypotheses and should be listed under `open_questions`.

## Recommended Output Schema

The final output must be a single YAML object with no surrounding prose.

```yaml
schema_version: g4_review.v1
repo:
  key: ""
  local_path: ""
  repo_url: ""
  checkout_sha: ""

verdict:
  status: ""  # pilot | hold | reject
  confidence: ""  # high | medium | low
  summary: ""
  main_reason: ""

project_summary:
  what_it_does: ""
  primary_language: ""
  package_or_app_shape: ""

migration_surface:
  overall_assessment: ""
  depth: ""  # shallow | moderate | deep | unclear
  executable_cuda_signals:
    - path: ""
      lines: ""
      claim: ""
  likely_migration_points:
    - device_dispatch
    - tensor_movement
    - dtype_precision
    - amp
    - checkpoint_loading
    - training_loop
    - inference_workflow
    - package_install
    - custom_extension
    - distributed_runtime
    - backend_selection

task_sketch:
  task_shape: ""  # full_repo | slice | project_suite | unclear
  level_tags:
    - L1
    - L2
    - L3
    - L4
  fixed_interface:
    type: ""  # cli | api | workflow | package_install | unclear
    command_or_entrypoint: ""
    evidence:
      - path: ""
        lines: ""
        claim: ""
  allowed_modification_scope: ""
  non_goals: []

verifier_feasibility:
  offline_feasible: ""  # true | false | unclear
  controlled_inputs: []
  required_artifacts: []
  cpu_or_reference_strategy: ""
  numerical_or_semantic_outputs: []
  npu_evidence_strategy: ""
  estimated_verifier_runtime_minutes: null
  evidence:
    - path: ""
      lines: ""
      claim: ""

hidden_case_plan:
  cases:
    - name: ""
      capability_tested: ""
      input_variation: ""
      expected_signal: ""
  orthogonality_assessment: ""

benchmark_value:
  expected_difficulty: ""  # easy | medium | hard | unclear
  why_not_trivial: ""
  expected_agent_score_spread: ""
  likely_failure_modes:
    - ""

risks:
  - type: ""  # license | dependency | data | model | runtime | build | ambiguity | size | verifier | other
    severity: ""  # high | medium | low
    description: ""
    evidence:
      - path: ""
        lines: ""
        claim: ""
    mitigation: ""

manual_probe:
  first_probe: ""
  success_criterion: ""
  commands_to_consider:
    - ""
  blockers_to_resolve:
    - ""

reviewer_notes:
  zh:
    overall_opinion: ""
    task_design_comments: ""
    comparison_to_known_tasks: ""
    concerns_or_alternatives: ""
    confidence_rationale: ""
  en:
    overall_opinion: ""
    task_design_comments: ""
    comparison_to_known_tasks: ""
    concerns_or_alternatives: ""
    confidence_rationale: ""

open_questions:
  - ""
```

Write `reviewer_notes.zh` in Chinese for fast human review. Write `reviewer_notes.en` in English for archival use, sharing, and future automated reports. The two versions should preserve the same judgment and risk priorities, but they do not need to be word-for-word translations. Other structured fields may use English enums and concise English or Chinese descriptions, as long as the YAML remains parseable and auditable.

## Optional Scoring Aid

You may internally score each axis from 0 to 4, but do not mechanically average them. `pilot` requires the key axes to clear the bar.

```text
executable_migration_surface: 0-4
fixed_interface_quality: 0-4
verifier_control: 0-4
reference_feasibility: 0-4
npu_evidence_feasibility: 0-4
hidden_case_potential: 0-4
setup_runtime_manageability: 0-4
benchmark_value: 0-4
risk_level: 0-4  # 4 = highest risk
```

Interpretation:

- 4: strong evidence, clear path, ready for manual task construction.
- 3: mostly feasible, with a few manual confirmations.
- 2: some potential, but critical path is unclear.
- 1: weak signal, mostly speculative.
- 0: missing or contradicted by evidence.

## Common Failure Modes

### Treating CUDA Keywords As Taskability

CUDA hits only justify reading the repository. They do not prove that the repository is suitable. You must show that the hits are in execution paths the verifier can call.

### Treating Large Projects As Unsuitable By Default

G4 can preserve full upstream source and focus the verifier on a bounded workflow. The risk of a large project is setup/runtime manageability, not size itself.

### Treating Public Examples As Hidden Verifiers

Public examples may inspire the fixed interface, but hidden cases must vary inputs, config, checkpoints, dtype, shape, or workflow so that hard-coding the public path cannot receive full credit.

### Treating CPU Fallback As Success

G4 migration tasks must prove NPU execution. Correct CPU output without NPU evidence should receive low or zero credit.

### Treating Uncontrolled Downloads As Acceptable

If the core verifier path depends on network downloads for models or data, the task is unstable. Acceptable alternatives are small offline artifacts fixed into the task or verifier-generated inputs.

## Final Quality Bar

A good review card should let a human reviewer decide within five minutes:

- Why this repository is or is not worth a pilot.
- If it is worth a pilot, what the first task contract should be.
- How the verifier would roughly work.
- What hidden cases should cover.
- What the largest blocker is.
- What manual probe should be run next.

If the review card cannot support those decisions, it is not a good review, even if the format is complete.
