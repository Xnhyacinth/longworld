# P29 IETF OAuth span-bound chunks (2026-09-03)

## Result

Selected-artifact replay now grants an IETF evidence item only when one selected
artifact's authenticated source character span fully covers that evidence span.
The unchanged raw-window gate therefore no longer treats an arbitrary chunk from
the same RFC as access to the whole RFC.

Two independent P29 parents were then generated in the exact 64K and 128K bands.
RFC 6749 and RFC 9700 are represented by unique, nonoverlapping source chunks
whose boundaries are existing blank lines or form feeds. All 12 evidence spans
are covered exactly once and none is cut. No padding, source cloning, synthetic
filler, or chronology reordering was used.

The public projection call stopped before emitting any view because the existing
IETF counterfactual projector still indexes the RFC 9700 evidence offset against
an entire-record artifact:

```text
PromotionError: IETF task counterfactual parent bytes are invalid
```

The P29 artifact containing `bearer_current` is a bound source slice rather than
the full RFC, so the projector must translate the authenticated absolute source
offset to that artifact's local offset. This was not changed in P29 because the
assigned second behavior limited implementation to the P29 generator/config and
required stopping at the next interface blocker. Dense ranking and dense audit
were not invoked. No projection rows, audit rows, selection, promotion, counting,
or commit occurred.

## Span replay RED/GREEN

- RED: `uv run pytest -q tests/test_ietf_cross_spec_projection.py -k
  p28_partial_window_does_not_grant_whole_ietf_record` returned `1 failed`.
  Selecting only RFC 9700 `[0,8)` still returned the complete factual answer.
- GREEN: after binding selected artifacts to their classification
  `source_record_id/source_char_start/source_char_end`, the same command returned
  `1 passed, 5 deselected`.
- Focused P20-P29 regression: `52 passed in 2.37s` across standards workflow,
  source workflow, requirement resolver, sidecar, projection, and dense-audit
  tests.
- Ruff: changed Python files pass `ruff check`; the generator and focused test
  pass `ruff format --check`. `git diff --check` passes.

## Exact parent distribution

| Bucket | Prompt tokens | Artifacts | Essential | RFC 6749 chunks | RFC 9700 chunks |
|---|---:|---:|---:|---:|---:|
| 64K | 64,529 | 50 | 11 | 37 | 4 |
| 128K | 128,520 | 58 | 11 | 37 | 4 |

For each parent, all artifact byte hashes are unique, document bytes equal the
declared source slice, the selected RFC 6749/RFC 9700 spans do not overlap, and
all 12 task evidence spans have exactly one complete covering artifact. The 11
essential artifacts are expected because more than one RFC 9700 evidence span
can reside in the same natural chunk.

The first 512-token chunk target was rejected before candidate materialization
because an indivisible natural unit exceeded the target. Exact measurement with
the pinned task tokenizer found maxima of 713 tokens for RFC 6749 and 1,058 for
RFC 9700; P29 therefore uses 1,058 as the smallest valid natural-unit cap.

## Candidate-local hashes

- config: `0302127ca136b44ac280fbaf03f907fe1606f139615b41cbcfe74d0341561d9c`
- generation receipt: `7c9427a4a0d0c928c2806bd499352fac534c46474359c20a51335ea1c6d87e6a`
- parents: `9ce53c55a7a4349c4e0da5b2533bfcf6a6509184ac9bb07cfca255b840862e2f`
- v1 sidecar: `7ec938b2f2eb5c468f15d50bb7e68ff21ec92829158c6ed9847c7e69d545a3db`

Candidate-local generated files are under
`data/candidates/p29_ietf_oauth_chunked_v1/` and remain ignored by Git.
