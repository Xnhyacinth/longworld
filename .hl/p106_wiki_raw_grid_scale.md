# P106 P95 exact-revision Wiki raw-grid scale pilot

P106 applies the same strict raw-Wikitext-to-reader method to a second pinned
Wiki source pool without modifying P104/P105 code or receipts. The P95
categorical audit contained 217 table-width rejection records on 43 distinct
pages in 27 source groups. All 43 exact revisions were frozen with four
workers, a shared two-request-per-second limit, one response file per
revision, and completed-state byte/revision replay. Their title and URL keys
are unique and have **zero overlap** with the P97 pool that contains the
P105 pages; each P95 page retains its original train/eval split.

## Gross to net

| Stage | Result |
| --- | ---: |
| Gross P95 `row_width_mismatch` records | 217 |
| Distinct affected pages / source groups | 43 / 27 |
| Exact revisions frozen / failed | 43 / 0 |
| Distinct document+section+visible-header targets | 186 |
| Unique raw-grid→reader-cell aligned tables with category options | 25 |
| Possible category options before task checks | 60 |
| Final reader tasks with source-span, hit/control, alternate-support and mask checks | 46 |

The 25 supported tables occur in 11 source groups; only **8 worlds** produced
the 46 admitted tasks. The final tasks span **5 domains** (history 22,
education 10, industry 9, heritage 4, media 1) and **6 topics** (castles 22,
breweries 9, schools 7, archaeological sites 4, colleges 3, television
stations 1). Their frozen splits are train 20 and eval 26. The 14 option-level
rejections were six without a distinct nonchanging control category, four
with same-line alternative support outside the selected table and four with
unresolved markup in a question/header/gold label. Other structurally
unusable tables remain in the [support ledger](/volume/pt-dev/qjiu/longworld-worlds/data/candidates/p106_wiki_width_target_support_p95_v1.json), including nested/multiline,
span-bearing, duplicate-name and ambiguous-header cases; they were not
counted as tasks.

The P106 source adapter reads P95 response files and checks page title, URL,
revision, split and snapshot SHA against the P95 global gate. In isolated
worker processes it delegates task compilation to the **SHA-pinned P105
compiler and reader logic**, so the row universe, source-cell and reader
offsets, complete-set answer, hit/control intervention and assistant loss
mask use one tested implementation. No topic-specific task function was
added. The independent P106 audit joins readers, indices and proofs by
sample ID across both splits, then replays every source span, all positive
and negative rows, answer-changing/nonchanging edits and final mask.

## Frozen receipts

| Artifact | SHA-256 |
| --- | --- |
| `data/candidates/p106_wiki_width_raw_p95_v1/manifest.json` | `9736a0f5ea7c86c39e1d4b8393e605a703c81519484a3f02e65e3ecd95a07935` |
| `data/candidates/p106_wiki_width_target_support_p95_v1.json` | `be4f281e69d8c3addd5c6135a35b0fbd2019f35b6ac0b678ecb7e35b399ab2bc` |
| `data/candidates/p106_wiki_grid_native_p95_v1/manifest.json` | `8ee97d46c262f2ef823b027c213adffcb66a94146ac4c718121cb1a930dafadd` |
| `data/candidates/p106_wiki_grid_native_p95_v1/mask_audit.json` | `8bdca66c3534a0de88ecdd36be53272032ffa03445f224de9eb40e13d241cd5b` |
| `data/candidates/p106_wiki_grid_unified_p95_v1/manifest.json` | `e5b720f43b0178915f8c536226e527df58fbbf7bb5cf43a73c6b8f0557d6d8e9` |
| `data/candidates/p106_wiki_grid_shared_mask_p95_v1/manifest.json` | `51398e45bad38de1e722206f07907589af7ccded0fa25acf55b7d441ea2bd2d1` |
| `data/candidates/p106_wiki_grid_length_report_p95_v1.json` | `cdca6a4cc619dc388ec312abc27ef1a31fe46ca1bce811094523a62481117ce0` |

The shared `audit_unified_reader_mask.py --all` receipt independently checks
46/46 unified readers using Qwen/Qwen3.5-4B revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`: 1,110,186 final-chat
tokens and 1,110 supervised tokens. Physical lengths are 9,192–86,614
tokens (median 14,133.5): 39 below 32K, four in 32K, three in 64K. Evidence
cell extent is 237–5,816 tokens (median 744); the last evidence cell ends
5,768–79,608 tokens before the question (median 10,693.5). Outside the
target table lies 67.004%–99.5698% of context tokens. That percentage is a
measured table-external fraction, not a semantic irrelevance proof.

These are **L2 table-local complete-set tasks** with some long retrieval
gaps. The three 64K physical inputs do not establish cross-document or
widely distributed multi-evidence dependence. No interval task was admitted
by this categorical route. The shard remains candidate-only
(`train_ready=false`); no GPU training was started.

Replay from the repository root:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p106_freeze_width_revisions.py --config configs/p106_wiki_raw_p95_v1.json --output data/candidates/p106_wiki_width_raw_p95_v1
UV_LINK_MODE=copy uv run --offline python scripts/p106_wiki_target_support.py --config configs/p106_wiki_support_p95_v1.json --output data/candidates/p106_wiki_width_target_support_p95_v1.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p106_wiki_grid_batch.py --config configs/p106_wiki_grid_p95_v1.json --output-dir data/candidates/p106_wiki_grid_native_p95_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p106_wiki_grid_audit.py --native-dir data/candidates/p106_wiki_grid_native_p95_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p106_wiki_to_unified.py --config configs/p106_wiki_grid_p95_v1.json --native-dir data/candidates/p106_wiki_grid_native_p95_v1 --output data/candidates/p106_wiki_grid_unified_p95_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py data/candidates/p106_wiki_grid_unified_p95_v1 --all --output data/candidates/p106_wiki_grid_shared_mask_p95_v1 --max-seq-len 131072 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p106_length_evidence_report.py --native-dir data/candidates/p106_wiki_grid_native_p95_v1 --output data/candidates/p106_wiki_grid_length_report_p95_v1.json --verify-only
```

Focused P106 tests passed 3/3; Ruff check/format passed. This wave shows that
one source-pair-independent raw-grid method can recover tasks from an earlier
pool across multiple topics and splits without per-domain QA code. Its yield
and task-family diversity remain bounded by source table quality; the next
scaling step should improve source routing and genuinely remote dependencies,
not multiply these 46 table-local answers through length-only views.
