# Scripts

Runnable CLI scripts and operational helpers will live here.

Scripts should be thin wrappers around reusable code in `src/` once the pipeline stabilizes.

## Seed Collector v0

Run the first repository seed collector with:

```bash
GITHUB_TOKEN=... python scripts/collect_repo_seeds.py \
  --config configs/seed-sources.example.yaml
```

Alternatively, put one or more tokens in ignored `auth.json`:

```json
{
  "github_tokens": [
    {"name": "token-a", "token": "..."},
    {"name": "token-b", "token": "..."}
  ]
}
```

`name` is optional and only used in diagnostics. Legacy single-token keys
`github_api_key`, `github_token`, and `github_key` are still accepted.
If `GITHUB_TOKEN` is set, it is used first and then merged with tokens from
`auth.json`. Duplicate token values are ignored.

Check whether tokens in `auth.json` are currently usable with:

```bash
python scripts/check_github_tokens.py --auth-file auth.json
```

The checker validates `/user` and a small `/search/code` request because Layer B
depends on GitHub code search. It prints token names and short SHA-256
fingerprints only; full token values are never printed. By default it checks
only `auth.json`. Add `--include-env` when you also want to validate
`GITHUB_TOKEN`.

When `--output-root` is omitted, each run writes under a timestamped directory:

```text
runs/YYYYMMDD-HHMMSS-seed-collector-v0/data/
```

Use `--run-name` to change the suffix, or pass `--output-root` to write to a
specific data directory.

The collector uses GitHub repository search as its only seed source. Search
results emit `source=github-search` rows and do not populate package-specific
fields such as package version or package download counts.
The search results already include common GitHub metadata, so the enrichment
step reuses complete `github_*` fields and skips an extra `/repos/<owner>/<repo>`
request for those rows.

Goal mode is enabled in the default config. The search frontier keeps issuing
repository-search queries until `goal.target_processed_repos` final rows pass
filtering or `goal.max_search_requests` is exhausted.

The collector runs as a serial streaming pipeline: each raw source record is
written, converted to a seed candidate, deduplicated, enriched, and filtered
before the next raw record is processed. Final CSV files are written as a
snapshot after all enabled sources finish so aggregate fields remain complete.

The pipeline writes raw GitHub search results to `data/raw/`, normalized
repository URLs to `data/interim/`, GitHub metadata to `data/interim/`, and the
final seed table to `data/processed/repo-seeds-v0.csv`.

## Layer B Remote Code Screening

Run the remote code-search screening stage on a seed CSV:

```bash
python scripts/screen_repo_candidates_b.py \
  --seed-csv runs/<run>/data/processed/repo-seeds-v0.csv \
  --auth-file auth.json
```

By default, Layer B reads `configs/layer-b.example.yaml`. Use `--config` to
point at a different YAML file:

```bash
python scripts/screen_repo_candidates_b.py \
  --config configs/layer-b.example.yaml \
  --seed-csv runs/<run>/data/processed/repo-seeds-v0.csv \
  --auth-file auth.json
```

CLI flags such as `--per-page`, `--max-code-queries-per-repo`,
`--rate-limit-max-retries`, `--rate-limit-retry-sleep`, `--rate-limit-max-sleep`,
`--transient-error-max-retries`, `--transient-error-retry-sleep`,
`--transient-error-max-sleep`, `--tree`, `--no-tree`, `--resume`, and
`--no-resume` override values from the config file.

For a smoke test, limit the number of rows:

```bash
python scripts/screen_repo_candidates_b.py \
  --seed-csv runs/<run>/data/processed/repo-seeds-v0.csv \
  --limit 20 \
  --auth-file auth.json
```

When `--output-root` is omitted, the script writes into the same `data/` root as
the seed CSV. It writes remote evidence to
`data/interim/github-code-signals-YYYYMMDD.jsonl` and ranked candidates to
`data/processed/repo-candidates-b.csv`.
By default, Layer B also writes promoted and maybe candidates to the B-to-C
SQLite buffer at `runs/<run>/buffers/b_to_c.sqlite` when the output root is a
run `data/` directory, or `<output-root>/buffers/b_to_c.sqlite` otherwise. Use
`--no-b2c-buffer` to disable this queue output.

Layer B is a streaming stage. After each repository finishes, it appends one
JSONL evidence row, appends one CSV candidate row, and writes detailed progress
events to `data/logs/remote-code-screening-YYYYMMDD.log`. This makes long runs
debuggable while they are still in progress.

