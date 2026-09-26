# P113 catalog-driven real-book source and explicit-speech tasks

This wave replaces the quarantined P112 book answers with a new truth
contract. The old 20 P112 rows remain excluded from the corrected global
index. P113 reuses three frozen *source texts* but regenerates all questions,
answers and proof receipts; it never imports old labels.

## Official source route and bounded intake

Project Gutenberg [Offline Catalogs](https://www.gutenberg.org/ebooks/offline_catalogs.html)
recommends its machine-readable metadata instead of crawling listing pages.
Its [terms](https://www.gutenberg.org/policy/terms_of_use.html) and
[robot guidance](https://www.gutenberg.org/policy/robot_access.html) direct bulk
content retrieval to a mirror. The chosen
[listed high-speed mirror](https://www.gutenberg.org/MIRRORS.ALL) is
`https://gutenberg.pglaf.org/`; this bounded pilot used at most 0.5 requests
per second. The [license](https://www.gutenberg.org/policy/license) and each
work's US jurisdiction notice are retained. These are local research candidates;
the metadata and source notice alone do not establish worldwide release rights.

The official compressed catalog SHA-256 is
`9965df5b1fdd56f19c876054c891c09b2a98d65ab910bee6e91fa734e645f31d`.
The declarative request groups six literary subject classes, deduplicates
catalog variants by normalized work key and applies a stable work-level split.
Among 79,433 catalog rows, 11,725 unique works met this *metadata proposal*
filter. The bounded plan contains 72 proposed downloads (12 per class; 59
train/13 eval). These counts are not source worlds or training tasks.

The mirror attempt receipt fixes the first 67 planned text files and their raw
SHA values. All 67 can be replayed locally. Of these, 31 passed actual UTF-8,
US notice, chapter, direct named-speech and cross-chapter capacity checks. The
selected cohort has 12 new works across six subjects plus three previously
frozen sources; train/eval source-world split is 11/4. The source manifest pins
the mirror attempt receipt and truth-parser SHA, so a later parser change
cannot silently alter the cohort. Source-level rejections
and nonproductive task worlds are preserved in separate ledgers. The source
pool has 15 worlds, but only 13 produce admitted tasks.

The 36 source rejections comprise 19 without eight detected chapter headings,
five short/contents-like chapter bodies, three repeated chapter numbers,
three without sufficient cross-chapter named speech, two with fewer than eight
body chapters, two with short work bodies, one without a first chapter and one
without a title/author header. Another 19 structurally viable downloaded
works were not selected under the 12-new-work cohort cap; they are not
quality failures. Two selected works produced zero final tasks because all
their candidate named speakers triggered alias uncertainty. The task-level
`book_results` ledger further records each rejected proposal reason; these
counts are tested pair/label possibilities, not unique failed task rows.

## New truth contract

The question asks for the last *single-line* quotation tagged with the same
**printed speaker name** in a later chapter. Its listed speech verbs include
`said`, `asked`, `replied`, `cried`, `exclaimed`, `panted`, and other named
speech tags; both quote-then-name and name-then-quote forms are recognized.
The answer contract excludes implicit dialogue, pronoun inference and nickname
resolution. If explicit names may be aliases (e.g. `Holmes` and
`Sherlock Holmes`), the candidate is rejected.

One regex-based parser proposes tasks; an independently coded quote-first
scanner replays all direct attributions in the source and target chapters,
including short utterances. Both must agree for the relevant printed label.
The verifier reads the complete frozen source, confirms reader sections are
unaltered contiguous slices, checks full-book quote uniqueness, and resolves
the answer without seeing gold. Replacing the source label with another
verified speaker must change the answer. Deleting that source label or every
equivalent target label makes the blinded direct-tag solver return unsolved.
These are bounded text interventions, not a proof that no natural-language
inference path remains. Final reader chat is
tokenized with the pinned template, evidence spans are mapped to exact token
positions, and only assistant answer tokens receive loss.

The native compiler generated **43 independent task views** from 13 productive
worlds: 36 use newly cataloged works, seven use prior frozen texts. Train/eval
is **34/9**; physical lengths are 13 at 32–64K, 26 at 64–128K and four at
128–256K. Observed executed source-to-target token envelopes span
16,630–147,683 (median 60,984). Last evidence-to-question gaps span
80–58,678 (median 3,214). There are 3,433,629 final chat tokens and 450
assistant-supervised tokens. These are executed evidence-path spans, not an
exhaustive shortest-proof certificate, and the short answers make this one
L2 binding lane rather than a balanced long-context curriculum. The catalog
does not prove hundreds of domains; all selected works are English literature.

Independent native audit, unified candidate merge and full-row assistant mask
replays passed **43/43**. The final unified manifest SHA-256 is
`2d1e0aadec7483ff7a2bc1777c7057a144cd2d3eb1864b46dd6dfade6eb1d013`;
the mask rows SHA-256 is
`5076b07d5d1bc2d51ab0fc63f8bb723c7e40dd32398fee688182b10f8b101b2c`.
No source group or normalized work key crosses train/eval. These checks admit
the shard as a local candidate; they do not establish model improvement or
release eligibility, and `train_ready=false`.
Earlier diagnostic builds are not interchangeable: initial 46 tasks became
43 after preserving more evaluation works and adding missed speech verbs.
The comparison has 29 identical task IDs, 17 removed and 14 added; this is a
source/contract change, not a filter silently dropping three failed rows.

## Reproduce and inspect

```bash
cat data/sources/p113_book_catalog_v1/plan_v2.json
cat data/sources/p113_books_v1/download_manifest.json
cat data/sources/p113_book_cohort_v4/manifest.json
less -R data/sources/p113_book_cohort_v4/attempt_ledger.jsonl
cat data/candidates/p113_book_native_v6/manifest.json
less -R data/candidates/p113_book_native_v6/proofs.jsonl
cat data/candidates/p113_book_native_v6/independent_audit_manifest.json
cat data/candidates/p113_book_unified_v3/manifest.json
cat data/candidates/p113_book_unified_mask_v3/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_catalog.py --config configs/p113_book_catalog_v1.json --catalog data/sources/p113_book_catalog_v1/pg_catalog.csv.gz --output data/sources/p113_book_catalog_v1/plan_v2.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_download.py --config configs/p113_book_catalog_v1.json --plan data/sources/p113_book_catalog_v1/plan_v2.json --output data/sources/p113_books_v1 --max-attempts 67 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_sources.py --config configs/p113_book_catalog_v1.json --plan data/sources/p113_book_catalog_v1/plan_v2.json --attempt-dir data/sources/p113_books_v1 --prior-source-dir data/sources/p112_books_v1 --output data/sources/p113_book_cohort_v4 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_tasks.py --source-dir data/sources/p113_book_cohort_v4 --output data/candidates/p113_book_native_v6 --workers 4 --max-tasks-per-book 4 --min-lineage-tokens 16384 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_audit.py --source-dir data/sources/p113_book_cohort_v4 --native-dir data/candidates/p113_book_native_v6 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_to_unified.py --source-dir data/sources/p113_book_cohort_v4 --native-dir data/candidates/p113_book_native_v6 --output data/candidates/p113_book_unified_v3 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_unified_mask.py --unified-dir data/candidates/p113_book_unified_v3 --output data/candidates/p113_book_unified_mask_v3 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p113_book_catalog.py tests/test_p113_book_truth.py
```
