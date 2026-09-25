# P108 automatic Wiki topic discovery and source-to-reader campaign

P108 replaces manually enumerated Wiki terms with a pinned, replayable
discovery pass over existing real MediaWiki search responses and article
category text. It uses the P93 intake, P97 sharder and P99 campaign unchanged.
The 12 seed intakes contain 212 frozen discovery queries. Their title/category
metadata yielded 250 candidate terms; the first exploratory catalog allowed
120, but 41 of those had no known title outside the 536-page prior pool.
The strict catalog rejects such terms unless at least three source categories
support them. It also rejects obvious title-clause fragments. The resulting
81 terms span 17 seed domain labels and 3 P97 shards of 27 queries each.

| Stage | Count | Meaning |
| --- | ---: | --- |
| Prior union | 170 groups / 536 pages | P95 plus P97, unique title and URL, with P92 router still checked by the gate |
| Strict vocabulary | 81 terms | 65 train / 16 eval, auto-derived from pinned source metadata |
| P93 acquisition | 645 HTTP requests | 81 gross groups / 186 frozen pages / 7,384 facts, zero intake failures |
| Global P92+prior gate | 77 groups / 171 pages / 7,116 facts | Four whole groups rejected on title/URL overlap, including one cross-split conflict |
| Native structural probe | 37 jobs | 34 lookup, 2 scan, 1 pair; 288 unsupported cells |
| Native reader compilation | 530 tasks | 34 productive jobs and 31 distinct source worlds; 77 rejected rows |
| Unified final readers | 530 tasks | 405 train / 125 eval; 485 below 32K and 45 in the 32K bin |
| Final assistant mask | 6,451,831 / 5,246 tokens | Full chat / assistant-supervised; 530/530 independently replayed |

The 530 operations are **499 table-cell lookups, 20 dense interval scans and
11 earlier-year table pairs**. Of the 31 worlds with tasks, 29 support only
one operation, one supports two and one supports three. Every selected evidence
span has exact final-prompt token offsets. Observed lineage width is median
4, p90 8 and maximum 7,010 tokens (lookup 1–14; scan 817–1,251; pair
4,245–7,010). No case reaches 16K of observed multi-evidence span. The
distance from the last selected evidence to the input end reaches 42,620
tokens, but that is placement, not a shortest-proof or learning guarantee.
This campaign adds real sources and tasks, while its current native task mix
remains heavily L1. Length buckets, source groups and task views are separate
quantities.
Only 11 of the 16 gated domain labels produced tasks, and education supplied
274/530 tasks. Domain labels inherit the frozen seed-query family and are not
independent semantic verification; one off-topic `theaters and campaigns`
arts-labeled source produced zero tasks. The inventory scanner also supports
later nested campaign intakes, so future waves can use newly frozen metadata
without hand-writing another per-domain term list.

## Same-world table-capability probe

All 77 gated groups and 171 pages were screened through the existing P100
visible-table parser. It found eight strict complete tables; two tables in
one world offered nine categorical complete-set options **before** final
reader admission. Seventy-one rendered row-width rejections affected 14
pages/groups. Those are only candidates for P106 raw-grid repair.

The 14 exact revision URLs were frozen separately with zero failures. The
P97 gate gives an ordered accepted-name list whereas P106 expects an integer;
a P108 gate bridge checks the original gate SHA, exact name order, pool SHA,
count and prior-router pin before exposing the integer. The raw-to-reader
support ledger collapsed the 71 rejections into 58 distinct
`document/heading/header` targets. Fifty-one lacked structural support;
two had unsupported multiline cells, two had visible delimiters/newlines,
and one needed span-aware set semantics. Two tables were cell-aligned with
six category options. The P106/P105-pinned compiler rejected one option for
same-line alternative support, leaving **five** independently audited tasks
in two worlds (2 train / 3 eval). They are all below 32K, with 71,037 final
chat tokens and 141 assistant-supervised tokens. Their unified shard and
all-reader mask both passed replay. These five are an independent P108
candidate shard; they were not included in the P109 bank's 530-task P108
source lane.
The reused P106 unifier retains its recipe lane label
`p106_wiki_grid_p95`; the P108 raw/source/gate hashes in its receipt identify
the actual cohort. A later sharded-index integration should give this shard a
distinct source name and check task identity against prior P106 grid rows.
The current five have zero `sample_id`, `task_id`, `source_group` and
`world_id` overlap with the frozen P106 grid shard.

