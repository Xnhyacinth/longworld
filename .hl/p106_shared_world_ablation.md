# P106 same-bank shared-world selection arm

This is a candidate-selection ablation on the frozen P106 index, not a new
source pool or a training result. Both arms draw from
`data/candidates/p106_candidate_refs_v1` with the same CodeForge/P99 proof
pins and source split. The default selector first balances
`(split, source_kind, operation, length_bin)` cells. The optional policy then
replaces a selected row with a proof-eligible row from the **same cell** only
when that row completes a second operation in an already selected source
group. It preserves unique semantic tasks, per-group and per-kind/split caps,
the controlled-simulation supervised-token cap, and all domain/topic labels.
It permits at most five fewer distinct source groups. The manifest records
every swap and the before/after coverage.

| Measure | Default P106 v2 | Shared-world arm |
| --- | ---: | ---: |
| Candidate views / independent tasks | 959 | 959 |
| Source groups | 244 | 239 |
| Groups with multiple operations | 25 | 37 |
| Controlled-simulation groups | 98 | 93 |
| Controlled-simulation multi-operation groups | 1 | 8 |
| Real-Wiki groups / multi-operation groups | 123 / 7 | 123 / 12 |
| Train / eval views | 677 / 282 | 677 / 282 |
| `<32k` / `32k` / `64k` / `128k` views | 315 / 247 / 270 / 127 | 315 / 247 / 270 / 127 |
| Final chat tokens | 67,008,336 | 67,025,467 |
| Assistant-supervised tokens | 83,834 | 83,050 |

There are 12 one-for-one swaps. All 332 occupied cells, 106 operation counts,
seven source-kind counts, split counts, and length counts match exactly. The
27 domain and 103 topic labels remain represented, although their per-label
sample counts can shift. The five-group breadth cost is entirely within
controlled simulation. These counts establish a controlled selection
comparison; they do not establish a learning benefit or new long-context
dependency.

Frozen receipts:

- Selection config: `configs/p106_shared_world_selection_v1.json`
- Selection manifest: `data/candidates/p106_shared_world_selection_v1/manifest.json`
  (SHA256 `ce92abcc21b5068fe85e69a9cfef9dfe49da537088ac817fef08dc8043d8995d`)
- Selected refs SHA256: `77331dd6716561e7780e1734ab4713d5866a202fd3725c454f7f7046b8bb78df`
- Materialized manifest: `data/candidates/p106_shared_world_materialized_v1/manifest.json`
  (SHA256 `bd720fd48cdad686818ba77d1c595e84eabc6e1de89dea648eb31b44aba024f9`)
- Full assistant-mask receipt: `data/candidates/p106_shared_world_materialized_v1/mask_audit.json`
  (SHA256 `f3dcbecf12c2bef322e146a8a21877349803e429161ef7e004ebb5543e259295`)
- Per-reader mask ledger SHA256: `5b6c273c3085420609c75ddb46fb30d2d0185bbc4a4413d532390f3f175d9a48`

Reproduce the selection and exact reader/mask materialization without writing
new assets:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p106_shared_world_selection_v1.json \
  --output data/candidates/p106_shared_world_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py \
  --index data/candidates/p106_candidate_refs_v1 \
  --selection data/candidates/p106_shared_world_selection_v1 \
  --selection-config configs/p106_shared_world_selection_v1.json \
  --output data/candidates/p106_shared_world_materialized_v1 --verify-only
```

For comparison, the historical P105 v1 and P106 v2 selection receipts and
materialized readers use the default path, which remains byte-identical when
the optional field is absent. The materialized set is local research data
(`train_ready=false`); model-training gain and unrestricted proof necessity
remain untested.

Verification completed: optional selection and materialization `--verify-only`
both passed; historical P105 v1 and P106 v2 materialization `--verify-only`
both passed; selector and materializer focused tests (14 total), Ruff lint,
format checks, and `git diff --check` passed. No GPU training was run.
