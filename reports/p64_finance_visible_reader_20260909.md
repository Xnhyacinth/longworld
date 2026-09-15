# Gold-blind latest-filing cash-flow reader

A deterministic reader reproduced **518 of 1,682 finance answers (30.8%)** using only the public question and visible text from the latest required filing. It made 518 predictions and all 518 matched the canonical structured answer. The remaining **1,164 tasks are unsupported**, not measured failures of a language model.

The reader API is `read_latest_filing(question, visible_text)` in `longworld/core/finance_visible_reader.py`. No gold amount, answer-cell offset, fact ID, source XBRL attribute or hidden table metadata crosses that API. It locates the visible Q4 cash-flow statement title, validates the visible USD-million and annual-period headers, parses calendar-date columns and metric-labelled signed integer cells, then executes the operation stated in the question. Oracle answers are accessed only after a prediction is fixed.

Supported outputs in this run were 95 lookup, 100 delta, 166 aggregate, 48 maximum, 70 filter, 20 filtered-aggregate and 19 cash-reconciliation answers. Coverage came from Alphabet (89), Amazon (119), Meta (116), Micron (103) and NVIDIA (91). These are five existing Q4-format training issuers; this run does not demonstrate transfer to the two eval issuers.

The 518 predictions also reproduced exactly when rerun on the reader's **label-selected contiguous cash-flow table prefix**. Crops were selected by visible title/metric labels, not gold answer locations. Exact pinned-Qwen counts were **851–1,125 source tokens**, with **at most 1,485 tokens for table plus question**. The shorter rerun independently retokenized each raw substring and invoked the same two-input reader again. It did not pass a pre-parsed table or previous numeric answers to the reader.

Of the 518 matches:

- **170 are single-filing tasks.** The required filing itself contains this local readable table. Long context is not necessary for this deterministic reader to reproduce their answers.
- **348 are multi-filing tasks.** The latest filing's comparative columns reproduce their target answers, but the route does not obey the instruction to use amounts as reported in each original filing. They are marked `scope_mismatch_latest_comparatives_not_original_filings`. They demonstrate an operational shortcut under the current answer-only exact-match score, not a complete original-source proof.
- **242 matches were labelled strict candidates by the geometry audit.** This supplies measured answer-reproduction evidence beyond the previous gold-assisted numeric-repeat opportunities. It does not change any corpus or strict flag automatically.

Unsupported reasons are counted before inspecting gold answers:

| Reason | Rows |
| --- | ---: |
| Non-cash-flow metric | 668 |
| No supported visible Q4 cash-flow layout | 372 |
| Requested year absent from the latest comparative table | 115 |
| Unsupported question operation | 7 |
| Requested cash-flow metric absent | 2 |

AMD, Intel and SiriusXM use inline filing layouts outside this parser's supported Q4 title/year-column contract; all their tasks are unsupported here. Ratio and margin tasks need an income-statement/revenue parser, while dependent selection instructions are not implemented. The baseline stops at these explicit boundaries. It never falls back to raw XBRL attributes, privileged fact tables, guessed years, or gold-based value search.

No neural model, GPU, question-only baseline, complete alternative-proof search or release certification was used. Matching an answer does not verify its stated reporting basis. The fact that all supported predictions matched should be read with the limited coverage and shared source/table structure above.

Artifacts:

- `p64_finance_visible_reader_v1/summary.json`: full coverage, failure-to-support reasons, family/issuer counts and code/input hashes.
- `p64_finance_visible_reader_v1/samples.jsonl`: immutable prediction, post-prediction exact-match score, input hash, visible table witnesses and scope status for every task.
- `p64_finance_visible_reader_v1/local_table_replay.json`: all 518 compact raw-table reruns, exact token counts and tokenizer asset binding.
- `run_p64_visible_finance_reader.py` and `replay_p64_visible_finance_table_windows.py`: reproducible audit drivers.

Four focused tests passed: signed numbers and chronological subtraction, missing year/unit refusal, conflicting-row refusal, and changed visible values changing the prediction. Ruff passed. Existing frozen modules, corpus rows, training files and strict labels were left unchanged.
