# P96 balanced mixed-source reader materialization

`data/candidates/p96_balanced_materialized_v1` is a frozen, candidate-only
materialization of `p96_candidate_refs_v2` and `p96_balanced_selection_v1`.
The selection policy is pinned by `configs/p96_balanced_selection_v1.json`,
including positive CodeForge reading proofs. The materializer re-executed that
policy, matched every selected shard pointer and candidate record to the
source index, SHA-checked the selected shard reader files, preserved each
reader JSONL line byte-for-byte, and retokenized the final assistant-only
loss mask. `--verify-only` rebuilt and retokenized the whole pack and found
identical manifests and all five output-file SHA-256 values. Initial build
took 3m49.583s and full replay took 3m47.728s on this host.

| Measure | P96 selection |
| --- | ---: |
| Views / independent semantic tasks | 756 / 756 |
| Train / eval | 512 / 244 |
| Full-chat / supervised tokens | 58,090,683 / 88,380 |
| Physical bins `<32K` / `32K` / `64K` / `128K` | 211 / 188 / 229 / 128 |
| Typed `(source_kind, source_group)` groups / raw group IDs | 187 / 186 |
| Domain labels / topic labels / domain-topic pairs | 24 / 69 / 70 |
| Operation labels | 104 |

The raw paper group `researchlab:arxiv:2203.01928` occurs under both
`real_paper_revision` and `real_paper_source`, always in train. There is no
cross-split conflict for a raw or typed source group in this selection.
Source-kind views are 334 real Wiki, 192 real finance, 104 real code workflow,
103 controlled simulation, eight grounded simulation, eight real paper source,
and seven real paper revision. The selected P96 additions are 152 finance
views and eight paper-source views. All selected finance IDs occur in the
positive 568-view bounded selector-intervention sidecar; all eight paper IDs
occur in the native final mask receipt.

Compared with historical `p95_balanced_materialized_v1`, 595 sample IDs are
retained, 161 are newly selected, and 153 are removed by balancing. These
756 rows are a **replacement selection**, not 756 net corpus additions.
Domain/topic counts are labels, not independent semantic mechanisms. Paper
reference QA covers two previously frozen source works and source-level TeX
references; it does not certify the compiled PDF or all semantic alternatives.
Finance tasks have checked visible cell execution and a bounded selector
intervention, but comparative tables can repeat values in later filings; the
minimum final-reader proof and all alternative supports remain unestablished.
The materialized receipt is `train_ready=false`; no GPU training was started.

The manifest binds the index manifest and references, selection config,
selection manifest, and selected references by SHA-256. Output file hashes are
recorded in `data/candidates/p96_balanced_materialized_v1/manifest.json`.
The final per-row mask receipt is `mask_audit.jsonl`; its summary is
`mask_audit.json`. Reproduce the full check from the repository root:

```bash
cat data/candidates/p96_balanced_materialized_v1/manifest.json
cat data/candidates/p96_balanced_materialized_v1/mask_audit.json
less -R data/candidates/p96_balanced_materialized_v1/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py \
  --index data/candidates/p96_candidate_refs_v2 \
  --selection data/candidates/p96_balanced_selection_v1 \
  --selection-config configs/p96_balanced_selection_v1.json \
  --output data/candidates/p96_balanced_materialized_v1 --verify-only
```
