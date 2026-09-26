# P115 real-source-shape pilot (2026-09-26)

This bounded pilot checked **34 pinned paper source groups** and **40 finance metric × issuer cells**. It produced **three net-new semantic tasks and six reader views**, all from one Amazon source group. These are local research candidates, not a scaled multi-domain dataset or train-ready release. `data/candidates/` is intentionally not tracked by Git; the immutable local native receipt is `data/candidates/p115_real_source_shape_v8/manifest.json` (SHA-256 `9ced4cafcd690fd964c4f6bee0d9e42cf5a7cfd5355748d0f5039984cb775823`). Its isolated unified candidate shard is `data/candidates/p115_real_source_shape_unified_v1/manifest.json` (SHA-256 `5166f97d172f1f0abdada0a87713bc534ac4f86871c8fb57e6cbe767573c89ba`). The code/config/manifest hashes and frozen inputs allow exact local replay.

## Evidence and scope

| Stage | Actual count | Boundary |
| --- | ---: | --- |
| Papers probed | 34 | 29 lack an independently verified typed-table/definition adapter; 5 source parser rejects. Zero paper QAs. |
| Finance cells probed | 40 = 8 issuers × 5 metrics | Four original annual filings per issuer, two final reader views. |
| Unsupported table grammar | 15 | Current table gate expects `$ in Millions`, a `Statements of` heading, first own-year column and same-row value. This **does not mean the filings lack units**. AMD, Intel and SiriusXM use common `(In millions, except per share amounts)`, `Years Ended (In Millions)`, and multiline row-label/year formats. A future *generic table parser* should handle these jointly; this pilot did not loosen the gate per issuer. |
| Short substitute support | 22 | Two-report comparative columns give the four values within <16K final-reader tokens. Their 64K–128K physical input is not long information dependence. |
| Bounded candidates | 3 tasks / 6 views | Amazon operating, investing, financing cash flow; exact prior-index task-ID/answer-hash overlap 0. |
| 32K information-distance gate | 0 | A separate diagnostic `p115_real_source_shape_v5` applied ≥32,768-token minimum exact-numeric support and rejected all 25 otherwise renderable tasks. |

The retained tasks ask for **the complete set of fiscal years above a numeric threshold**, using the cash-flow value in each year's original filing. They read each of four source-visible table rows, including statement title, USD-millions unit, own fiscal-year first column, and signed number. Their answers contain 1–3 years. The renderer uses full source reader context and does not expose the hidden numeric table/program. The source group split is `train` from the pinned P96 receipt; no split was relabeled. One semantic task produces a `complete_statements` and `analyst_packet` view; views are not distinct tasks.

The final-chat sizes are **101,052–101,058** tokens for complete statements and **153,385–153,391** for analyst packets. Supervised assistant tokens are **17–22 per view**. The shortest exact-numeric alternative support spans **25,750–27,656 tokens** and uses **at least two reports**, measured on the final tokenizer/chat template. This is 16–32K bounded numeric dependency, **not four-report necessity**: comparative columns duplicate values. Each required number was removed from *all* exact numeral occurrences in the model-visible context, and the targeted cell became unreadable; a distinct `reader_context_minus_sha256` is recorded for every value. This is bounded exact-numeric intervention, not exhaustive paraphrase/arithmetic support search or a universal shortest proof.

`support_matrix.jsonl` records accepted/rejected denominators and the alternative-support results; `proof.jsonl` records source spans, final prompt token spans, all matching numeric occurrences, per-value reader-minus SHA, token distance, and mask status. `sample_index.jsonl` records split, task ID, answer hash, physical length, and supervised tokens. `novelty.jsonl` compares exact task ID and answer hash with pinned book-free `p113_book_quarantined_refs_v1` and the current `p114_book_scale_refs_v1`; all three are absent from both. The isolated unified shard has `candidate_train.jsonl`, empty `candidate_eval.jsonl`, a normalized `sample_index.jsonl` with native proof pointers, and `mask_audit.jsonl` for all six views. The 3 candidates **must not be merged into the P114 index** merely because this pilot passed bounded checks.

## Reproduce and inspect

```bash
.venv/bin/python -m pytest tests/test_p115_real_source_shape.py -q
.venv/bin/python scripts/p115_real_source_shape.py --config configs/p115_real_source_shape_v2.json --output data/candidates/p115_real_source_shape_v8 --workers 4 --verify-only
.venv/bin/python scripts/p115_real_source_shape.py --config configs/p115_real_source_shape_v2.json --shard-native data/candidates/p115_real_source_shape_v8 --output data/candidates/p115_real_source_shape_unified_v1 --shard-verify-only
sha256sum data/candidates/p115_real_source_shape_v8/manifest.json
sha256sum data/candidates/p115_real_source_shape_unified_v1/manifest.json
cat data/candidates/p115_real_source_shape_v8/novelty.jsonl
less -R data/candidates/p115_real_source_shape_v8/amazon/support_matrix.jsonl
less -R data/candidates/p115_real_source_shape_v8/amazon/proof.jsonl
less -R data/candidates/p115_real_source_shape_v8/amazon/sample_index.jsonl
```

Exact replay of every v8 native and unified-shard file passed. An independent v7 readback re-executed the six answers from final visible cells, recomputed each all-equal-number context-minus SHA, and reran the final assistant-only mask audit; all six passed. The six reader/proof/index rows remain byte-identical in v8. A separate reviewer checked the unified reader bytes against native v8, all six final assistant masks and reader hashes, every exact-number support combination and its final-token minimum, and exact task/sample/answer overlap against all 13,762 P114 reference rows; all checks passed with zero overlap. That P114 index already has 355 Amazon views, so the source has heavy prior exposure. No GPU training was run. The paper unsupported matrix is a capacity result, not a paper-native synthesis result. The v1–v7 local output directories are retained as diagnostics and should not be counted in final candidate totals.

A follow-up read-only probe found that a *generic* table grammar for `In millions` variants, full-month dates, and labels split from the numeric row can structurally read the 15 unsupported cells. It was not promoted to the pinned compiler: their complete-statements contexts expose all four numbers within only **5,218–20,888 characters**, and one SiriusXM cell has all four values in one report. These are not additional 16K-token long-dependency tasks under the same two-view gate. A future table parser should be justified on a source pool with actual long-distance capacity, not on this cohort's apparent row coverage.
