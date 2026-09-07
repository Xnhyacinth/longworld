# P52 GovInfo bounded transition and key screen

The new screen examined **21 frozen task combinations across two actual bill
chains**, rejected nine combinations with a concrete 32K artifact-window
counterexample, and emitted **36 signed registered parents** from the remaining
12 combinations. These are candidate parents, not promoted or train-ready rows.
Inventory delta remains zero. Different task keys and views do not create new
independent bill worlds.

The search did not move background, choose keys by artifact hashes or observed
window position, relax gates, split sections, pad context, or modify old signed
rows. It reused the official frozen sources and existing whole-section packer.

## Frozen search and evidence

The plan is `configs/p52_govinfo_geometry_screen_20260906.json`. Before any
pack/window evaluation, the runner wrote all 21 explicit combinations and source
bundle bindings to
`data/candidates/p52_govinfo_geometry_screen_20260906/FROZEN_TRIALS.json`.

Within each transition, it selected three CF anchors by the absolute required-only
shared-materializer token delta, with lexical structural-key tie breaking. It
paired those anchors with lexical key quantiles and appended other lexical keys
to at most six requests. This yielded 12 H.R.4366 combinations and nine H.R.815
combinations, below the fixed limit of 12 per transition.

The current cross-schema oracle found 108 eligible modified pairs for H.R.4366
EAS→EAH and four for H.R.815 EAS→EAH. The latter is four under this oracle, not the
five shown by the older P49 preflight disposition table. The screen uses the
current oracle after excluding numeric presentation identifiers.

H.R.4366 combinations 01, 04, and 05 survived the cheap screen. Its other nine
combinations were rejected at 32K. All nine H.R.815 combinations survived the
cheap geometry screen, but their 128K packs have **no additional necessary
proof events** beyond 64K. They therefore must not be described as successful
three-band proof growth.

## First surviving combinations

Counts below are fully serialized with the existing shared counterfactual
materializer and shared chronological order. These measurements are not a
substitute for shared projection, raw-token windows, dense retrieval, selection,
or promotion.

| Chain / trial | Band | Full exact | CF exact | Ordered exact | Essential artifacts | Essential document tokens |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| H.R.4366 / 01 | 32K | 32,266 | 32,264 | 32,266 | 5 | 1,659 |
| H.R.4366 / 01 | 64K | 64,117 | 64,115 | 64,117 | 9 | 4,185 |
| H.R.4366 / 01 | 128K | 129,240 | 129,238 | 129,240 | 13 | 5,957 |
| H.R.815 / 01 | 32K | 32,188 | 32,363 | 32,188 | 5 | 2,458 |
| H.R.815 / 01 | 64K | 64,815 | 64,990 | 64,815 | 9 | 3,688 |
| H.R.815 / 01 | 128K | 129,674 | 129,849 | 129,674 | 9 | 3,688 |

Essential document tokens are the sum of individually tokenized whole essential
documents; they exclude separators and the question. They measure actual required
text, rather than counting all background tokens as proof. The unsigned
`SCREEN_REPORT.json` records this scope and the necessary-event/token deltas.

The shortest complete artifact proof span in the H.R.4366 / 01 ordered 32K
view is 16,725 exact tokens. Its full and CF spans are 17,978 and 17,976.
Thus this cheap counterexample search does not reproduce the old complete-8K
failure. It does **not** prove that the shared raw-token and intersecting-artifact
window gates pass.

Every screened view also replays after removal of each designated essential
artifact; each removal changes the gold answer. Full and shared materialized CF
answers differ. No standalone shared audit was run by this screening script.

## Implementation and verification

`reports/p52_govinfo_bill_disposition_pipeline.py` now derives the question,
relation IDs, and semantic task IDs from the configured transition. Its original
ENR→Law strings remain unchanged. A regression test reproduced the prior EAS→EAH
question incorrectly naming ENR→Public-Law before the fix.

Registered generation accepts an optional nonempty prefix of 32K, 64K, 128K.
The default remains all three bands; this does not alter any formal promotion
profile. This run generated all three candidate-parent bands for its survivors.

The new runner is
`reports/p52_govinfo_geometry_screen_20260906.py`; its tests cover transition
identity, original wording, nested-prefix construction/rejection, deterministic
bounded key selection, and rejection of background-only proof growth claims.

Verification: 19 focused tests passed across the existing P52 pipeline and
GovInfo adapter tests plus the new screening tests. Ruff and formatting checks
passed. No GPU work was started. Source XML was kept in process memory; old
registered outputs were not read for generation or rewritten.

To reproduce, run the screen into a fresh output directory using the existing
`run_with_local_probe_trust.py` wrapper with source and candidate roles, local
probe trust, and the frozen local tokenizer. Run from the project root with
`uv run`; pass `HF_HOME`, `HF_HUB_OFFLINE=1`,
`TRANSFORMERS_OFFLINE=1`, and `TOKENIZERS_PARALLELISM=false` through the wrapper.
A fresh output path is required; the runner refuses replacement.

The canonical artifact directory is
`data/candidates/p52_govinfo_geometry_screen_20260906/`.
Shared downstream work is owned by the main task and should be reported from its
own receipts, not inferred from this screen.
