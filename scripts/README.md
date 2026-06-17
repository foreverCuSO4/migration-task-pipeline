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

Layer B is a streaming stage. After each repository finishes, it appends one
JSONL evidence row, appends one CSV candidate row, and writes detailed progress
events to `data/logs/remote-code-screening-YYYYMMDD.log`. This makes long runs
debuggable while they are still in progress.

Both Layer A and Layer B rotate GitHub tokens round-robin for API requests. If a
request gets HTTP 403 or 429 from one token, the same request is retried with
the next token and fails only after every configured token has failed.
