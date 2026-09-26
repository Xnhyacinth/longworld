# P136 deterministic Gutenberg window, positions 180:360

P136 uses the **same frozen P121 v3 catalog plan** and derives the next 180
candidate IDs by `start_offset=180` and `window_size=180`. No ID or title is
manually selected. The P136 window plan pins its parent plan SHA and the exact
ordered ebook-ID hash. The downloader checks that every candidate equals the
parent slice before requests, and records the offset/parent/window hashes in
the attempt receipt. The P136 freeze wrapper verifies that receipt and the
derived plan before invoking the unchanged P114 structural freezer; the
source manifest pins both the attempt manifest and window-plan SHA. The
original P124 first-window download and freeze artifacts replay byte-for-byte.

The window contains 145 planned train and 35 planned eval requests across 17
Gutenberg literature subject labels. There is no ebook/work ID overlap with
positions 0:180. Twenty-three catalog author components appear in both
windows, but the pinned P121 split assignment places none across train and
eval. This is **one literature domain with multiple topics**, not 17 domains.

The source audit compares every newly frozen book with P113, P114, P120, and
P124 frozen cohorts. It checks ebook IDs, stored and recomputed catalog work
keys, raw and normalized body SHA, exact P113-parsed chapter SHA, direct
catalog author keys, and train/eval split crossings. Same-author books in
different cohorts are allowed only when their split agrees; the overlap is
reported rather than being counted as a new independent author. These are
exact source-identity checks, not near-duplicate or pseudonym detection.

The bounded mirror campaign attempted all **180** planned IDs at the pinned
global 0.5 requests/second rate with four workers. It downloaded 179 complete
texts and recorded one over-2-MB prefix. The unchanged P114 structural gate
froze **73 books** (62 train, 11 eval) across 16 actually represented literary
topics. Its complete 180-row decision ledger is
`data/sources/p136_book_cohort_v1/attempt_ledger.jsonl`. The 107 source
rejections comprise 67 with too few chapter headings, 10 short/TOC chapter
bodies, 8 repeated chapter numbers, 6 insufficient named-speech chapter
pairs, 5 too few body chapters, 5 short work bodies, 4 missing first-chapter
headings, 1 missing title/author header, and 1 over-cap text. The source audit
compared these 73 against 226 previously
frozen books and found zero ebook/work/raw/body/exact-chapter overlap and zero
author-key split crossings. Four direct author keys recur in the same split.

The existing P113 named-speech and P116 printed-speaker-intersection compilers
produced **194** and **46** independent candidate tasks, respectively.
P113 has 166 train/28 eval tasks from 59 books; P116 has 41 train/5 eval
tasks from 21 books. The union is **240 tasks from 61 books**; 19 books support
both operations, and 12 frozen books produce neither. These are independent
tasks, not multiple length views of a single question. The 240 final-chat
inputs span 33,398-201,386 tokens and total 18,834,781 tokens, with just
2,102 assistant-supervised tokens (0.0112%). Physical length bins are 82 at
32-64K, 135 at 64-128K, and 23 at 128-256K; no case was padded to a target
length. P113 independently audited all 194 readers; its bounded
anchor-to-answer extent is 16,614-193,578 tokens (median 59,840). P116
screened 33,671 chapter pairs, 5,407 structurally eligible, then admitted 46
after attribution, unknown-speaker, intervention and final-token gates. Its
closest recognized cross-chapter shared-speaker support distance is
18,149-115,499 tokens (median 44,898). Both measures have limited witness
scope and do not prove unrestricted natural-language shortest paths.

All **240/240** final readers have positive supervised tokens, exact
assistant-only mask rows, `loss_mask_start == input_tokens`, and
`input_tokens + supervised_tokens == full_chat_tokens`. The two task shards
have zero source-group, sample-ID or semantic-task-ID overlap with P124's 279
candidates; the P120 14,028-view overlap audit also found no source-group
collision. This wave remains a local research candidate and
`train_ready=false`; it does not claim a model training gain. The very small
answer-token fraction is a material training-design limitation.

```text
data/candidates/p136_book_named_speech_unified_v1/manifest.json
data/candidates/p136_book_named_speech_mask_v1/manifest.json
data/candidates/p136_book_intersection_unified_v1/manifest.json
data/candidates/p136_book_shard_overlap_v1.json
```

## Reproduce

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p136_book_window.py --config configs/p136_book_window_v1.json --phase prepare --output data/sources/p136_book_window_plan_v1/plan.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p124_book_primary_acquire.py --config configs/p121_book_primary_author_v3.json --plan data/sources/p136_book_window_plan_v1/plan.json --phase download --output data/sources/p136_book_attempts_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p136_book_window.py --config configs/p136_book_window_v1.json --phase freeze --window-plan data/sources/p136_book_window_plan_v1/plan.json --attempts data/sources/p136_book_attempts_v1 --output data/sources/p136_book_cohort_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p136_book_window.py --config configs/p136_book_window_v1.json --phase audit-source --window-plan data/sources/p136_book_window_plan_v1/plan.json --catalog data/sources/p113_book_catalog_v1/pg_catalog.csv.gz --source-dir data/sources/p136_book_cohort_v1 --output data/candidates/p136_book_source_audit_v1.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_tasks.py --source-dir data/sources/p136_book_cohort_v1 --output data/candidates/p136_book_named_speech_native_v1 --workers 4 --max-tasks-per-book 4 --min-lineage-tokens 16384 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_audit.py --source-dir data/sources/p136_book_cohort_v1 --native-dir data/candidates/p136_book_named_speech_native_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_to_unified.py --source-dir data/sources/p136_book_cohort_v1 --native-dir data/candidates/p136_book_named_speech_native_v1 --output data/candidates/p136_book_named_speech_unified_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_unified_mask.py --unified-dir data/candidates/p136_book_named_speech_unified_v1 --output data/candidates/p136_book_named_speech_mask_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p116_book_speaker_intersection.py --config configs/p136_book_speaker_intersection_v1.json --phase screen --output data/candidates/p136_book_intersection_screen_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p116_book_speaker_intersection.py --config configs/p136_book_speaker_intersection_v1.json --phase compile --screen-dir data/candidates/p136_book_intersection_screen_v1 --output data/candidates/p136_book_intersection_unified_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p120_book_cohort_scale.py --config configs/p120_book_cohort_v1.json --phase audit-shards --source-dir data/sources/p136_book_cohort_v1 --prior-refs-dir data/candidates/p120_candidate_refs_v1 --unified-dir data/candidates/p136_book_named_speech_unified_v1 --unified-dir data/candidates/p136_book_intersection_unified_v1 --screen-dir data/candidates/p136_book_intersection_screen_v1 --output data/candidates/p136_book_shard_overlap_v1.json --verify-only
UV_LINK_MODE=copy uv run --offline python -m pytest tests/test_p136_book_window.py tests/test_p124_book_primary_acquire.py -q
```

```bash
cat data/sources/p136_book_attempts_v1/download_manifest.json
less -R data/sources/p136_book_cohort_v1/attempt_ledger.jsonl
cat data/sources/p136_book_cohort_v1/manifest.json
cat data/candidates/p136_book_source_audit_v1.json
less -R data/candidates/p136_book_named_speech_unified_v1/sample_index.jsonl
less -R data/candidates/p136_book_intersection_unified_v1/proofs.jsonl
```
