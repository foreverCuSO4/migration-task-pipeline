# Seed Collector v0 Plan

This document describes the first concrete pipeline for collecting repository seeds from PyPI and conda-forge.

The goal is to produce the first normalized GitHub repository list for later G4 candidate screening. This stage should not clone repositories and should not ask agents to judge projects yet. It should only collect package metadata, extract GitHub repository URLs, normalize/deduplicate them, and enrich them with cheap GitHub metadata.

## Goal

Produce a first seed table with thousands to around ten thousand GitHub repository URLs that are likely to represent real ML, scientific computing, HPC, or accelerator-related software packages.

Primary output:

```text
data/processed/repo-seeds-v0.csv
```

Secondary outputs:

```text
data/raw/pypi-packages-YYYYMMDD.jsonl
data/raw/conda-forge-repodata-YYYYMMDD.jsonl
data/interim/repo-urls-normalized-YYYYMMDD.csv
data/interim/github-metadata-YYYYMMDD.jsonl
```

## Step 1: Define The Output Table

All collectors should write into one minimal shared schema.

Minimum columns:

```csv
source,package_name,package_version,repo_url,homepage,summary,keywords,license,downloads_30d
```

Example rows:

```csv
pypi,mace-torch,0.3.16,https://github.com/ACEsuit/mace,https://github.com/ACEsuit/mace,"MACE: Fast and accurate machine learning interatomic potentials",molecular;torch,MIT,12345
conda-forge,pytorch-lightning,2.5.0,https://github.com/Lightning-AI/pytorch-lightning,https://github.com/Lightning-AI/pytorch-lightning,"PyTorch Lightning",pytorch;training,Apache-2.0,
```

Recommended additional columns for auditability:

```csv
collected_at,source_record_id,repo_owner,repo_name,repo_key,url_extract_field,matched_keywords
```

Definitions:

- `source`: `pypi` or `conda-forge`.
- `package_name`: package name from the source ecosystem.
- `package_version`: latest or selected version at collection time.
- `repo_url`: normalized GitHub URL in `https://github.com/<owner>/<repo>` form.
- `homepage`: homepage or project URL from package metadata.
- `summary`: short package description.
- `keywords`: normalized keyword string from package metadata when available.
- `license`: source package license string.
- `downloads_30d`: 30-day download count if available. It is expected for PyPI and may be empty for conda-forge v0.
- `url_extract_field`: field where the GitHub URL was found, such as `home_page`, `project_urls`, `dev_url`, `summary`, or `description`.
- `matched_keywords`: configured seed keywords matched in package metadata.

Completion criteria:

- A schema file or documented column list exists.
- Both PyPI and conda-forge collectors can write records that fit this schema.
- Missing values are represented consistently as empty strings, not mixed sentinels.

## Step 2: Collect PyPI Package Seeds

Use the BigQuery PyPI public dataset where possible. Avoid one-package-at-a-time PyPI API calls for the initial broad collection.

Fields to collect:

- package name
- latest or selected version
- summary
- description
- classifiers
- keywords
- `home_page`
- `project_urls`
- 30-day download count
- license

Initial keyword set:

```text
torch
pytorch
cuda
triton
deep learning
machine learning
vision
graph
molecular
simulation
distributed
accelerate
transformer
```

Filtering strategy:

1. Search the metadata fields for the initial keyword set.
2. Keep packages with at least one keyword hit.
3. Extract GitHub URLs from:
   - `home_page`
   - `project_urls`
   - description
   - summary
   - classifiers if useful
4. Keep records where a GitHub repository URL can be extracted.
5. Preserve raw package metadata in `data/raw/` for later audit.

GitHub URL extraction pattern:

```text
github.com/<owner>/<repo>
```

Practical notes:

- Prefer repository URLs over issue, docs, or organization URLs.
- Some packages link to docs first and source second; inspect `project_urls` labels such as `Source`, `Repository`, `Homepage`, `Code`, `Bug Tracker`.
- Descriptions can contain many GitHub links. Keep the most repository-like one and store extraction provenance.
- Package download counts should be used for ranking, not as a hard requirement in v0.

Completion criteria:

- A raw PyPI metadata dump or query output is saved.
- A PyPI-derived candidate table is produced.
- Each retained row has a normalized `repo_url`.

## Step 3: Collect conda-forge Package Seeds

For v0, use conda-forge repodata directly. Do not inspect feedstock repositories yet.

Initial sources:

```text
https://conda.anaconda.org/conda-forge/noarch/repodata.json
https://conda.anaconda.org/conda-forge/linux-64/repodata.json
```

Fields commonly available:

