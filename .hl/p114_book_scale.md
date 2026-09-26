# P114 catalog-scaled real-book candidate wave

**Local research candidate, `train_ready=false`.** This wave scales the
P113 explicit printed-name speech task across a larger, automatically selected
Project Gutenberg cohort. It does not establish broad long-context capability:
all admitted questions perform the same cross-chapter speaker binding and
last-quotation operation. No GPU training or model-gain claim is attached.

The first P114 task build, `data/candidates/p114_book_native_v1` (134 rows), is
**quarantined in full**. Independent review found at least five wrong gold
answers: four missed attribution tags split by a line break (Elfie, Gowan,
Manco and Reynolds) and one missed Reynolds attribution with an adverb between
the printed name and verb. Its `p114_book_unified_v1` derivative must not enter
an index. P113's earlier 44-row v4 chain is also quarantined. The current
build uses an independent high-recall *veto* on later plausible direct
attributions in the target chapter. It rejects ambiguity instead of inferring
a new answer; the generator keeps its high-precision truth parser. The P113
source and this P114 source pin the truth parser SHA
`804c7cb77c5a9d8cb4531991bf36e2022cd6ad65b663a88f0fa3d0d3f0a27033`
and the veto SHA
`97c3b94034b55ac8e170640ef5a41adf7317a281f313d06082199553b14892e6`.

## Official source and capacity accounting

