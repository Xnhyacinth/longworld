# P120 catalog-scaled book source and task campaign

**Local research candidates; `train_ready=false`.** P120 uses the same pinned
Project Gutenberg catalog as P113/P114 but a new, parameterized 17-subject
campaign. These are literature subject classes, not 17 independent domains.
The planner computes author connected components over *all* English catalog
texts, including unselected co-authored works. It excludes the P113 v6 and
P114 v2 source worlds and all P114 mirror attempts, then round-robins subject
classes. The official catalog bytes have SHA-256
`9965df5b1fdd56f19c876054c891c09b2a98d65ab910bee6e91fa734e645f31d`.
This closure is conservative: one transitive contributor component contains
30,706 English catalog rows and intersects prior sources. The planner excludes
30,661 rows for `prior_author_component`, many of which are not duplicate
works. This protects the current split but sharply limits future catalog
yield; a later planner should distinguish primary-author identity from
anthology or many-contributor links and rerun split checks before changing
the policy. P120's frozen cohort is not retrospectively relabeled.

The `configs/p120_book_cohort_v1.json` campaign caps 180 mirror attempts,
four concurrent in-flight requests, a **global** 0.5 requests/second rate,
2 MB/book and 80 frozen books. The 180 attempted entries include 160 author
components, 149 train and 31 eval labels. All 180 mirror requests returned
complete raw texts. The P114 source parser found 75 structurally viable
books; 74 were frozen, including 62 train and 12 eval books from 71 author
components. All 17 planned subject classes survived source freezing. This is
source breadth, not yet 17 task categories.

Source rejection reasons are frozen in
`data/sources/p120_book_cohort_v1/attempt_ledger.jsonl`: 58 too few chapter
headings, 18 insufficient shared named speech, nine missing title/author
headers, eight repeated chapter numbers, five too-short bodies, four short/TOC
chapters, two fewer than eight usable body chapters and one missing first
chapter. One structurally viable source was not frozen due to the selection
cap/author limit. The source overlap audit compared ebook ID, normalized work,
raw SHA, body SHA and the full-catalog author component with P113 v6/P114 v2:
all five intersections are empty, and no P120 source internally repeats
these identities. It passed **before** task compilation.

P120 then applies two independently audited, syntactically scoped long-book
operations to these same frozen worlds:

* P113 named-speech anchor and last explicit target-chapter quote;
* P116 complete intersection of explicit printed speaker names in two
  chapters, with deletion and negative-insertion checks.

Only final readers that pass the source, broad speaker-attribution, exact
token-span and assistant-only mask contracts become unified candidates. The
answer contract excludes implicit dialogue, character coreference and
unlisted speech verbs. Long-distance support is bounded to the recognized
printed-name grammar; it is not a proof of global natural-language necessity
or model improvement. Both operations share source worlds where each produces
tasks; this does not by itself prove shared *necessary facts*.

## Frozen task accounting

The named-speech reader yielded **148 distinct tasks/views from 50 books**
(118 train / 30 eval). The complete-set reader yielded **45 distinct
tasks/views from 21 books** (38 train / seven eval). Their source-world union
is 54 books: 17 books support both operations and 20 of the 74 frozen books
support neither. The two task banks cover 16 subject classes; the two frozen
war-stories books yielded zero tasks. The numbers below are counts of actual
reader tasks, not synthetic domain labels or repeated length views.

| Catalog subject | Frozen books | Named-speech tasks | Complete-set tasks |
| --- | ---: | ---: | ---: |
| adventure_fiction | 6 | 15 | 5 |
| detective_fiction | 7 | 17 | 3 |
| fantasy_fiction | 3 | 4 | 0 |
| ghost_stories | 2 | 8 | 3 |
| gothic_fiction | 5 | 7 | 3 |
| historical_fiction | 5 | 8 | 4 |
| humorous_stories | 4 | 4 | 0 |
| juvenile_fiction | 6 | 9 | 2 |
| pirate_fiction | 4 | 4 | 3 |
| political_fiction | 7 | 19 | 5 |
| romance_stories | 4 | 9 | 4 |
| school_stories | 4 | 13 | 0 |
| science_fiction | 1 | 0 | 2 |
| sea_stories | 3 | 4 | 1 |
| social_fiction | 4 | 10 | 3 |
| war_stories | 2 | 0 | 0 |
| western_stories | 7 | 17 | 7 |

The **193** combined final-chat readers occupy the following actual token
intervals: one below 32K, 69 at 32–64K, 99 at 64–128K, and 24 at
128–256K. They contain 15,593,306 final-chat tokens but only 1,762
assistant-supervised tokens (0.0113%). The P113 148/148 all-reader mask audit
and the P116 compiler's 45/45 final mask audit both report
`exact_assistant_mask_checked`; a small loss fraction is a training-design
limitation, not a mask failure.

P113's executed anchor-to-target answer token extent is
16,501–207,200 (median 55,994.5); P116's closest recognized shared-label
cross-chapter support distance is 17,273–234,381 (median 47,588). These
measure different bounded proof witnesses, so they should not be combined as
one global minimum dependency-distance metric. Both operations were checked
in the final chat template. P113's independent source/answer audit passed
148/148, and P116's compiler replay reproduced 45/45 bytes. The global-bank
dedup receipt pins the current P117 bank's 13,835 candidate views and found
zero exact sample/task IDs or book source groups shared with either P120
shard. This is still a research-candidate quality stage; independent
adversarial review and model readout are separate.

