# ABCD Buffer and Local Repository Management Design

This document records the planned architecture for turning the A/B/C/D
screening levels into a resumable pipeline, and for managing repositories after
Stage C clones them locally.

The design goal is to avoid large in-memory queues and ambiguous local folders.
Every repository should have a durable identity, every stage transition should
be auditable, and interrupted runs should be able to resume without guessing
what already happened.

## Pipeline Buffers

The ABCD pipeline should use persistent buffers between stages:

```text
A -> buffer_a_to_b -> B -> buffer_b_to_c -> C -> buffer_c_to_d -> D -> final csv
```

Each stage is responsible for its own filtering decision:

```text
A accept -> write to A_to_B buffer
A reject -> do not write to A_to_B

B consumes A_to_B
B accept -> write to B_to_C buffer
B reject -> do not write to B_to_C
```

This means "discard" happens at the producing stage's output boundary. A later
stage may also reject an item, but that is its own stage decision.

## Buffer Storage

Buffers should be implemented as SQLite files, not JSON arrays:

```text
runs/<run_id>/buffers/a_to_b.sqlite
runs/<run_id>/buffers/b_to_c.sqlite
runs/<run_id>/buffers/c_to_d.sqlite
```

SQLite is preferred because it supports:

- Transactional task claiming.
- Durable status updates.
- Deduplication by stable item id.
- Resume after process failure.
- Multiple readers and controlled worker concurrency.
- Queryable state without loading the whole buffer into memory.

The SQLite files are not plain text, but their fields should store readable
strings and JSON text where practical. They can be inspected with `sqlite3`.

## Buffer Item State

Buffer records should use logical state transitions instead of physical delete.

Recommended states:

```text
pending
in_progress
done
failed
rejected
```

Basic lifecycle:

```text
producer writes pending item
consumer claims pending -> in_progress
consumer finishes -> done
consumer fails recoverably -> failed or pending after lease expiry
```

A consumed item should not be immediately removed from the database. Keeping the
record preserves auditability and makes crash recovery simpler. Old completed
items can be compacted or archived later.

## Lease-Based Claiming

Consumers should claim work with a lease:

```text
worker_id
leased_at
lease_expires_at
attempts
last_error
```

If a worker crashes while processing a task, another worker can reclaim the
item after `lease_expires_at`.

The claim operation should be transactional:

```text
find pending item
mark it in_progress
assign worker_id and lease expiry
commit
```

This avoids two workers processing the same buffer item.

## Buffer Item Schema

Each buffer item should carry a stable identity and enough payload for the next
stage to work without re-reading upstream outputs.

Suggested fields:

```text
item_id
repo_id
repo_key
repo_full_name
repo_url
source_layer
source_run_id
payload_version
payload_json
scores_json
evidence_json
priority
status
attempts
worker_id
leased_at
lease_expires_at
created_at
updated_at
last_error
```

`item_id` should be deterministic, for example:

```text
github:<github_repo_id>
github-url:<sha256(normalized_repo_url)>
```

`repo_key` should remain the human-readable lowercase `owner/repo` key, but it
should not be the only identity field because repositories can be renamed.

## Goal Mode

The buffer design supports goal mode naturally.

The global stop condition can be:

```text
final accepted repo count >= target_count
```

The coordinator can observe:

```text
A_to_B pending / in_progress / done counts
B_to_C pending / in_progress / done counts
C_to_D pending / in_progress / done counts
final accepted count
stage reject rates
```

If downstream buffers are empty and the final goal has not been reached, Stage A
can continue producing more seeds. If downstream buffers are backed up, upstream
production can pause.

## Stage C Repository Management

Stage C starts the local clone phase. From this point on, local directories must
be treated as a cache or working tree, not as the source of truth.

The source of truth should be a SQL registry.

Recommended table:

```text
local_repos
```

Suggested fields:

```text
repo_id
github_repo_id
github_node_id
full_name
repo_key
html_url
clone_url
default_branch
run_id
buffer_item_id
local_path
clone_status
checkout_ref
checkout_sha
clone_depth
submodules_enabled
lfs_enabled
disk_bytes
file_count
created_at
updated_at
last_checked_at
error_message
```

The stable identity should prefer GitHub's numeric repository id when
available:

```text
github_repo_id
github_node_id
```

If those are unavailable, use a deterministic fallback such as:

```text
sha256("github.com/<owner>/<repo>")
```

## Local Directory Naming

Local clone paths should be readable but collision-resistant:

```text
runs/<run_id>/repos/
  pytorch__pytorch--12345678/
  rapidsai__cudf--9a81bc22/
  deepchem__deepchem--a3fd9210/
```

The readable prefix helps humans. The suffix should come from `github_repo_id`
or a short stable hash to handle renames, casing differences, and duplicate
names across sources.

Do not infer repository identity from directory names alone. Always resolve:

```text
repo_id -> local_repos -> local_path / full_name / checkout_sha
```

## Local Manifest

Each cloned repository directory may include a small manifest:

```text
.repo-manifest.json
```

Example:

```json
{
  "repo_id": "github:65600975",
  "full_name": "pytorch/pytorch",
  "html_url": "https://github.com/pytorch/pytorch",
  "checkout_sha": "abc123",
  "run_id": "20260617-example",
  "source_buffer": "b_to_c",
  "local_path": "runs/20260617-example/repos/pytorch__pytorch--65600975"
}
```

This manifest is for human debugging only. The SQL registry remains the source
of truth.

## Clone State vs C Screening Result

Clone/cache state and Stage C screening decisions should be separate.

Recommended tables:

```text
local_repos
c_screening_results
```

`local_repos` records whether the repository exists locally, where it is, and
which commit was checked out.

`c_screening_results` records Stage C's decision and evidence:

```text
repo_id
buffer_item_id
decision
scores_json
evidence_json
reason
created_at
```

A repository can be cloned successfully and still be rejected by C. Conversely,
a clone failure should be tracked as clone state, not as a semantic C reject.

## Stage C Cleanup Policy

Stage C should be able to manage disk pressure explicitly.

Recommended default policy:

```text
accept repo -> keep local clone for D
reject repo -> delete working tree after evidence is written
failed repo -> keep recent failures for debugging, then prune
```

Useful runtime options:

```text
--keep-rejected-repos
--delete-rejected-repos
--max-local-repos
--max-repo-disk-gb
```

Even when a rejected repository's working tree is deleted, the SQL registry,
C-layer evidence, and rejection reason should remain.

## Stage D Input

Stage D should not discover work by scanning `runs/<run>/repos/`.

It should consume `C_to_D` buffer items. Each item should include:

```text
repo_id
local_path
checkout_sha
c_evidence_summary
```

This keeps D deterministic and auditable. If a local directory exists but no
`C_to_D` item references it, D should ignore it.

## Implementation Principles

The buffer and C-layer implementation should follow the project's existing data
principles:

- Do not accumulate large queues in memory.
- Stream evidence and logs as each unit finishes.
- Make every stage resumable.
- Use SQL status fields instead of physical deletion for core state.
- Keep local repository directories as cache, not as identity.
- Make every final CSV row traceable back through C, B, and A evidence.

The long-term target is:

```text
SQLite buffers manage flow.
SQL registries manage local cloned repositories.
CSV files remain export/report artifacts, not the primary state store.
```
