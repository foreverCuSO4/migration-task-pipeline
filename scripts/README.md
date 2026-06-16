# Scripts

Runnable CLI scripts and operational helpers will live here.

Scripts should be thin wrappers around reusable code in `src/` once the pipeline stabilizes.

## Seed Collector v0

Run the first repository seed collector with:

```bash
GITHUB_TOKEN=... python scripts/collect_repo_seeds.py \
  --config configs/seed-sources.example.yaml \
  --pypi-backend auto
```

When `--output-root` is omitted, each run writes under a timestamped directory:

```text
runs/YYYYMMDD-HHMMSS-seed-collector-v0/data/
```

Use `--run-name` to change the suffix, or pass `--output-root` to write to a
specific data directory.

`auto` tries the PyPI BigQuery discovery backend first. If BigQuery is
unavailable, it falls back to `http-curated`, which only fetches metadata for
the configured package list and is intended for smoke/sample runs rather than
broad PyPI discovery.

Set `github_search.enabled: true` in the config to add GitHub repository search
as a third seed source. GitHub search emits `source=github-search` rows and does
not populate package-specific fields such as package version or PyPI downloads.
The search results already include common GitHub metadata, so the enrichment
step reuses complete `github_*` fields and skips an extra `/repos/<owner>/<repo>`
request for those rows.

The pipeline writes raw package metadata to `data/raw/`, normalized repository
URLs to `data/interim/`, GitHub metadata to `data/interim/`, and the final seed
table to `data/processed/repo-seeds-v0.csv`.