The [Project Gutenberg offline catalog](https://www.gutenberg.org/ebooks/offline_catalogs.html)
is the metadata source. Its compressed CSV snapshot has SHA-256
`9965df5b1fdd56f19c876054c891c09b2a98d65ab910bee6e91fa734e645f31d`.
The [robot access guidance](https://www.gutenberg.org/policy/robot_access.html)
and [mirror list](https://www.gutenberg.org/MIRRORS.ALL) support the selected
`gutenberg.pglaf.org` mirror route. The loader capped traffic at 0.5 requests
per second, 160 attempts and 2,000,000 bytes per book, storing the response
bytes and SHA for every completed or capped transfer. The one HTTP 404 has a
transport-status receipt but no raw bytes. Individual raw texts retain the
Gutenberg notice; the [license](https://www.gutenberg.org/policy/license) is
US-jurisdiction-specific and these records are not a release-rights finding.

| Stage | Frozen artifact | Observed result |
| --- | --- | --- |
| Catalog plan | `data/sources/p114_book_scale_plan_v1/plan.json` (`68f55549…`) | 550 planned candidates across 11 catalog subject classes; 529 after P113 author exclusion |
| Mirror attempts | `data/sources/p114_book_attempts_v1/download_manifest.json` (`77d33f94…`) | 160 attempts: 158 complete raw texts, one HTTP 404, one over-cap prefix |
| Structural freeze | `data/sources/p114_book_cohort_v2/manifest.json` (`018d4952…`) | 58 new selected works, 53 author components, 46 train / 12 eval; ten actual source topics |
| Native task build | `data/candidates/p114_book_native_v2/manifest.json` (`9ea46b7b…`) | 129 distinct tasks from 39 productive works, 105 train / 24 eval; 19 frozen works produced zero tasks |
| Final reader audit | `data/candidates/p114_book_native_v2/independent_audit_manifest.json` (`7a616ab5…`) | 129/129 full raw/body, reader, gold, intervention, span and assistant-mask checks |
| Unified / mask | `data/candidates/p114_book_unified_v2/manifest.json` (`bae310fb…`), `data/candidates/p114_book_unified_mask_v2/manifest.json` (`5fd8532b…`) | 129 unified readers; 129/129 final mask replay |
| Overlap receipt | `data/candidates/p114_book_scale_compare_v2.json` (`ca360f01…`) | Zero exact source group, task ID, raw SHA, catalog work key or author-key overlap with the historical P113 v4 source; independent review also checked the new P113 v5 work/source disjointness |

The plan and downloads are source intake, **not** 550 worlds or 160 tasks.
Source freeze rejected 65 insufficient chapter headings, ten missing title or
author headers, seven insufficient body chapters, six without enough shared
cross-chapter named speech, five short bodies, four repeated chapter numbers,
two missing first chapters, one short/TOC chapter, one HTTP 404 and one
over-byte-cap prefix. Per-attempt reasons and SHA pins are in
`data/sources/p114_book_cohort_v2/attempt_ledger.jsonl`.

The 129 final task lengths are eight below 32K, 32 at 32–64K, 66 at
64–128K and 23 at 128–256K final-chat tokens. They contain 11,356,001
final-chat tokens and 1,190 assistant-supervised tokens. The executed
source-to-answer token span is 16,615–181,807 (median 71,437). Task topics:
romance 27, western 22, detective 20, historical 19, social 16, sea 8,
fantasy 7, juvenile 6, adventure 4. The one science-fiction frozen book and
all 19 zero-task books are counted as source capacity, not task coverage.
In the new compiler, the broad veto rejected 929 *chapter-pair/label
proposals*; these are not 929 unique questions. Compared with the old
quarantined P114 134-row native set, 99 sample IDs remain, 35 were removed
and 30 new candidates fill later positions. No quantity quota overrode a
truth or mask gate.

Independent adversarial review of all 129 new target chapters found no later
direct same-name attribution under a wider scan allowing 0–8 modifiers and
tag line breaks. It checked all 129 reader chapters against frozen body bytes,
unique anchors/answers, question leaks, source hashes, author-component split
and P113 work/body overlap. A separate final pass independently re-tokenized
all 129 unified readers and confirmed full/input/supervised counts, assistant
tail masks, SHA pins and split receipts with zero mismatches. This supports
candidate-index integration while leaving `train_ready=false`.

## Reproduction

The config is `configs/p114_book_scale_v1.json`; `plan`, `download`, `freeze`
and `compare` are idempotent phases of `scripts/p114_book_scale.py`.
`download --verify-only` checks frozen raw bytes without new HTTP traffic.
The 160-source acquisition used the official mirror only; remaining commands
replay from local frozen inputs.

```bash
cat data/sources/p114_book_cohort_v2/manifest.json
less -R data/sources/p114_book_cohort_v2/attempt_ledger.jsonl
less -R data/candidates/p114_book_native_v2/proofs.jsonl
cat data/candidates/p114_book_unified_mask_v2/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/p114_book_scale.py --config configs/p114_book_scale_v1.json --phase plan --catalog data/sources/p113_book_catalog_v1/pg_catalog.csv.gz --output data/sources/p114_book_scale_plan_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p114_book_scale.py --config configs/p114_book_scale_v1.json --phase download --plan-dir data/sources/p114_book_scale_plan_v1 --output data/sources/p114_book_attempts_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p114_book_scale.py --config configs/p114_book_scale_v1.json --phase freeze --plan-dir data/sources/p114_book_scale_plan_v1 --download-dir data/sources/p114_book_attempts_v1 --output data/sources/p114_book_cohort_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_tasks.py --source-dir data/sources/p114_book_cohort_v2 --output data/candidates/p114_book_native_v2 --workers 4 --max-tasks-per-book 4 --min-lineage-tokens 16384 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_audit.py --source-dir data/sources/p114_book_cohort_v2 --native-dir data/candidates/p114_book_native_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_to_unified.py --source-dir data/sources/p114_book_cohort_v2 --native-dir data/candidates/p114_book_native_v2 --output data/candidates/p114_book_unified_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_book_unified_mask.py --unified-dir data/candidates/p114_book_unified_v2 --output data/candidates/p114_book_unified_mask_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p114_book_scale.py --config configs/p114_book_scale_v1.json --phase compare --source-dir data/sources/p114_book_cohort_v2 --unified-dir data/candidates/p114_book_unified_v2 --output data/candidates/p114_book_scale_compare_v2.json --verify-only
```