Layer B resumes by default. On startup it reads the existing
`data/processed/repo-candidates-b.csv`, treats complete `repo_key` rows as
already processed, skips those repositories, and appends new evidence, candidate
rows, and log events. This uses the candidate CSV as the completion source of
truth so a crash between evidence and CSV writes cannot hide a missing final
candidate row. Use `--no-resume` to overwrite B outputs and scan from scratch.
When the B-to-C buffer is enabled, resume also backfills any promoted or maybe
candidate that exists in prior B CSV/JSONL outputs but is missing from the
buffer, so older Layer B runs can be made visible to Stage C without rescanning
those repositories.

When run in an interactive terminal, Layer B also displays a live dashboard on
stderr with completion progress, elapsed time, average repositories per minute,
ETA, decision counts, current repository, and current scan phase. Use
`--dashboard` to force it on or `--no-dashboard` to disable it.

Both Layer A and Layer B rotate GitHub tokens round-robin for API requests. If a
request gets HTTP 403 or 429 from one token, the same request is retried with
the next token and fails only after every configured token has failed.
Token-specific access failures such as HTTP 401 `Bad credentials` and HTTP 403
`Resource not accessible by personal access token` are also tried against the
next configured token before the run fails.

Layer B treats GitHub rate limits as incomplete remote evidence, not as a
negative repo signal. When all configured tokens are rate limited, the stage
waits and retries the same API call before scoring the repository. Use
`--rate-limit-max-retries` to bound this behavior for smoke tests or debugging;
when omitted, Layer B waits until GitHub allows the request to complete.

Layer B also retries transient network errors from `requests`, including
`Timeout`, `ConnectionError`, `SSLError`, and `ChunkedEncodingError`. If these
retries are exhausted, the current repository is treated as incomplete remote
evidence and no candidate row is written, so the default resume behavior can
retry it on the next run.

## Stage C1 Local Repository Materialization

Run C1 after Layer B has produced `runs/<run>/buffers/b_to_c.sqlite`:

```bash
python scripts/materialize_repos_c1.py \
  --run-root runs/<run>
```

By default, C1 reads `configs/layer-c1.example.yaml` and uses these paths:

```text
input buffer:  runs/<run>/buffers/b_to_c.sqlite
output buffer: runs/<run>/buffers/c1_to_c2.sqlite
repo root:     runs/<run>/repos/
registry:      runs/<run>/state/local-repos.sqlite
log file:      runs/<run>/data/logs/c1-materialization-YYYYMMDD.log
```

C1 shallow-clones repositories by default and runs workers concurrently. Set
the worker count in config:

```yaml
runtime:
  concurrency: 4
```

or override it on the command line:

```bash
python scripts/materialize_repos_c1.py \
  --run-root runs/<run> \
  --concurrency 8
```

If `auth.json` contains GitHub tokens, C1 rotates them for HTTPS GitHub clones.
Tokens are provided through `GIT_ASKPASS`; they are not embedded in clone URLs
or logged. Repositories are cloned anonymously when no token is available.

Proxy values can be set in `configs/layer-c1.example.yaml`:

```yaml
materialization:
  proxy:
    http: ""
    https: ""
    all: ""
    no_proxy: ""
```

Empty values inherit the current process environment and git configuration.
Non-empty values override `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and
`NO_PROXY` for C1 clone subprocesses.

Clone failures are operational failures, not candidate rejects. C1 records the
failure in `local-repos.sqlite`, requeues the B item as `pending` with retry
priority, and does not write a `C1_to_C2` item until a later clone succeeds.

## Stage C2 Local Heuristic Screening

Run C2 after C1 has produced `runs/<run>/buffers/c1_to_c2.sqlite`:

```bash
python scripts/screen_local_repos_c2.py \
  --run-root runs/<run>
```

By default, C2 reads `configs/layer-c2.example.yaml` and uses these paths:

```text
input buffer:   runs/<run>/buffers/c1_to_c2.sqlite
output buffer:  runs/<run>/buffers/c2_to_d.sqlite
evidence jsonl: runs/<run>/data/interim/local-heuristic-evidence-YYYYMMDD.jsonl
candidates csv: runs/<run>/data/processed/repo-candidates-c2.csv
log file:       runs/<run>/data/logs/c2-local-screening-YYYYMMDD.log
```

C2 is local-only and does not execute repository code. It scans bounded file
trees and text files for CUDA/GPU assumptions, runnable interfaces,
installability, tests/examples, CPU/reference hints, risk signals, and actual
checkout size. Promote and maybe decisions are written to `c2_to_d.sqlite`;
rejects are recorded in the C2 CSV/JSONL/log and marked rejected in the input
buffer.

Set local scan concurrency in config:

```yaml
runtime:
  concurrency: 16
```

or override it on the command line:

```bash
python scripts/screen_local_repos_c2.py \
  --run-root runs/<run> \
  --concurrency 16 \
  --dashboard
```
