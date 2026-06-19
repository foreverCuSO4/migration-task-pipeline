# Current Pipeline Progress

This document records the current implementation and run state of the
repository screening pipeline for G4 task candidate discovery.

Snapshot date: 2026-06-19

## Implemented Stages

The current pipeline has these implemented units:

```text
A. GitHub metadata seed collection
B. Remote GitHub code-search screening
C1. Local repository materialization
C2. Local heuristic screening
```

The durable buffer flow is:

```text
B -> runs/<run>/buffers/b_to_c.sqlite
C1 -> runs/<run>/buffers/c1_to_c2.sqlite
C2 -> runs/<run>/buffers/c2_to_d.sqlite
```

All implemented post-A stages are designed to stream progress to disk. A stage
should not accumulate large in-memory result sets before writing outputs.

## Current Run State

Main run:

```text
runs/20260617-003331-github-goal-10000
```

Layer C1 cloned repositories into:

```text
runs/20260617-003331-github-goal-10000/repos/
```

At the latest check, the local repo directory was roughly:

```text
92G
```

C2 output files:

```text
runs/20260617-003331-github-goal-10000/data/processed/repo-candidates-c2.csv
runs/20260617-003331-github-goal-10000/data/interim/local-heuristic-evidence-20260619.jsonl
runs/20260617-003331-github-goal-10000/data/logs/c2-local-screening-20260619.log
runs/20260617-003331-github-goal-10000/buffers/c2_to_d.sqlite
```

C2 has processed all available C1 output items:

```text
C1->C2 input buffer:
  done      1108
  rejected   412

C2->D output buffer:
  pending   1108
```

C2 candidate decisions:

```text
promote   107
maybe    1001
reject    412
total    1520
```

C2 score distribution:

```text
>=0.80       8
0.70-0.80   99
0.60-0.70  245
0.50-0.60  351
0.40-0.50  412
0.30-0.40  261
<0.30      144
```

Score summary:

```text
overall mean    0.4873
median          0.4875
p90             0.6775
max             0.8800

promote mean    0.7450
maybe mean      0.5148
reject mean     0.3537
```

Top C2 repositories by score:

| Rank | Repo | Score | Decision |
|---:|---|---:|---|
| 1 | `enlite-ai/maze` | 0.8800 | promote |
| 2 | `4ment/torchtree` | 0.8600 | promote |
| 3 | `huggingface/diffusers` | 0.8475 | promote |
| 4 | `bhoov/exbert` | 0.8375 | promote |
| 5 | `intel/intel-xpu-backend-for-triton` | 0.8225 | promote |
| 6 | `datawhalechina/joyrl` | 0.8150 | promote |
| 7 | `dptech-corp/uni-core` | 0.8100 | promote |
| 8 | `cuiziteng/illumination-adaptive-transformer` | 0.8000 | promote |
| 9 | `cnstark/easytorch` | 0.7975 | promote |
| 10 | `google-deepmind/xmanager` | 0.7975 | promote |
| 11 | `ethanhe42/kl-loss` | 0.7950 | promote |
| 12 | `bytedance/infinistore` | 0.7950 | promote |
| 13 | `biomedsciai/fuse-med-ml` | 0.7925 | promote |
| 14 | `aiqm/torchani` | 0.7875 | promote |
| 15 | `awslabs/graphstorm` | 0.7875 | promote |

Main C2 reject reasons:

```text
weak_local_evidence          188
no_local_cuda_signal         110
no_interface_signal           67
docs_only_cuda_signal         44
repo_too_large_or_truncated    3
has_local_scan_errors          3
```

## Useful Commands

Run C1:

```bash
python scripts/materialize_repos_c1.py \
  --run-root runs/20260617-003331-github-goal-10000 \
  --auth-file auth.json \
  --concurrency 8 \
  --dashboard
```

Run C2:

```bash
python scripts/screen_local_repos_c2.py \
  --run-root runs/20260617-003331-github-goal-10000 \
  --dashboard
```

Inspect buffer status:

```bash
sqlite3 runs/20260617-003331-github-goal-10000/buffers/c1_to_c2.sqlite \
  "select status, count(*) from buffer_items group by status order by status;"

sqlite3 runs/20260617-003331-github-goal-10000/buffers/c2_to_d.sqlite \
  "select status, count(*) from buffer_items group by status order by status;"
```

Show top C2 candidates:

```bash
python - <<'PY'
import csv
from pathlib import Path

path = Path("runs/20260617-003331-github-goal-10000/data/processed/repo-candidates-c2.csv")
with path.open(newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))

rows.sort(key=lambda row: float(row.get("c2_score") or 0), reverse=True)
for index, row in enumerate(rows[:20], start=1):
    print(index, row["repo_key"], row["c2_score"], row["c2_decision"])
PY
```

## Notes Worth Remembering

- C2 is local-only and does not execute repository code. It does not run
  `pip install`, `pytest`, examples, or arbitrary scripts.
- C2 skips symlinks so a repository cannot cause the scanner to read files
  outside its local checkout.
- C2 currently favors recall after C1: it produced many `maybe` results. The D
  layer should prioritize `promote` candidates first and then sample or rank
  `maybe` candidates.
- The top C2 score does not guarantee a high-quality G4 task. It means the repo
  has strong local static evidence: CUDA/GPU source signals, interface signals,
  installability, test/example evidence, CPU/reference hints, and manageable
  checkout size.
- C1 previously had an invalid-clone retry loop caused by treating any `.git`
  directory as a completed clone. This has been fixed by checking that `HEAD` is
  valid before reusing a local checkout.
- C1 now has `materialization.max_attempts`, defaulting to `3`, so bad clone
  candidates cannot spin forever.
- C1 and C2 both have terminal dashboards. Use `--dashboard` to force them on
  when running inside tmux or non-interactive shells.

## Next Work

The next major stage is D: agent-assisted semantic review.

Recommended D input:

```text
runs/20260617-003331-github-goal-10000/buffers/c2_to_d.sqlite
```

D should start with `promote` candidates from C2, especially the top-ranked
repositories. It should verify whether the repository can support a G4 contract:

- concrete external interface or API
- reproducible setup
- bounded task size
- real CUDA/GPU assumption in executable paths
- CPU/reference or otherwise verifier-controllable expected behavior
- feasible hidden tests
- no unacceptable data/model/runtime dependency