- package name
- version
- summary
- license
- home
- dev_url

Filtering strategy:

1. Download `repodata.json` for `noarch` and `linux-64`.
2. Search package fields for the same seed keywords used by PyPI.
3. Extract GitHub URLs from:
   - `home`
   - `dev_url`
   - summary
   - description fields if present
4. Normalize extracted GitHub URLs.
5. Write records into the shared output schema.

Why conda-forge matters:

- Many scientific, simulation, molecular, and HPC projects are more visible in conda-forge than PyPI.
- conda-forge metadata often points to upstream source repositories through `home` or `dev_url`.
- It complements PyPI and reduces bias toward Python-only package ecosystems.

Completion criteria:

- Raw conda-forge repodata is saved.
- A conda-forge-derived candidate table is produced.
- Records conform to the same schema as PyPI output.

## Step 4: Deduplicate And Normalize GitHub URLs

Normalize all GitHub URLs to:

```text
https://github.com/<owner>/<repo>
```

Strip:

```text
/issues
/pulls
/tree/main
/tree/<branch>
/blob/...
.git
query strings
fragments
```

Deduplication key:

```text
lowercase(owner) + "/" + lowercase(repo)
```

Rules:

- If the same repo appears from multiple packages, keep one canonical repo row and preserve all source package links in auxiliary fields or a separate mapping table.
- Prefer records with stronger source provenance:
  1. project URL explicitly labeled source/repository/code
  2. homepage/dev_url
  3. description or summary extraction
- Preserve `source` information. It is useful to know whether a repo came from PyPI, conda-forge, or both.

Expected output:

```text
data/interim/repo-urls-normalized-YYYYMMDD.csv
```

Recommended columns:

```csv
repo_key,repo_url,sources,package_names,licenses,matched_keywords,homepage_candidates,first_seen_at
```

Completion criteria:

- All retained URLs are canonical GitHub repository URLs.
- Duplicate package records are collapsed by `owner/repo`.
- The result is large enough for downstream filtering, ideally several thousand to around ten thousand repositories.

## Step 5: Enrich With GitHub Metadata, No Cloning Yet

At this stage, do not clone or download repository source code. Only use GitHub API metadata.

Metadata to fetch:

```text
stars
forks
archived
fork
license
default_branch
pushed_at
size
topics
primary_language
```

Initial filters:

- Drop `archived = true`.
- Drop repositories with missing license unless they are exceptionally important and manually allowlisted later.
- Drop repositories with very large `size` if they are likely to be expensive to inspect.
- Drop repositories with very low stars and very low package download counts.
- Drop forks unless there is evidence of independent activity or the fork is intentionally useful.

Suggested v0 thresholds:

```text
archived == false
license is not empty
size < 500000 KB
stars >= 10 OR downloads_30d >= 1000 OR source_count >= 2
```

These thresholds are intentionally loose. The purpose is to remove obvious noise without losing unusual but valuable scientific projects.

Expected output:

```text
data/processed/repo-seeds-v0.csv
```

Recommended additional GitHub metadata columns:

```csv
github_stars,github_forks,github_archived,github_is_fork,github_license,github_default_branch,github_pushed_at,github_size_kb,github_topics,github_primary_language
```

Completion criteria:

- Every retained row has GitHub metadata or a recorded metadata fetch failure.
- Obvious archived/license-missing/noise repositories are removed.
- No repository has been cloned yet.

## What Comes After v0

Only after this seed table exists should the pipeline move to source inspection:

```text
repo-seeds-v0.csv
  -> select top 2k-5k
  -> shallow clone or download archives
  -> static CUDA/NVIDIA signal scan
  -> rank by migration surface and taskability
  -> agent candidate-card review for top 200-1000
```

Agent review begins after static scanning, not during seed collection.

## Open Questions

- Which BigQuery project and credentials should be used for PyPI public dataset access?
- Should download counts be required, or used only as a ranking signal?
- How should multiple packages pointing to the same monorepo be represented?
- What license policy should be used for benchmark/data-generation candidates?
- What is the first acceptable repo count for v0: 3k, 5k, or 10k?
- How much GitHub API rate limit is available for metadata enrichment?

## Immediate Implementation Tasks

1. Add a schema definition for `repo-seeds-v0.csv`.
2. Implement GitHub URL normalization and tests.
3. Implement PyPI metadata collector.
4. Implement conda-forge repodata collector.
5. Implement deduplication by `owner/repo`.
6. Implement GitHub metadata enrichment.
7. Produce and manually inspect the first 100 rows.
8. Produce the first full `repo-seeds-v0.csv`.

