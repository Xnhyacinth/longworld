# P108 source-aware selection with separate code proof packages

The P108 frozen candidate index contains 12,078 views and 11,553 semantic tasks. Its `p99_code_content` lane contains 91 views: 21 from the original P99 native receipt and 70 new Oxc tasks from the P107 expansion. The former single `code_content_proof` gate admits the 21 P99 rows and rejects all 70 P107 rows. The P108 selection config uses a separate, opt-in `code_content_proofs` list; P105–P107 singleton configs and the optional shared-world rebalance arm keep their old behavior.

Each package binds its raw native manifest, raw audit, unified manifest and all-reader mask. The P107 package additionally binds the P107 curated manifest, curated all-reader mask, quality ledger, prior P99 native receipt and exact curated sample membership. The selector compares the candidate's source group, task ID, sample ID, split, native receipt, native audit row reference, final-chat tokens and evidence span with the raw proof. A positive raw P107 proof without curated membership is rejected; an unknown receipt is rejected. The two packages yield **91/91 proven code-content candidates** (21 P99 + 70 curated P107), with zero unpinned receipts in this frozen index. This is an input gate, not a statement that all 91 will fit the balanced selection.

| Selection measure | P107 default | P108 candidate arm |
| --- | ---: | ---: |
| Selected views / independent tasks | 967 / 967 | 967 / 967 |
| Source groups | 248 | 247 |
| Multi-operation source groups | 25 | 27 |
| Train / eval | 684 / 283 | 684 / 283 |
| Source kinds | unchanged | unchanged |
| `<32K` / `32K` / `64K` / `128K` | 319 / 248 / 273 / 127 | 319 / 249 / 272 / 127 |
| Final-chat tokens | 67,368,549 | 67,080,848 |
| Assistant-supervised tokens | 84,006 | 79,754 |

The fixed 24-view Oxc source-group cap now selects 16 new P107 added-line tasks and retains four original P99 Oxc added-line tasks plus four Oxc `merged_files_union` tasks. The earlier selection had 12 P99 added-line and 12 merged-union Oxc tasks. Both P107 Wiki remote-selector eval tasks are selected. Overall P108 gains **18 rows** (16 P107 code, 2 P107 Wiki) and replaces **18 rows** (8 older P99 added-line, 8 merged-union, 2 Wiki table-cell lookups); it does **not** add 72 rows to the selected pack. The code operation mix shifts toward added-line complete sets, while eight merged-union examples are lost. Supervised tokens fall by 4,252 (5.1%) and source-group breadth falls by one. This is an experimental candidate arm, not a claim that P108 is a better training mixture. The Oxc cap was not raised.

The P108 selected reader pack contains 967 byte-checked readers and exact assistant-only masks, with 684 train and 283 eval views. Both the selection and materialized receipts mark `train_ready=false`; no GPU job was started. Source kinds, train/eval totals and physical length buckets are measured from the final selected entries, not from gross input availability.

The P108 selection and all 967 materialized readers reproduced with `--verify-only`. Historical default P105 (881 readers), P106 v2 (959) and P107 (967) selections and complete materialized reader packs also reproduced byte-for-byte under their original configs. The optional P106 shared-world selection arm reproduced its frozen selection receipt; the focused materializer test checks that its optional policy is forwarded during initial build and replay. Focused selector/materializer tests passed (18), as did Ruff and `git diff --check`.

Pinned receipts:

- Config: `configs/p108_balanced_selection_v1.json`, SHA-256 `7fe137b0bc45dfcf2677282804daec03bea8f8e109a10020c8e5cccdf6df8bcb`.
- Selection: `data/candidates/p108_balanced_selection_v1/manifest.json`, SHA-256 `9d7681fdd6267854a62baca5bbbc3b03a1e41b4f616821aa790208be099538f4`.
- Materialized: `data/candidates/p108_balanced_materialized_v1/manifest.json`, SHA-256 `b83d5b7438105aa8c62b357cbba0c5ba182bff98f9896489092b3acecac961c9`.

Replay from the project root:

```bash
.venv/bin/python scripts/select_p90_balanced_candidates.py --config configs/p108_balanced_selection_v1.json --output data/candidates/p108_balanced_selection_v1 --verify-only > /dev/null
.venv/bin/python scripts/materialize_p95_balanced_selection.py --index data/candidates/p108_candidate_refs_v1 --selection data/candidates/p108_balanced_selection_v1 --selection-config configs/p108_balanced_selection_v1.json --output data/candidates/p108_balanced_materialized_v1 --verify-only
.venv/bin/python -m pytest -q tests/test_select_p90_balanced_candidates.py tests/test_materialize_p95_balanced_selection.py
```