## Frozen receipts and commands

- Strict catalog: `data/capability_records/p108_wiki_autotopic_catalog_v2/manifest.json`
  SHA256 `daef457722aff63693cb124c06e82c18d662963a4e45148215a449184b3b4dca`;
  per-term evidence and rejection reasons: `ledger.jsonl` in the same directory.
- P97 plan: `data/capability_records/p108_wiki_autotopic_plan_v1/manifest.json`
  SHA256 `032077549c1631f3956f0992406b50d89b53365a923e836f3636767d0508ed60`.
- P99 campaign: `data/capability_records/p108_wiki_autotopic_campaign_v1/gate_result.json`
  SHA256 `7ad246c3a8c396ad138897994874b95ee6995670af58162c0dd3be69981710b7`;
  `overlap.json` records every rejected group.
- Unified 530: `data/candidates/p108_wiki_autotopic_unified_v1/merged/manifest.json`
  SHA256 `5a2c86c7552b27a5685d6246ca166ce1b1010b70926c59df88a4623e9a16108f`;
  final mask manifest SHA256 `16ac7a5941ba9acdc9a91820181042ed8b3f76ce381b0af0709b403c7e9246f4`.
- Final capability/evidence audit:
  `data/capability_records/p108_wiki_autotopic_final_audit_v1.json`
  SHA256 `44f624c84cf8c46db1930daf04c58f2dee735abf6f38ebe2a470dabd4c10b547`.
- P100/P106 support ledger:
  `data/capability_records/p108_wiki_autotopic_support_v1/manifest.json`
  SHA256 `18522b301bce14a400ee9605ec8baf388552e2f1cebff2398977b833f32426b4`.
- Five new raw-grid tasks: `data/candidates/p108_wiki_grid_native_v1/manifest.json`
  SHA256 `0abe25b115f97fcc71143fb8f8ea11120d869f1221c6d2be3f6b7ade0064aa5e`;
  unified manifest SHA256 `bd58838162939cb952d9a600ea0f083a92e61e247d23b3d6206d18e0f5b92d35`;
  all-reader mask SHA256 `26681dadfeea2b89a620fcc7868e2da37bf8053e8c2e8d44249e85ad51c7f5a0`.

```bash
cat data/capability_records/p108_wiki_autotopic_final_audit_v1.json
less -R data/capability_records/p108_wiki_autotopic_catalog_v2/ledger.jsonl
less -R data/capability_records/p108_wiki_autotopic_campaign_v1/overlap.json
UV_LINK_MODE=copy uv run --offline python scripts/p108_wiki_autotopic_catalog.py \
  --config configs/p108_wiki_autotopic_catalog_v2.json \
  --output-dir data/capability_records/p108_wiki_autotopic_catalog_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/run_p99_wiki_campaign.py \
  --config configs/p108_wiki_autotopic_campaign_v1.json \
  --output-dir data/capability_records/p108_wiki_autotopic_campaign_v1 --mode verify
UV_LINK_MODE=copy uv run --offline python scripts/run_unified_synthesis_batch.py \
  --config configs/p108_wiki_autotopic_unified_v1.json \
  --output data/candidates/p108_wiki_autotopic_unified_v1 --workers 4 --resume
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py \
  data/candidates/p108_wiki_autotopic_unified_v1/merged --all \
  --output data/candidates/p108_wiki_autotopic_unified_mask_v1 \
  --max-seq-len 131072 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p106_wiki_to_unified.py \
  --config configs/p108_wiki_autotopic_grid_v1.json \
  --native-dir data/candidates/p108_wiki_grid_native_v1 \
  --output data/candidates/p108_wiki_grid_unified_v1 --verify-only
```

The frozen snapshots retain article title, page URL, exact revision URL and
Wikipedia CC BY-SA attribution; all output receipts remain
`train_ready=false`. Neither source breadth nor the scoped table edits prove
model improvement or unrestricted reader-text necessity. The loose 120-term
catalog and the interrupted symlink-path unified attempt are diagnostic only;
the strict 81-term catalog and original native batch are authoritative.
