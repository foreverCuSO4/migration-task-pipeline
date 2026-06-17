# G4 Candidate Screening Plan

This document records the planned screening funnel for turning broad GitHub
repository seeds into high-quality G4 external-interface task candidates.

The goal is to avoid cloning or agent-reviewing every seed repository. Each
stage should spend more effort only on repositories that survived cheaper
checks, while preserving enough evidence to explain why a repository was kept
or rejected.

## G4 Candidate Definition

A strong G4 candidate is not just a repository that mentions CUDA. It should
support a contract-backed task:

- A real upstream NVIDIA/CUDA-oriented project.
- Clear CUDA, GPU, or accelerator assumptions in executable code paths.
- A stable external interface such as a CLI, package API, example workflow, or
  test command.
- A reproducible setup, build, or install path.
- A feasible CPU/reference-backend comparison path, because G4 has no Ascend
  oracle.
- Verifier-controlled evidence that migrated code really executes on NPU.
- Hidden cases that can test generalization rather than only public smoke paths.
- Manageable size, dependencies, artifacts, and runtime.

The MACE G4 task is the current positive template: full upstream source, fixed
CLI/API contract, offline checkpoint, CPU-vs-NPU comparison, runtime NPU trace
evidence, and multiple inference/training hidden cases.

## Funnel Overview

The screening pipeline has four levels.

```text
A. Metadata seed screening
   -> B. Remote GitHub code-search screening
   -> C. Local clone heuristic screening
   -> D. Agent-assisted semantic review
```

Stages A and B should favor recall. Stages C and D should increasingly favor
precision. Early stages should avoid hard rejection except for clear negatives.

## Stage A: Metadata Screening

Stage A uses repository metadata, names, topics, tags, descriptions, and seed
keywords. This stage is already mostly covered by the current GitHub Search
seed collector.

Input:

```text
runs/<run>/data/processed/repo-seeds-v0.csv
```

Useful signals:

- Repository name, owner, description, topics, and matched search keywords.
- Stars, forks, license, archived flag, fork flag, pushed date, and repo size.
- Seed provenance and source count.

Good filters:

- Drop archived repositories.
- Drop forks unless there is a specific reason to keep them.
- Drop repositories with unclear or missing license for normal candidates.
- Drop very large repositories when they are unlikely to be taskable.
- Downrank tutorials, awesome lists, papers-only lists, docs-only repos, and
  benchmark result collections.

Output:

```text
data/processed/repo-seeds-v0.csv
```

Stage A should answer:

```text
Does this repository look like it could be relevant?
```

It should not try to prove G4 viability.

## Stage B: Remote Code-Search Screening

Stage B uses GitHub code search without cloning repositories. It confirms that
candidate repositories contain likely executable migration surfaces and basic
taskability signals.

Input:

```text
data/processed/repo-seeds-v0.csv
```

Recommended output:

```text
data/interim/github-code-signals-YYYYMMDD.jsonl
data/processed/repo-candidates-b.csv
```

CUDA/GPU signal queries:

```text
torch.cuda
.cuda(
device="cuda"
device='cuda'
cuda:
CUDAExtension
nvcc
.cu
.cuh
nccl
cudnn
nvidia-smi
cupy
numba.cuda
triton.jit
```

Interface and taskability signal queries:

```text
console_scripts
argparse
click
typer
train.py
eval.py
infer.py
predict.py
benchmark.py
examples/
tests/
pytest
```

Installability signal queries:

```text
pyproject.toml
setup.py
setup.cfg
requirements.txt
environment.yml
CMakeLists.txt
Dockerfile
```

Risk signal queries:

```text
download
wget
gdown
kaggle
wandb
s3://
flash-attn
deepspeed
```

Stage B should produce an evidence score, not only a pass/fail decision. GitHub
code search can miss useful repositories because of indexing, default-branch
limitations, generated code, submodules, or abstraction layers that hide direct
CUDA strings.

Stage B should answer:

```text
Does remote code evidence support spending clone/local-analysis cost?
```

