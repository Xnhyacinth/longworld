# P56 mixed-disposition GovInfo successor

## Outcome

The bounded three-task batch produced two surviving three-band parent sets and
rejected one task on an actual raw window. It removes the measured all-M output
shortcut from these candidate tasks. It does not establish model accuracy,
exclude every possible label/position heuristic, or add train-ready inventory.

| Frozen task | Schedule | CF request | 32K full | 64K full | 128K full | Outcome |
| --- | --- | --- | ---: | ---: | ---: | --- |
| mixed-01, seed 2026090601 | 4→8→16 | D02 | 32,326 | 64,376 | 128,622 | Three signed parents; shared full audit pending |
| mixed-02, seed 2026090602 | 4→8→16 | D01 | prefix only | — | — | Ordered 16K raw window `0:16384` answers the task; rejected |
| mixed-03, seed 2026090603 | 6→12→24 | D06 | 32,121 | 64,092 | 128,319 | Three signed parents; shared full audit pending |

Both surviving sets have nine complete shared projections, including full, CF
and chronological views. Every view remains in its unchanged exact band. The
largest CF adjustment is -218 tokens for mixed-01; mixed-03's CF adds 15 tokens.
No world ID, section boundary, background ordering or token threshold was tuned
after observing the window results.

## Source and task selection

The same frozen H.R.4366 EAS→EAH transition exposes 124 unambiguous retained
keys and 108 modified keys under the existing five-word shingle/Jaccard oracle.
All 232 eligible keys and all three complete request lists were written to
`FROZEN_MIXED_TRIALS.json` before any pack or raw-window evaluation.

Selection starts from the two lexical source-key pools, shuffles them with each
predeclared seed, and constructs shuffled blocks containing one R plus three M
or two R plus four M. The three requested prefixes are nested unions of those
blocks. The CF anchor is selected from the first block's actual M requests by
the same seeded RNG, then given the code at its real position. It is no longer
hard-coded to D02. Artifact hashes, artifact IDs, evidence positions and prior
window outcomes are not selection inputs.

`requested_disposition_policy=mixed_retained_modified` is an explicit new
configuration option. The old default remains `modified_only`. The new policy
requires both factual classes in every generated bucket and rejects an R
counterfactual anchor. This is a task-admission check; the shared source,
near-duplicate, derived-view, exact-band and raw-window contracts are unchanged.
The old default entry serialization has a fixed byte-hash regression.

## Duplicates remain duplicates

R endpoints are not exempt from duplicate checks. Each selected pair has frozen
whole-section text hashes, exact and canonical equality indicators, and both
whole-section and oracle five-shingle similarities. The counts below cover all
requested keys in each task's largest prefix, not separate candidate capacity:

| Task | Authenticated endpoints | Exact unique section bodies | Tokens counting endpoints | Tokens after exact body collapse |
| --- | ---: | ---: | ---: | ---: |
| mixed-01 | 32 | 28 | 5,149 | 4,593 |
| mixed-02 | 32 | 28 | 4,627 | 3,836 |
| mixed-03 | 48 | 40 | 5,051 | 4,403 |

These are individually tokenized normalized whole-section derivatives, not raw
XML and not serialized context/proof token counts. Repeated endpoints are never
claimed as new unique text. No duplicate threshold was relaxed. The surviving
parents' existing sentence-duplicate ratios are 0.0251–0.0369, below the
unchanged 0.25 ceiling; later shared duplicate/source gates remain required.

The shared proof-token calculation, which includes the declared essential
artifact serialization and separators, grows 6,369→9,403→16,130 for mixed-01
and 5,187→9,034→19,127 for mixed-03. Both schedules pass the existing substantial
proof-growth precheck in all three views. These proof counts are deliberately
not presented as unique source-text capacity.

## Constant baselines

On the 18 projected candidate rows from the two surviving schedules:

| Baseline | Exact answers | Correct labels |
| --- | ---: | ---: |
| Always M | 0 / 18 | 141 / 210 (67.14%) |
| Always R | 0 / 18 | 69 / 210 (32.86%) |
| Fixed D02=R, every other code=M | 0 / 18 | 129 / 210 (61.43%) |

The factual majority-label rates are still 75% and 66.7%. This is a measured
improvement over the original factual all-M shortcut, not a claim of balanced
classes or comprehensive shortcut robustness. These are exact programmatic
baselines, not an LLM evaluation. Full/CF/ordered variants and the two schedules
are correlated views/tasks from one bill and one oracle, not independent worlds.
They must stay in the same source-connected split and cannot serve as unseen-world
evaluation against each other.

Independent content review additionally searched all 126 binary positional
patterns of periods 1–6 **after observing this batch**. The best pattern,
`MMMRRM` repeated by request index, gets **4/18 exact answers and 153/210 labels
(72.86%)**. Those exact hits are 32K factual/ordered rows; the first four factual
labels of all three frozen tasks happen to be `MMMR`. This is residual small-
sample position bias, not a held-out score or proof that all templates fail.
The post-hoc search is reproduced separately in the baseline ledger. No seed,
request list or signed row was changed in response to this finding.

## Artifacts and verification

- Plan: `configs/p56_govinfo_mixed_dispositions_20260906.json`.
- Runner: `reports/p56_govinfo_mixed_dispositions_20260906.py`.
- Frozen source-key lists, per-pair duplicate receipts, raw results and growth
  measurements: `data/candidates/p56_govinfo_mixed_dispositions_20260906/`.
- Shared handoff: `{mixed-01,mixed-03}/shared_views/candidates.jsonl`, with sidecars
  and replay registries in those directories.
- Full-row baseline ledger:
  `reports/p56_govinfo_mixed_dispositions_constant_baselines_20260906.json`.

There are six final parents and 18 full projections. The three prefix parents
and nine prefix projections are diagnostics and must not be added as independent
training examples. The raw results here cover 32K only and are unsigned direct
shared-function diagnostics; full 64K/128K raw, dense, selection, release quality,
promotion and B5 are separate downstream gates.

The focused regression passes 29 unique tests: the existing 24 P52/adapter tests
plus five new policy, compatibility, deterministic selection and baseline tests.
Ruff, formatting and `git diff --check` pass. No shared core, existing signed
P52 row, raw-window algorithm, packer or duplicate threshold changed.

Recompute the baseline ledger without refetching sources or rerunning raw audits:

```bash
uv run python reports/p56_govinfo_mixed_dispositions_20260906.py \
  --plan configs/p56_govinfo_mixed_dispositions_20260906.json \
  --output-dir data/candidates/p56_govinfo_mixed_dispositions_20260906 \
  --summarize-existing
```

Fresh generation uses that runner without `--summarize-existing`, a fresh output
directory and the existing local-probe source/candidate wrapper, pinned offline
Qwen tokenizer, and two CPU threads. It fetches the hash-pinned official XML
into process memory; no raw XML is persisted. No GPU, training, upload or Git
commit was performed by this track.
