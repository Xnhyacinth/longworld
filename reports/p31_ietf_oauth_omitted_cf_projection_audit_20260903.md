# P31 IETF OAuth omitted counterfactual artifact (2026-09-03)

## Result

When the authenticated exact-span exclusion covers all non-whitespace bytes of
the selected `bearer_current` artifact, the IETF CF projector now omits that
child artifact instead of emitting empty or whitespace-only bytes. Its artifact
ID is also absent from the CF source map, essential set, projection artifact
bindings, parent artifact bindings, chronology, and source-token contribution
receipt. Ordinary partial-span exclusions continue to emit one hash-bound
synthetic counterfactual artifact.

An independent P31 source pack isolated `bearer_current` at its authenticated
source character boundaries. Generation, six-view projection, and six-row dense
ranking completed. The single permitted dense audit stopped before row 1 at the
next fail-closed derivation invariant:

```text
PromotionError: task projection derivation receipt is invalid
```

The v3 validator requires every CF receipt to have
`retained_parent_document_tokens < parent_document_context_tokens`. For the
128K omission CF receipt, both values are `128414`: the omitted artifact and its
token contribution are intentionally absent, while all retained artifacts are
unchanged real-source bytes. Ordinary span-edit CF receipts still have a
synthetic artifact and a strict retained-token decrease. This validator was not
changed in P31 because the task required stopping at the next blocker.

No audit row or manifest was written. No gate was relaxed and nothing was
selected, promoted, counted, committed, or marked train-ready.

## RED/GREEN

- RED: `uv run pytest -q tests/test_ietf_cross_spec_projection.py -k
  omits_fully_excluded_ietf_counterfactual_chunk` returned `1 failed, 7
  deselected`; the whitespace child reached `_task_view_artifacts` and failed
  `task view projection artifact is malformed`.
- GREEN: the same command returned `1 passed, 7 deselected`. The public test
  verifies omission from classifications, source map, essential IDs, projection
  bindings, parent bindings, token contributions, and verifies no blank artifact
  and remove-one replay for the retained essential set.
- Focused P20-P31 regression: `54 passed in 2.75s`.
- Changed Python files pass Ruff; `git diff --check` passes.

## Independent P31 distribution

| Bucket | View | Prompt tokens | Artifacts | Essential | Blank artifacts |
|---|---|---:|---:|---:|---:|
| 64K | full | 64,537 | 50 | 11 | 0 |
| 64K | cf | 64,501 | 49 | 10 | 0 |
| 64K | ordered | 64,537 | 50 | 11 | 0 |
| 128K | full | 128,515 | 58 | 11 | 0 |
| 128K | cf | 128,479 | 57 | 10 | 0 |
| 128K | ordered | 128,515 | 58 | 11 | 0 |

All six remain inside their unchanged exact bands. Projection completed 6/6,
dense ranking completed 6/6, and dense audit emitted 0/6 rows.

## Candidate-local hashes

- config: `3bbef3bc7d6b0e1e334de53f7e98a78484f0676e751864599a4b3d56658866a9`
- generation receipt: `d799b50328c5eb417597b6c48209e3cf3c03833e1f6bdd6652cef4ff34201471`
- parents: `6465d07388e6f4885717ecdf61d79d0795688eb4bc1501b75ec70ddc8d515d40`
- v1 sidecar: `fb793c5e57bb0562544582a342237f310ccf4a0c7b697a6c73af4d1f01ea9802`
- projected candidates: `8b2067222bf7d41a24d3d457d67a7877d836e5fdb0440af042f54dd67652ddb9`
- v3 sidecar: `bd0b95196f3c89179dd839792cccc761e0f70012082df48a8e83629efd74eee4`
- dense rankings: `44ba3316799dde2a0753897ef662733d5fe848cf7981a7e35933c5d032b05aac`

Candidate-local files are under
`data/candidates/p31_ietf_oauth_omitted_cf_v1/` and remain ignored by Git.
