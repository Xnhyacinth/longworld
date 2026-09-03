# P30 IETF OAuth chunk-local counterfactual projection (2026-09-03)

## Result

The IETF counterfactual projector now applies the authenticated RFC 9700
`bearer_current` exclusion to the unique artifact whose bound source character
span fully contains that evidence. It verifies the original whole-RFC twin
character span, byte span, parent hash, child hash, and source hash before
translating the operation to chunk-local character and byte offsets. It then
verifies the parent artifact equals the corresponding authenticated manifest
slice and binds the projected chunk's child hash. Zero or multiple containing
chunks are rejected.

P29 projection consequently emitted all six exact views. The pinned dense
ranker emitted one signed ranking for each view. The single permitted dense
audit stopped on its next unchanged gate, before emitting any audit row:

```text
PromotionError: task upstream proof replay failed: task proof gates failed: remove_one_fails
```

The failure is isolated to the 128K CF row, which is audited first. The changed
RFC 9700 artifact
`ietf:rfc:9700:chars:0041908-0046297:b04658418eda` contains only
`bearer_current`. Its authenticated source span is `[41908,46297)`, the absolute
operation span is `[46008,46124)`, and the chunk-local operation span is
`[4100,4216)`. Removing that artifact leaves the CF bearer result unchanged
because RFC 6750 baseline evidence still resolves it to
`URI_QUERY_DISCOURAGED_OR_CONDITIONAL`. The same artifact is the sole
remove-one no-op in the 64K CF row; all full and ordered rows have no such
remove-one no-op.

No gate was changed. No audit row or audit manifest was written, and nothing was
selected, promoted, counted, committed, or marked train-ready.

## RED/GREEN

- RED: `uv run pytest -q tests/test_ietf_cross_spec_projection.py -k
  projects_counterfactual_into_unique_source_span_chunk` reproduced
  `PromotionError: IETF task counterfactual parent bytes are invalid` and
  returned `1 failed, 6 deselected`.
- GREEN: after the source-span-aware projector change, the same command returned
  `1 passed, 6 deselected`.
- Projection focused file: `7 passed in 1.09s`.
- Focused P20-P30 regression: `53 passed in 3.73s` across standards workflow,
  source workflow, requirement resolver, sidecar, projection, and dense audit.

## Projection and ranking distribution

| Bucket | View | Prompt tokens | Artifacts | Essential | Strict supports | Remove-one no-op |
|---|---|---:|---:|---:|---:|---:|
| 64K | full | 64,529 | 50 | 11 | 6 | 0 |
| 64K | cf | 64,498 | 50 | 11 | 6 | 1 |
| 64K | ordered | 64,529 | 50 | 11 | 6 | 0 |
| 128K | full | 128,520 | 58 | 11 | 6 | 0 |
| 128K | cf | 128,489 | 58 | 11 | 6 | 1 |
| 128K | ordered | 128,520 | 58 | 11 | 6 | 0 |

Projection manifest: 2 parents, 6 projections, views `full`, `cf`, and
`ordered_artifact_view`; `train_ready=false`, `production_eligible=false`, and
`dense_audit_complete=false`. Dense ranking completed 6/6. Dense audit produced
0/6 rows.

## Candidate-local hashes

- projected candidates:
  `5659be3e8cf049b9004122e4e0db2c629936744c4fb40bb623050f0b57d67608`
- v3 task sidecar:
  `19d782317877cf991992f94b5dd5cd1258e3e6073c89e05e750a7361e3ffa826`
- dense rankings:
  `257ef773f37ba51039add5672205f9195f971a73cb2dae2d3d54b6491b0d13ef`

Generated files remain candidate-local under
`data/candidates/p29_ietf_oauth_chunked_v1/` and are ignored by Git.
