# P113 pinned legal-cell preflight

This planner closes one scaling gap: a frozen source inventory can be crossed with a declarative source-shape recipe and semantic parameter grid, then separated from renderer and target-length requests. It **does not** invoke a native compiler, validate an answer, materialize final chat, or promote training data. Every supported row has `status=planned_only`; the result has `native_tasks_executed=0` and `train_ready=false`.

It complements `longworld/synthesis/p92_source_router.py`. P92 probes native Wiki task builders and reports observed candidate receipts for other source kinds. P113 plans potential source/recipe combinations across two frozen source formats and gives them stable semantic and presentation job identities; P92/native compilers must still determine real task yield, evidence, length, masks and admission. P113 currently has no connected general-report, book, paper, code or agentic recipe. Adding vocabulary entries for those genres would create no valid tasks.

Inputs are [the config](../configs/p113_legal_cell_plan_v1.json): P76's pinned Wiki source pool (14 snapshots), P64's pinned finance source catalog (8 issuers), and P96's pinned finance split manifest. Each leaf Wiki snapshot and finance source manifest hash is checked. Wiki source IDs and revisions come from the snapshot; finance issuer keys and CIKs are checked against its split receipt. A Wiki page shared across train/eval blocks both snapshots. Domain/topic are copied from the pinned source catalog and do not enter the semantic ID. The semantic ID hashes source bytes, recipe, source binding and semantic parameters; renderer and requested length only enter the presentation job ID. Requested length is not observed final chat length.

First frozen run:

| Measure | Count |
| --- | ---: |
| Source groups | 22 (14 Wiki, 8 finance) |
| Source-shape semantic cells / planned jobs | 122 / 122 |
| Wiki list-document preflight | 90 (45 list documents × 2 operations) |
| Finance four-annual-report preflight | 32 (8 issuers × 2 selectors × 2 baselines) |
| Unsupported source/recipe/parameter cells | 72 (56 Wiki→finance recipe, 16 finance→Wiki recipe) |
| Planned train/eval jobs | 88 / 34 |
| Native tasks executed / reader samples admitted | 0 / 0 |

The source-shape predicates are deliberately weak: a `List of` page may lack an unambiguous table cell, and four annual reports may lack a valid cross-period metric. The rejection ledger only says whether a source has the required shape; it is not a semantic or answer audit. There is no claim of 122 qualified long-context tasks.

Reproduce and verify:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p113_legal_cell_plan.py \
  --config configs/p113_legal_cell_plan_v1.json \
  --output-dir data/candidates/p113_legal_cell_plan_v1 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p113_legal_cell_plan.py
```

Inspect individual rows:

```bash
less -R data/candidates/p113_legal_cell_plan_v1/supported_jobs.jsonl
less -R data/candidates/p113_legal_cell_plan_v1/unsupported_cells.jsonl
cat data/candidates/p113_legal_cell_plan_v1/manifest.json
```

Next connection is a native compiler dispatcher using these rows as work requests, with each recipe's actual source-shape probe, evidence/audit receipt, final reader offsets and mask check returned under the same semantic ID. The planner itself remains a cheap control-plane gate.
