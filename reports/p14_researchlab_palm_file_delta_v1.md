# P14 ResearchLab PaLM file-delta closeout

## Outcome

PaLM (`arXiv:2204.02311`, v1--v5) produced five of the required nine
candidate cells and is closed without promotion. The candidate is not a
complete 9/9 world and must not count toward ResearchLab quota.

The run did not use padding, copied text, unchanged files, relaxed exact-token
bands, relaxed near-duplicate checks, or relaxed derived-view/truncation gates.

## Authentic dependency program

The source contract now exports `paper-revision-file-delta-v1` receipts for
macro-heavy multi-file papers. Every receipt binds the adjacent `revision_of`
edge, source and target record/revision identities, path and basename, both
complete file-payload SHA-256 values, and a nonempty exact changed-text span
and SHA-256. Byte-identical, whitespace-only, macro-only, and whitespace-
normalized text already present in the prior revision are excluded.

The task replays complete source/target file views and every required adjacent
revision edge. Its answer reconstructs the changed basename and content, not
only a filename or hash. In particular, the 16K answer requires the v3→v4
addition of `training-longer.tex` and its paragraph about continued training
to 1.325 trillion tokens, together with the v4→v5 `evals-gpt3.tex` change.
Removing any source, relation, or decision artifact makes the answer unknown;
the counterfactual changes an exact digit inside the signed changed span and
changes the replayed answer.

## Capacity after fail-closed filtering

Exact counts use local `Qwen/Qwen3.5-4B` revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`.

| Adjacent edge | Substantive receipts | Complete file-pair tokens |
| --- | ---: | ---: |
| v1→v2 | 9 | 47,646 |
| v2→v3 | 6 | 35,148 |
| v3→v4 | 10 | 55,306 |
| v4→v5 | 1 | 12,908 |

The nested selected stages are:

| Stage | Receipts | Complete selected-file tokens |
| --- | ---: | ---: |
| 16K | 2 | 15,742 |
| 32K | 6 | 31,218 |
| 64K | 10 | 63,053 |

The 32K stage adds four v2→v3 substantive files to the 16K stage. The 64K
stage adds four v1→v2 files: `evals-code.tex`, `evals-gpt3.tex`,
`evals-mt.tex`, and `exploring-explanations.tex`.

## Exact candidate matrix

Candidate:
`data/releases/p14-researchlab-palm-file-delta-v1-candidate/`.

| Length | View | Exact prompt tokens | Result |
| --- | --- | ---: | --- |
| 16K | full | 16,194 | pass |
| 16K | cf | 16,194 | pass |
| 16K | ordered_artifact_view | 16,194 | omitted: byte/order-identical to full |
| 32K | full | 31,979 | reject: `exact_32k_out_of_range` |
| 32K | cf | 31,979 | not emitted after factual exact-band reject |
| 32K | ordered_artifact_view | 31,979 | not emitted after factual exact-band reject |
| 64K | full | 64,155 | pass |
| 64K | cf | 64,155 | pass |
| 64K | ordered_artifact_view | 64,155 | pass |

The signed quality report records `n_rows=5`, `n_rejects=1`,
`gold_cfr=1.0`, no exact duplicate rows, and mean real-source token ratio
`0.993`.

The only permitted 32K repair was to add one complete substantive receipt
already present in 64K. Offline exact increments prove that none fits the
unchanged band:

| Added receipt | Increment | Resulting prompt lower bound |
| --- | ---: | ---: |
| `evals-code.tex` | 12,001 | 43,980 |
| `evals-gpt3.tex` | 11,670 | 43,649 |
| `evals-mt.tex` | 6,525 | 38,504 |
| `exploring-explanations.tex` | 1,639 | 33,618 |

The smallest authentic addition still exceeds the 32K upper bound, so no
second generation was run. The 16K ordered view also correctly remains absent
under the unchanged derived-view gate.

## Signed artifacts

- Manifest:
  `data/source_inventory/p14_paper_palm_revision_preflight_v1/paper_workflow_manifest.p14.file-delta.signed.json`
  (`sha256=eb48dc0987f3765d819312a10887ed463d98148757e70051c8105bdf6ef8b8d4`)
- Bundle:
  `data/source_inventory/p14_paper_palm_revision_preflight_v1/paper_source_workflow_bundle.p14.file-delta.signed.json`
  (`sha256=d7607e6445a8ee01f8cc61924a0142503ce4da33be2b56f8cf8378b337ae5fc8`)
- Config: `configs/p14_researchlab_palm_file_delta_v1.yaml`
- Quality report SHA-256:
  `7bca5f89dbad7bfa37474b7e4b9aad687fd67cd531bd31cd96e04b74dbb42246`

## Verification and reproduction

Dedicated exporter and ResearchLab regression:

```bash
uv run pytest -q tests/test_paper_workflow_export.py \
  tests/test_researchlab_source_workflow.py
uv run ruff check longworld/core/documentworkflow.py \
  longworld/core/sourceworkflow.py \
  longworld/domains/researchlab/simulate.py \
  longworld/domains/researchlab/events.py \
  longworld/domains/researchlab/queries.py \
  longworld/domains/researchlab/render.py \
  tests/test_paper_workflow_export.py \
  tests/test_researchlab_source_workflow.py
```

Final observed result: `48 passed in 4.21s`; Ruff reported
`All checks passed!`. The exporter case includes the whitespace-move
regression.

Candidate generation used the existing local-probe role wrapper:

```bash
uv run python scripts/run_with_local_probe_trust.py \
  --trust-file /workspace/wynckeliao/.longworld-agent-probe/p12-probe-12-v2/local_probe_trust.json \
  --role source --role candidate --role report --allow-combined-roles -- \
  .venv/bin/python scripts/generate.py \
  --config configs/p14_researchlab_palm_file_delta_v1.yaml \
  --seed-start 142204023 \
  --out-dir data/releases/p14-researchlab-palm-file-delta-v1-candidate \
  --workers 1
```

No dense or production promotion audit was run because the candidate is not
9/9. The general file-delta source/task adapter remains uncommitted for
evaluation against a different paper entity with a naturally admissible nested
stage plan.
