# Scripts

Runnable CLI scripts and operational helpers will live here.

Scripts should be thin wrappers around reusable code in `src/` once the pipeline stabilizes.

## Seed Collector v0

Run the first repository seed collector with:

```bash
GITHUB_TOKEN=... python scripts/collect_repo_seeds.py \
  --config configs/seed-sources.example.yaml
```

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

The collector runs as a serial streaming pipeline: each raw source record is
written, converted to a seed candidate, deduplicated, enriched, and filtered
before the next raw record is processed. Final CSV files are written as a
snapshot after all enabled sources finish so aggregate fields remain complete.

The pipeline writes raw GitHub search results to `data/raw/`, normalized
repository URLs to `data/interim/`, GitHub metadata to `data/interim/`, and the
final seed table to `data/processed/repo-seeds-v0.csv`.
