# P113 legal-cell native dispatch: bounded Wiki and report cohorts

**Current novelty comparison:** `data/candidates/p113_legal_cell_recompare_v1/manifest.json` supersedes the index comparison embedded in the native release below. The release used `p113_candidate_refs_v1`, later quarantined after five book gold errors. The new receipt pins `p113_book_quarantined_refs_v1`, verifies the unchanged native release and its old lineage, then compares task IDs and answer hashes again without rerunning compilers. It confirms the same net **one Amazon task / two views**. Neither receipt promotes it to training: comparative-text alternatives remain unchecked.

`scripts/p113_legal_cell_dispatch.py` executes two source cohorts selected from the P113 legal-cell plan through existing native compilers. It does not reinterpret each preflight job as one QA: the selected Wiki lookup cell maps to multiple table-cell tasks, and the selected Amazon source maps four selector/baseline preflight cells to the native report-route program matrix. Every accepted sample retains its native semantic task ID and its P113 preflight cell ID in `lineage.jsonl`.

Frozen result: `data/candidates/p113_legal_cell_execution_v2_release/`. It pins the P113 planned-job JSONL, P76 Wiki source pool, P96/P112 finance inputs, and the frozen `p113_candidate_refs_v1` index (`sha256:7f00ce2ee1c14a016d052bca15c1c6e953a7aacfddde585a40264b753afb3a3c`). `--verify-only` re-executes both native producers into a persistent temporary directory and byte-compares their output, then replays the independent P112 answer/reader/mask audit and unified conversion. The v1 preflight artifact remains unchanged. Earlier `p113_legal_cell_execution_v2`, `_v2_current` and `_v2_final` were diagnostic runs; use `_v2_release` for these figures.

| Stage | Wiki bridge lookup | Amazon report route | Combined |
| --- | ---: | ---: | ---: |
| Native selected tasks | 2 | 12 | 14 |
| Native reader views | 2 | 24 | 26 |
| Native rejects | 1 | 68 of 80 program cells | separate denominators |
| P113 post-native quality rejects | 1 | 0 | 1 |
| Post-quality task/view count | 1 / 1 | 12 / 24 | 13 / 25 |
| Existing task+answer-hash overlap views | 1 | 22 | 23 |
| Net distinct tasks / views vs frozen P113 index | 0 / 0 | 1 / 2 | 1 / 2 |

The Wiki clean row asks which `Name` in *List of longest bridges* has `Road and rail` in the `Traffic` column. Its answer, `The Padma Multipurpose Bridge`, occurs once in the reader and is absent from the page title; the final reader has 106,488 chat tokens and 9 supervised tokens. This is a bounded table-cell lookup. Its source audit says `reader_text_necessity_unchecked`, so it is not evidence of a long multi-document dependency. It is also an exact pre-existing semantic task and answer hash. The other native Wiki row was rejected because its answer contains the collapsed HTML text `BridgebrNantong`; retaining it would teach a malformed value.

The Amazon native matrix checked 80 program cells: 12 selected, 6 rejected by balance cap, 38 by single-report/answer shortcuts, and 24 by unsupported source shape. The 12 selected semantic tasks yield 24 reader views across 64K and 128K bins. Independent final-reader audit passed 24/24 assistant-only masks (3,052,456 input tokens and 1,092 supervised tokens), answer replay, 24 selector edits, 24 target edits, and 48 bounded target-support deletions. The single net-new task is `task:e8fc741663e2ec46051b96cfdc7ede14c131b21abe94f258f181a0993867453d`, with two source variants and identical answer hash `b28ed955fa43e9ae586ac1fa55944ddfba500887e76dba3bd45f4ba446104935`. Its chat lengths are 101,064 and 153,397 tokens; each has 45 supervised tokens. As in P112, alternate support in comparative filing text has **not** been exhausted. It remains a research candidate, not a train-ready sample or an admitted addition to the frozen P113 index.

Reproduce the exact verification and inspect cases:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p113_legal_cell_recompare.py \
  --config configs/p113_legal_cell_recompare_v1.json \
  --output data/candidates/p113_legal_cell_recompare_v1/manifest.json --verify-only
cat data/candidates/p113_legal_cell_recompare_v1/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/p113_legal_cell_dispatch.py \
  --config configs/p113_legal_cell_dispatch_v2.json \
  --output data/candidates/p113_legal_cell_execution_v2_release --verify-only
cat data/candidates/p113_legal_cell_execution_v2_release/manifest.json
less -R data/candidates/p113_legal_cell_execution_v2_release/lineage.jsonl
cat data/candidates/p113_legal_cell_execution_v2_release/finance_final_audit.json
```

The data-flow result is concrete but narrow: source-shape scheduling successfully reached two native compilers and produced verified reader bytes, while exact novelty accounting leaves only one new candidate task. Scaling task count requires source/semantic expansion beyond rerunning existing source matrices; presentation variants and duplicate tasks cannot supply that expansion.
