# P96 candidate synthesis and scaling status

P96 extends the P95 sharded candidate index with two new operations on frozen
real sources. The current index is `data/candidates/p96_candidate_refs_v2`:
**11,107 final reader views / 10,582 global independent semantic tasks** across
1,325 source/world groups. It has 8,512 train and 2,595 eval views. A complete
reader-byte hash replay passed. All rows remain `train_ready=false`.

| Source kind | Indexed views |
| --- | ---: |
| Controlled simulation | 6,116 |
| Grounded simulation | 24 |
| Real Wiki | 1,778 |
| Real annual reports | 2,314 |
| Real code workflows | 860 |
| Real paper revisions | 7 |
| Real paper LaTeX sources | 8 |

The physical final-chat length distribution is 1,932 under 32K, 3,349 at
32–64K, 3,872 at 64–128K, and 1,954 at 128–256K. The index has 24 domain
labels, 73 topic labels and 121 operation strings. Many operation strings
encode a selector/target combination, so 121 is not a count of independent
long-context abilities. The dominant simulation lane and the small grounded
and paper lanes show that source and capability diversity remain uneven.

The new annual-report matrix reuses eight signed issuer worlds. It enumerates
736 legal source/program cells, filters known equivalent programs and repeated
answer buckets, and admits 296 independent native tasks with two reader views
each. A final-visible answer and assistant-mask audit passed 592/592. Bounded
selector-cell interventions changed the selected period and answer for 568
views; the other 24 views, representing 12 tasks, were excluded from the
unified shard. The accepted `p96_finance_factorial_wide_unified_v1` shard has
284 independent tasks / 568 views. The intervention does not establish that
all four original filings are indispensable: annual comparative columns can
repeat earlier values. An independent unified full-mask pass retokenized all
568 accepted readers, recording 37,551,420 full-chat tokens and 18,708
supervised tokens at `data/candidates/p96_finance_full_mask_v1`. See
[finance detail](p96_finance_factorial.md).

The new paper source compiler scans five frozen arXiv LaTeX source works for
cross-file prose reference to a uniquely labelled caption or section. Eight
tasks from two works survive cue, label and final-reader checks. These are
source-navigation tasks, not scientific inference or rendered-PDF QA. See
[paper detail](p96_paper_reference_qa.md).

The quality-aware balanced selection at
`data/candidates/p96_balanced_selection_v1` retains 756 distinct semantic
tasks across 187 source/world groups: 334 Wiki, 192 finance, 104 positively
certified code workflow, 103 controlled simulation, eight grounded simulation,
eight paper source and seven paper revision. It covers all 24 domain labels,
69 topic labels and 104 operation strings, with 211 under 32K, 188 at 32–64K,
229 at 64–128K and 128 at 128–256K. The selection contains 152 views from the
new finance shard and all eight new paper tasks. Its finance count is still
192 because group and source-kind caps replace older finance choices; the
full candidate index retains all 568 newly admitted finance views.

The selected readers are actually materialized at
`data/candidates/p96_balanced_materialized_v1`: 756 tasks, 512 train and 244
eval, 58,090,683 final full-chat tokens and 88,380 supervised tokens. All
756 assistant-only masks passed. A second full deterministic build matched
all five output SHA-256 values. Its 152 selected new finance readers are in
the positive selector-intervention sidecar, and its eight new paper readers
are in the native mask receipt. Relative to the historical P95 748-task
materialization, 595 sample IDs persist, 161 are new and 153 were removed by
rebalancing. The output is a candidate research set, not an approved training
set. See [materialization detail](p96_balanced_materialized.md).

The selector pins the same positive CodeForge content-proof receipts as P95.
The 43 new DuckDB/Wasmtime source candidates still lack a strong proof and are
not selected. These gates are scoped to their stated mechanisms; a valid
token mask, length or intervention is not a proof of minimum reader-text
dependency or model gain. No GPU training was launched in P96.

Inspect the exact cases and manifests from the repository root:

```bash
cat data/candidates/p96_candidate_refs_v2/manifest.json
cat data/candidates/p96_balanced_selection_v1/manifest.json
cat data/candidates/p96_finance_factorial_wide_unified_v1/manifest.json
cat data/candidates/p96_paper_unified_v2/manifest.json
cat data/candidates/p96_balanced_materialized_v1/manifest.json
less -R data/candidates/p96_balanced_selection_v1/selected_refs.jsonl
less -R data/candidates/p96_balanced_materialized_v1/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p96_candidate_refs_v2 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p96_balanced_selection_v1.json \
  --output data/candidates/p96_balanced_selection_v1 --verify-only
```

The active objective is wider than this wave. The current corpus does not yet
have hundreds of productive domains, a broad natural report/book or agentic
lane, or measured training gain. The new P97 vocabulary sharder addresses the
manual source-configuration ceiling, but a dry-run plan is not a new source,
world, task or training sample. Subsequent large batches must record source
acquisition yield, legal task cells, rejection reasons, final reader bytes and
mask receipts separately.
