# P124 primary-author book acquisition and task shards

**Local research candidates; `train_ready=false`.** P124 consumes the frozen
P121 V3 catalog plan. It does not change the P113/P114/P120 sources, their
splits, or the P120 candidate bank. The pinned P121 plan uses one untagged,
personal-name-shaped catalog author key per proposed book. That key is a
catalog label, not proof of real-person identity or independence of a
pseudonym.

The globally rate-limited Gutenberg mirror campaign made **180/180** planned
requests at 0.5 requests/second with four in-flight workers; all 180 returned
complete raw text within the 2 MB cap. The existing P114 structural freezer
found and froze **79** books (58 train, 21 eval; 72 direct catalog author keys).
The other 101 were rejected for documented structural reasons, chiefly too
few chapter headings (68). The frozen books cover **15 literature subject
classes**, not 15 unrelated domains. Source records retain exact raw/body
SHA, URL, title, author, chapter count and split. The source receipt and all
180 decisions are in `data/sources/p124_book_cohort_v1/manifest.json` and
`attempt_ledger.jsonl`.

Before compiling tasks, the P124 audit compared the 79 new books against all
147 frozen P113/P114/P120 book worlds. Ebook IDs, stored and catalog-recomputed
work keys, raw/body SHA, and direct catalog author keys have no overlap.
Exact P113-parsed chapter text hashes have no old/new or between-new-book
collision, including across train/eval. The 79 new books have no direct
author-key split crossing. This check is exact only: near-duplicate chapters,
unlisted coauthors and pseudonyms remain outside its identity claim. The
source audit is `data/candidates/p124_book_source_audit_v1.json`.

The same frozen worlds feed two existing long-book operations:

| Operation | Independent candidates | Train / eval | Productive worlds |
| --- | ---: | ---: | ---: |
| P113 explicit named-speech follow | 232 | 161 / 71 | 69 |
| P116 complete printed-speaker intersection | 47 | 42 / 5 | 23 |

The union is **279 independent candidate tasks from 70 worlds**, with 22
worlds productive for both operations. Nine frozen worlds produced neither.
This is world reuse across operations, not proof that two individual questions
share the same necessary fact. The 279 final-chat inputs contain 23,064,878
tokens and 2,525 assistant-supervised tokens (0.01095% of full-chat tokens).
Actual full-chat lengths are 32,093–194,771 tokens: 1 below 32K, 90 at
32–64K, 158 at 64–128K, and 30 at 128–256K. No 256K+ case was admitted.
All **279/279** final readers have `exact_assistant_mask_checked` rows,
positive supervised tokens, `loss_mask_start == input_tokens`, and
`input_tokens + supervised_tokens == full_chat_tokens`. The P113 all-reader
mask receipt is `data/candidates/p124_book_named_speech_mask_v1/manifest.json`;
the P116 mask rows are in its unified shard. The tiny answer-token fraction
is a material training-design limitation.

| Literature subject | Frozen worlds | Named speech | Speaker intersection |
| --- | ---: | ---: | ---: |
| adventure_fiction | 8 | 27 | 9 |
| detective_fiction | 8 | 25 | 11 |
| fantasy_fiction | 6 | 18 | 5 |
| gothic_fiction | 2 | 6 | 0 |
| historical_fiction | 6 | 13 | 3 |
| humorous_stories | 4 | 13 | 0 |
| juvenile_fiction | 8 | 24 | 4 |
| pirate_fiction | 5 | 8 | 1 |
| political_fiction | 7 | 15 | 0 |
| romance_stories | 5 | 14 | 4 |
| school_stories | 7 | 25 | 6 |
| sea_stories | 5 | 16 | 0 |
| social_fiction | 2 | 8 | 1 |
| war_stories | 3 | 8 | 1 |
| western_stories | 3 | 12 | 2 |

