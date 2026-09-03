# P25 IETF OAuth redirect-only counterfactual audit (2026-09-03)

## Result

The RFC 9700 counterfactual now excludes only the authenticated
`redirect_current` evidence span. The child is produced by replacing exactly
that span with equal-length spaces; it invents no standards text. The twin binds
the evidence ID, record ID, character and byte offsets, parent/child text hashes,
parent source hash, and source-manifest hash.

The materialized answer changes only `redirect_match` to `UNKNOWN`; the other
five fields retain their factual results. Consequently both CF projections now
replay `strict_support_event_count=5`, without changing the existing complexity
gate.

The single permitted dense-audit run stopped on the next unchanged gate:

```text
PromotionError: task upstream proof replay failed: task proof gates failed: remove_one_fails
```

The first audit row is the 128K CF view. Because its redirect result is already
`UNKNOWN`, removing the factual essential RFC 6749 redirect-baseline artifact
does not change that CF answer. The inherited factual essential set therefore
is not remove-one necessary for this derived CF view. The gate was not relaxed;
0 audit rows and no audit manifest were written.

## RED/GREEN

1. Redirect-only materialization RED:
   `uv run pytest -q tests/test_ietf_cross_spec_projection.py -k
   materializes_byte_bound_rfc9700_redirect_requirement_exclusion` failed with
   missing `counterfactual_twin.evidence_id`. After the minimal materializer
   change, the same command passed `1 passed, 4 deselected`.
2. CF replay RED:
   `uv run pytest -q tests/test_ietf_cross_spec_projection.py -k
   projects_source_bound_ietf_full_cf_and_ordered_views` failed with `task view
   source replay does not match answers`. After excluding only the twin-bound
   evidence ID during CF replay, it passed `1 passed, 4 deselected`, including
   the explicit five-support assertion.
3. Focused P20-P25 regression: `51 passed` across standards workflow, source
   workflow, requirement resolver, sidecar, projection, and dense-audit tests.

## Exact projection distribution

| Bucket | View | Prompt tokens | Strict supports | Real-source marginal |
|---|---|---:|---:|---:|
| 64K | full | 64,528 | 6 | 0.9989926853 |
| 64K | cf | 64,481 | 5 | 0.5311176936 |
| 64K | ordered | 64,528 | 6 | 0.9989926853 |
| 128K | full | 128,541 | 6 | 0.9994943248 |
| 128K | cf | 128,494 | 5 | 0.7647049668 |
| 128K | ordered | 128,541 | 6 | 0.9994943248 |

Natural parent packing is unchanged: 64K has 10 artifacts/8 essential and 128K
has 17 artifacts/8 essential. No filler, padding, cloned artifact, selection,
promotion, counting, or commit was performed.

## Candidate-local hashes

- configuration:
  `c8918d7272e0609f85f9e53495625bd01385c3144453f13e31a291af4e8c29b1`
- parent candidates:
  `89a6d2845d700483b62bc0394f2dd21983448edf9458b192300d60343d9faf92`
- v1 sidecar:
  `4f62bcde06048c44d19f73ca1ed79ee527cdb0b528ce7c549e59457be17750f3`
- projected candidates:
  `38d024940a1578daab1cf704c416e6d631e9499393402102c404dbe9a6f5b2a0`
- v3 sidecar:
  `1b8e657848fecfa86a04eacde42cda3a60b37ccd436315fa687b6db6c41f02b7`
- dense rankings:
  `6fd7d6a5e1caa2b619803140d9d50855d07704f9e0f5e5dcee8343c6cd0fd92a`

Generated candidate-local files are under
`data/candidates/p25_ietf_oauth_redirect_cf_v1/` and remain ignored by Git.
