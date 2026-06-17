# Source Code

Reusable pipeline code lives here.

The main pipeline is organized by screening layer:

- `migration_task_pipeline.layers.a_seed_collection`: Stage A seed collection,
  repository metadata screening, normalization, deduplication, and CSV output.
- `migration_task_pipeline.layers.b_remote_code_search`: Stage B remote GitHub
  code and tree signal screening, rule-based scoring, and candidate ranking.

Expected future modules:

- Stage C local repository scanners
- Stage D agent review job preparation
- candidate ranking
- report generation

Keep one-off experiments in `scripts/` until they are stable enough to reuse.