P113's independent quote-first full-reader audit checked 232/232. Its bounded
anchor-to-answer token extent is 16,391–191,752 (median 63,026). P116 screened
26,162 chapter pairs and found 6,013 structurally eligible; final attribution,
unknown-speaker, intervention, token-distance and per-book limits admitted 47.
Its closest recognized cross-chapter shared-speaker support distance is
16,391–112,577 tokens (median 51,678). These are two different bounded
lineage measures, not an unrestricted shortest natural-language proof. Both
shards replayed byte-identically through their compilers. The P120 bank
overlap check found zero source-group, sample-ID or task-ID collisions with
the existing 14,028 candidate views; P124 is **not appended to that bank**.
The manifest paths are:

```text
data/candidates/p124_book_named_speech_unified_v1/manifest.json
data/candidates/p124_book_intersection_unified_v1/manifest.json
data/candidates/p124_book_shard_overlap_v1.json
```

The answer contract is printed single-line quotations with explicit named
speech verbs, and printed speaker names in two named chapters. It excludes
implicit dialogue and unrestricted coreference. No gold-blind model readout,
generalization result, loader-level training run or SFT gain is claimed.

## Reproduce and inspect

```bash
cat data/sources/p124_book_attempts_v1/download_manifest.json
less -R data/sources/p124_book_cohort_v1/attempt_ledger.jsonl
cat data/sources/p124_book_cohort_v1/manifest.json
cat data/candidates/p124_book_source_audit_v1.json
cat data/candidates/p124_book_shard_overlap_v1.json
less -R data/candidates/p124_book_named_speech_unified_v1/proofs.jsonl
less -R data/candidates/p124_book_intersection_unified_v1/proofs.jsonl

UV_LINK_MODE=copy uv run --offline python scripts/p124_book_primary_acquire.py --config configs/p121_book_primary_author_v3.json --plan data/sources/p121_book_primary_plan_v3/plan.json --phase download --output data/sources/p124_book_attempts_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p114_book_scale.py --config configs/p121_book_primary_author_v3.json --phase freeze --plan-dir data/sources/p121_book_primary_plan_v3 --download-dir data/sources/p124_book_attempts_v1 --output data/sources/p124_book_cohort_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p124_book_primary_acquire.py --config configs/p121_book_primary_author_v3.json --plan data/sources/p121_book_primary_plan_v3/plan.json --phase audit-source --catalog data/sources/p113_book_catalog_v1/pg_catalog.csv.gz --source-dir data/sources/p124_book_cohort_v1 --output data/candidates/p124_book_source_audit_v1.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_tasks.py --source-dir data/sources/p124_book_cohort_v1 --output data/candidates/p124_book_named_speech_native_v1 --workers 4 --max-tasks-per-book 4 --min-lineage-tokens 16384 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_audit.py --source-dir data/sources/p124_book_cohort_v1 --native-dir data/candidates/p124_book_named_speech_native_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_to_unified.py --source-dir data/sources/p124_book_cohort_v1 --native-dir data/candidates/p124_book_named_speech_native_v1 --output data/candidates/p124_book_named_speech_unified_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_unified_mask.py --unified-dir data/candidates/p124_book_named_speech_unified_v1 --output data/candidates/p124_book_named_speech_mask_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p116_book_speaker_intersection.py --config configs/p124_book_speaker_intersection_v1.json --phase screen --output data/candidates/p124_book_intersection_screen_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p116_book_speaker_intersection.py --config configs/p124_book_speaker_intersection_v1.json --phase compile --screen-dir data/candidates/p124_book_intersection_screen_v1 --output data/candidates/p124_book_intersection_unified_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p120_book_cohort_scale.py --config configs/p120_book_cohort_v1.json --phase audit-shards --source-dir data/sources/p124_book_cohort_v1 --prior-refs-dir data/candidates/p120_candidate_refs_v1 --unified-dir data/candidates/p124_book_named_speech_unified_v1 --unified-dir data/candidates/p124_book_intersection_unified_v1 --screen-dir data/candidates/p124_book_intersection_screen_v1 --output data/candidates/p124_book_shard_overlap_v1.json --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p124_book_primary_acquire.py
```