## Stage C: Local Clone Heuristic Screening

Stage C clones or downloads repositories that survived Stage B, then runs
deterministic local analysis without agents.

Input:

```text
data/processed/repo-candidates-b.csv
```

Recommended outputs:

```text
data/interim/local-repo-signals-YYYYMMDD.jsonl
data/processed/repo-candidates-c.csv
```

Recommended local checks:

- Repository size, file count, source LOC, largest files, and binary/model file
  ratio.
- Real source layout versus docs, notebooks, vendor code, or generated code.
- CUDA/GPU signal distribution by path and file type.
- Installability files and package metadata.
- CLI and API entry points.
- Tests, examples, notebooks, and public smoke commands.
- CPU/reference path availability.
- Training and inference entry points.
- Dependency risk and offline feasibility.
- Dataset/model download requirements.
- License and provenance consistency.

Useful tools:

```text
git clone --depth 1
find
grep
cloc or scc if available
python -m compileall for Python syntax sanity
pip install --dry-run or metadata inspection when safe
pytest --collect-only when cheap and dependencies are present
```

Hard rejects should mostly happen here, because local evidence is more reliable
than remote code search.

Common hard reject reasons:

- No real CUDA/GPU/accelerator assumption in executable source.
- No stable interface or verifier entry point can be found.
- Requires private data, private services, or unavoidable large downloads.
- Too large or too complex for a controlled task bundle.
- No plausible CPU/reference comparison path.
- Mostly notebooks or scripts with no reusable package/workflow structure.
- Migration point is too shallow to produce a meaningful benchmark task.

Stage C should answer:

```text
Can this repository plausibly become a bounded, reproducible G4 task?
```

## Stage D: Agent-Assisted Semantic Review

Stage D uses an agent only after cheap and deterministic filters have narrowed
the candidate set. The agent should read the repository and produce a structured
candidate card, not modify code.

Input:

```text
data/processed/repo-candidates-c.csv
```

Recommended outputs:

```text
candidate_cards/YYYYMMDD-g4-screening/<owner>__<repo>.yaml
registry/g4-candidate-shortlist-YYYYMMDD.csv
```

Agent review questions:

- What does the project do?
- Where are the real CUDA/NVIDIA/GPU assumptions?
- What fixed CLI/API/workflow could define the task contract?
- What setup/build/install path would the verifier replay?
- What CPU/reference path can be used for G4 scoring?
- What hidden cases would test generalization?
- What runtime NPU evidence can the verifier control?
- What artifacts must be bundled or replaced with small fixtures?
- What are the main environment, license, data, and dependency risks?
- Is this repo better as full-repo, slice, project-suite, or reject?

Stage D should answer:

```text
Should a human spend task-construction effort on this repository?
```

## Scoring Model

Each stage should keep raw evidence fields and computed scores. A practical
candidate score can be decomposed as:

```text
g4_viability_score =
  cuda_signal_score
  + interface_score
  + installability_score
  + reference_feasibility_score
  + hidden_case_potential_score
  + manageability_score
  + provenance_license_score
  - risk_score
```

The score should be used for ranking and triage, not as a final admission
decision. Final admission still requires human review and at least one pilot run
before a task should be counted as useful.

## Implementation Order

1. Keep Stage A as the existing GitHub Search seed collector.
2. Add Stage B as a GitHub code-search signal scanner over `repo-seeds-v0.csv`.
3. Add Stage C as a local clone heuristic scanner over top Stage B candidates.
4. Add Stage D candidate-card prompts and a batch review runner.
5. Add registry files for accepted, rejected, and known-risk repositories.

## Operational Notes

- Do not clone all 10,000 seed repositories immediately.
- Keep intermediate JSONL evidence files even when producing compact CSV
  rankings.
- Prefer high recall in Stages A and B.
- Prefer explainable hard rejects in Stage C.
- Use agents only after deterministic evidence is available.
- Keep G4 separate from G0/G1/G2 statistics, because G4 has no Ascend oracle and
  uses external-interface scoring.

