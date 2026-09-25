# P103 frozen Wiki categorical-scan increment

P100 already ran its strict categorical complete-set compiler over every
source group in the P97 gated pool: 114 groups and 339 pages yielded 43 tasks.
Running the same code on the same pool would repeat those tasks. P103 instead
uses the independently frozen P95 merged pool, with the unchanged P100 parser,
answer oracle, hit/control text interventions, and final assistant mask.

The P95 sweep screened **56 groups and 197 pages**. Four tables passed the
strict structure parser and offered categorical options. Thirteen candidate
category selections entered final compilation: **eight passed**, four lacked
a distinct answer-preserving control value, and one had alternate same-line
support. The eight tasks are in **two source worlds**: five railway-station
tasks and three university tasks. All eight are eval examples below 32K. This
is a small L2 complete-set addition, not broad long-range coverage.

`data/candidates/p103_wiki_categorical_delta_audit_v1/` contains the pinned
gross-to-net receipt and one support-matrix row for each of the 170 frozen
P97/P95 source groups. It verifies zero repeated page titles, page URLs or
semantic task IDs across P100 and P103. Thus the eight accepted P103 tasks
are eight net-new tasks. It records **217** P95 and **186** P97
`row_width_mismatch` table rejection instances. These are rejected table
candidates, not 403 repairable pages; safe row/column reconstruction is the
next parser bottleneck. No parser rule was relaxed to increase this batch.

Authoritative receipts:

| Artifact | Manifest SHA-256 | Result |
| --- | --- | --- |
| `data/candidates/p103_wiki_categorical_p95_native_v1/manifest.json` | `548e80540ca95206ffc0d0cbbaf1bf1e0233280054f8464b2c98e733bf975e71` | 8 native tasks |
| `data/candidates/p103_wiki_categorical_p95_unified_v1/manifest.json` | `e91490e77db27f6300c43bc869ef0e6173c68ada08d68dcab4e483d8d67e615f` | 8 normalized readers |
| `data/candidates/p103_wiki_categorical_p95_full_mask_v1/manifest.json` | `133216b5de345db4f43475b901d1aec73dbddce99d57e26ffd930fa9c32ba7ee` | 8/8 masks |
| `data/candidates/p103_wiki_categorical_delta_audit_v1/manifest.json` | `f3868678656d4f7267f5293c5448f039c3c4fb7860ed10f4ba087771b5ddd646` | zero old-page/task overlap |

The native final-reader audit passed 8/8 and pinned the exact answer and
bounded intervention hashes. The separate unified all-reader mask replay
passed 8/8: 197,271 final-chat tokens, 307 assistant-supervised tokens. All
artifacts remain `train_ready=false`. No GPU experiment or model gain is
claimed. P103 stops at this shard; integration into a later global index and
balanced materialization is handled separately.

Inspect and replay:

```bash
cat data/candidates/p103_wiki_categorical_p95_native_v1/manifest.json
cat data/candidates/p103_wiki_categorical_delta_audit_v1/manifest.json
cat data/candidates/p103_wiki_categorical_p95_full_mask_v1/manifest.json
less -R data/candidates/p103_wiki_categorical_delta_audit_v1/support_matrix.jsonl
less -R data/candidates/p103_wiki_categorical_p95_native_v1/rejected.jsonl
UV_LINK_MODE=copy uv run python scripts/p100_wiki_categorical_scan.py \
  --config configs/p103_wiki_categorical_p95_v1.json \
  --output-dir data/candidates/p103_wiki_categorical_p95_native_v1 \
  --workers 4 --verify-only
UV_LINK_MODE=copy uv run python scripts/p100_wiki_categorical_audit.py \
  --native-dir data/candidates/p103_wiki_categorical_p95_native_v1
UV_LINK_MODE=copy uv run python scripts/p100_wiki_to_unified.py \
  --config configs/p103_wiki_categorical_p95_v1.json \
  --native-dir data/candidates/p103_wiki_categorical_p95_native_v1 \
  --output data/candidates/p103_wiki_categorical_p95_unified_v1 --verify-only
UV_LINK_MODE=copy uv run python scripts/audit_unified_reader_mask.py \
  data/candidates/p103_wiki_categorical_p95_unified_v1 --all \
  --output data/candidates/p103_wiki_categorical_p95_full_mask_v1 --verify-only
UV_LINK_MODE=copy uv run python scripts/audit_p103_categorical_delta.py \
  --p97-pool data/capability_records/p97_wiki_gated_delta_v1/source_pool.json \
  --p100-native data/candidates/p100_wiki_categorical_scan_v3 \
  --p95-pool data/capability_records/p95_wiki_structural_merge_v1/source_pool.json \
  --p103-native data/candidates/p103_wiki_categorical_p95_native_v1 \
  --output-dir data/candidates/p103_wiki_categorical_delta_audit_v1 --verify-only
UV_LINK_MODE=copy uv run pytest -q tests/test_audit_p103_categorical_delta.py
```
