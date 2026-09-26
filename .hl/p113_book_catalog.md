# P113 catalog-driven real-book source and explicit-speech tasks

**QUARANTINED (2026-09-26):** The P113 `native_v6 → unified_v3 →
unified_mask_v3` chain's 43/43 parser-agreement and mask checks did not prove
correct gold. An independent target-chapter scan found at least five wrong
answers, all caused by a listed speech verb and printed name followed by a
modifier that both original parsers skipped. Affected samples:

```text
book-explicit-f72db90573fc9a3bce42-v1
book-explicit-3e5ba3a65a16ce15c488-v1
book-explicit-e72a12727d8443bc5d0d-v1
book-explicit-b94402ad7754f483393e-v1
book-explicit-b29750267446add4fc8a-v1
```

All 43 P113 book rows are excluded from the current candidate index; the
remaining figures below describe a historical diagnostic build, not admitted
training data. The frozen raw source and rejection ledgers remain useful for
rebuilding with a corrected truth contract.

## Corrected P113 candidate (not yet in global index)

The replacement freezes `data/sources/p113_book_cohort_v5/manifest.json`
(SHA-256 `08ceb2706e039775468d9bebca1500e7a389756bb981f6ab3d0b31fc90a097eb`)
with the corrected truth-parser SHA and the same 67-file mirror download
receipt. The generator and independent quote-first verifier now recognize
named speech tags followed by lowercase adverbs or clauses, including
`Alice panted as she ran`, `exclaimed Craddock enthusiastically`, `declared
Heavitree when`, `said Michael bitterly` and `Penelope whispered to him`.
Behavioral regression tests cover all five forms and a later utterance
changing the blind answer.

The corrected native set is
`data/candidates/p113_book_native_v7/manifest.json` (SHA-256
`2951dc010bd17c4b1a3496f1fb11fa9b5e434a082a86eac95e9123521e1f6425`).
It has **44 independent tasks** from 14 productive source works: 34 train,
10 eval; 38 tasks come from newly cataloged works. Physical final-chat lengths
are 13 at 32–64K, 23 at 64–128K and eight at 128–256K. Executed source to
answer token envelopes are 16,612–148,527 (median 63,994.5); final chat
contains 3,785,454 tokens and 464 assistant-supervised tokens. These remain
one literal cross-chapter binding operation, not a broad narrative reasoning
or L5 policy product.

The corrected unified shard is
`data/candidates/p113_book_unified_v4/manifest.json` (SHA-256
`dc5be273da1ea07cc50d855da5b3ad7512c87bcfafb0c0aab03fb094f7c8c6a8`),
with full-row mask receipt
`data/candidates/p113_book_unified_mask_v4/manifest.json` (SHA-256
`9af104c8cae2b942dcccf2d7f6e76899ea8ffbdfafd1f46a2f90ef5b0fad5a27`).
Native independent audit and final mask both report 44/44. A separate
read-only review rescanned all 44 source and target chapters with wider
syntax and found no new wrong gold in the declared printed-name/verb scope;
it verified full chapter bytes and unified/native reader equality. The old
five wrong-gold sample IDs are absent. Four corrected answers reappear under
new IDs; the fifth source pair is not selected. This is still a local
candidate with `train_ready=false` and no measured model gain.

```bash
cat data/sources/p113_book_cohort_v5/manifest.json
less -R data/sources/p113_book_cohort_v5/attempt_ledger.jsonl
cat data/candidates/p113_book_native_v7/manifest.json
less -R data/candidates/p113_book_native_v7/proofs.jsonl
cat data/candidates/p113_book_native_v7/independent_audit_manifest.json
cat data/candidates/p113_book_unified_v4/manifest.json
cat data/candidates/p113_book_unified_mask_v4/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_sources.py --config configs/p113_book_catalog_v1.json --plan data/sources/p113_book_catalog_v1/plan_v2.json --attempt-dir data/sources/p113_books_v1 --prior-source-dir data/sources/p112_books_v1 --output data/sources/p113_book_cohort_v5 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_tasks.py --source-dir data/sources/p113_book_cohort_v5 --output data/candidates/p113_book_native_v7 --workers 4 --max-tasks-per-book 4 --min-lineage-tokens 16384 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_audit.py --source-dir data/sources/p113_book_cohort_v5 --native-dir data/candidates/p113_book_native_v7 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_to_unified.py --source-dir data/sources/p113_book_cohort_v5 --native-dir data/candidates/p113_book_native_v7 --output data/candidates/p113_book_unified_v4 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_unified_mask.py --unified-dir data/candidates/p113_book_unified_v4 --output data/candidates/p113_book_unified_mask_v4 --verify-only
```

## Historical quarantined v3 build

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

The original native audit, unified merge and full-row assistant mask replays
passed **43/43 under the faulty parser**. This agreement did not detect the
modifier cases above. The quarantined unified manifest SHA-256 is
`2d1e0aadec7483ff7a2bc1777c7057a144cd2d3eb1864b46dd6dfade6eb1d013`;
the mask rows SHA-256 is
`5076b07d5d1bc2d51ab0fc63f8bb723c7e40dd32398fee688182b10f8b101b2c`.
No source group or normalized work key crossed train/eval. These checks do not
admit the shard; no model improvement or release eligibility is established,
and `train_ready=false`.
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