An independent read-only review subsequently checked all 74 frozen raw/body
hashes and chapter boundaries, reproduced the full-catalog author graph and
its disjointness from the 73 prior sources, and reconstructed all 193 final
reader chapter bytes, context/answer hashes and chat masks. It checked both
direct tags for each P113 anchor/answer (296 tags), found no later direct
same-speaker quote, and recovered all 45 P116 intersection answers with an
independent case-sensitive direct-tag scanner. All 193 final-chat
re-tokenizations, assistant masks, proof token spans and ordering passed;
observed full-chat lengths range from 30,982 to 237,006 tokens. The reviewer
resolved 45 initial P113 broad suspects as apostrophe fragments, addressees,
narrative text or earlier tags; initial P116 mismatches came from the
reviewer's case-insensitive regex and disappeared after correcting it. No
confirmed wrong gold was found under the declared syntactic answer contract.
This does not establish unrestricted natural-language proof completeness or
model gain.

The source ledger gives every one of the 180 acquisition/structural decisions.
`data/candidates/p120_book_named_speech_native_v1/manifest.json` records
per-book proposals and reason counts, including 7,295 target chapters without
competing named speakers, 6,165 alias-uncertain proposals, 1,796 missing
unique anchors, 614 uncertain later same-name quotes and 47 parser
disagreements. These counts are candidate decisions, not 180 mutually
exclusive book rejections. The complete-set
`compile_ledger.jsonl` classifies all 26,728 chapter pairs: 3,684 passed
cheap structural gates, 45 became final readers, 2,559 were vetoed for a
possible unparsed or article-titled speaker, 637 for possible omitted known
speaker support, 26 for full attribution disagreement and 414 were simply
not materialized after a per-book cap. Unmaterialized capacity is not a
quality failure or accepted supervision.

```bash
cat data/candidates/p120_book_named_speech_native_v1/independent_audit_manifest.json
cat data/candidates/p120_book_named_speech_unified_v1/manifest.json
cat data/candidates/p120_book_named_speech_mask_v1/manifest.json
cat data/candidates/p120_book_intersection_screen_v1/manifest.json
cat data/candidates/p120_book_intersection_unified_v1/manifest.json
cat data/candidates/p120_book_shard_overlap_v1.json
less -R data/candidates/p120_book_named_speech_native_v1/proofs.jsonl
less -R data/candidates/p120_book_intersection_unified_v1/proofs.jsonl
less -R data/candidates/p120_book_intersection_unified_v1/compile_ledger.jsonl
less -R data/candidates/p120_book_intersection_unified_v1/unknown_tag_evidence.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_tasks.py --source-dir data/sources/p120_book_cohort_v1 --output data/candidates/p120_book_named_speech_native_v1 --workers 4 --max-tasks-per-book 4 --min-lineage-tokens 16384 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_audit.py --source-dir data/sources/p120_book_cohort_v1 --native-dir data/candidates/p120_book_named_speech_native_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_to_unified.py --source-dir data/sources/p120_book_cohort_v1 --native-dir data/candidates/p120_book_named_speech_native_v1 --output data/candidates/p120_book_named_speech_unified_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_unified_mask.py --unified-dir data/candidates/p120_book_named_speech_unified_v1 --output data/candidates/p120_book_named_speech_mask_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p116_book_speaker_intersection.py --config configs/p120_book_speaker_intersection_v1.json --phase screen --output data/candidates/p120_book_intersection_screen_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p116_book_speaker_intersection.py --config configs/p120_book_speaker_intersection_v1.json --phase compile --screen-dir data/candidates/p120_book_intersection_screen_v1 --output data/candidates/p120_book_intersection_unified_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p120_book_cohort_scale.py --config configs/p120_book_cohort_v1.json --phase audit-shards --source-dir data/sources/p120_book_cohort_v1 --prior-refs-dir data/candidates/p117_candidate_refs_v1 --unified-dir data/candidates/p120_book_named_speech_unified_v1 --unified-dir data/candidates/p120_book_intersection_unified_v1 --screen-dir data/candidates/p120_book_intersection_screen_v1 --output data/candidates/p120_book_shard_overlap_v1.json --verify-only
```

## Reproduce source stage

```bash
cat data/sources/p120_book_plan_v1/plan.json
cat data/sources/p120_book_attempts_v1/download_manifest.json
cat data/sources/p120_book_cohort_v1/manifest.json
less -R data/sources/p120_book_cohort_v1/attempt_ledger.jsonl
cat data/candidates/p120_book_source_overlap_v1.json
UV_LINK_MODE=copy uv run --offline python scripts/p120_book_cohort_scale.py --config configs/p120_book_cohort_v1.json --phase plan --catalog data/sources/p113_book_catalog_v1/pg_catalog.csv.gz --output data/sources/p120_book_plan_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p120_book_cohort_scale.py --config configs/p120_book_cohort_v1.json --phase download --plan-dir data/sources/p120_book_plan_v1 --output data/sources/p120_book_attempts_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p114_book_scale.py --config configs/p120_book_cohort_v1.json --phase freeze --plan-dir data/sources/p120_book_plan_v1 --download-dir data/sources/p120_book_attempts_v1 --output data/sources/p120_book_cohort_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p120_book_cohort_scale.py --config configs/p120_book_cohort_v1.json --phase audit-source --catalog data/sources/p113_book_catalog_v1/pg_catalog.csv.gz --source-dir data/sources/p120_book_cohort_v1 --output data/candidates/p120_book_source_overlap_v1.json --verify-only
```

The mirror attempt phase should be replayed with `--verify-only`; it rechecks
local bytes and does not issue network requests.
