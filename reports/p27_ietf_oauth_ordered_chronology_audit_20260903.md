# P27 IETF ordered chronology audit (2026-09-03)

## Diagnosis before change

The third P26 row is the 128K `ordered_artifact_view`. Its 17 artifacts are
independently ordered by authenticated manifest timestamps:

1. RFC 6749 and RFC 6750 (2012-10; three byte-disjoint spans each)
2. RFC 6819 (2013-01; three spans)
3. RFC 7636 (2015-09; three spans)
4. RFC 8414 (2018-06; two spans)
5. RFC 9207 (2022-03; one span)
6. draft-ietf-oauth-security-topics-29 (2024-12; one span)
7. RFC 9700 (2025-01; one complete record)

All ten verified derived-order edges already point from the older prerequisite
to the newer RFC 9700 consumer: three `updates`, four `normative_reference`, two
`informative_reference`, and one `published_as`. Every edge has
`parent_occurred_at < child_occurred_at`. No relation direction was reversed.

The first violation was in the audit adapter: standards artifacts fell into the
generic `json.loads(document)` chronology path, so the first plain-text RFC 6749
artifact could not be parsed and `projection_chronology_valid` became false.

## RED/GREEN

- RED: `uv run pytest -q tests/test_ietf_cross_spec_projection.py -k
  projects_source_bound_ietf_full_cf_and_ordered_views` failed with `task view
  projection failed: projection_chronology_valid`.
- GREEN: the audit now requires each artifact's signed source-record and source
  span binding, looks up `occurred_at` only from the bound task manifest, sorts
  independently by `(occurred_at, record_id, char_start, char_end, artifact_id)`,
  and compares every resulting receipt entry. It does not parse RFC text and
  does not accept current list order as a fallback. The same test passed.
- Focused P20-P27 regression: `51 passed`.

## P26 audit rerun

The only authorized P26 audit rerun passed the prior 128K ordered chronology
failure and continued through the 64K CF and full rows. It stopped on the sixth
row, 64K ordered:

```text
TaskProofError: raw token window 4k intersecting-artifact upper bound retrieves
the gold answer: 30152:34248
```

A read-only localization shows that this 4K token window intersects all eight
essential artifacts: RFC 6749, RFC 6750, RFC 6819, RFC 7636, RFC 8414, RFC 9207,
draft 29, and RFC 9700. The conservative intersecting-artifact upper bound can
therefore replay the gold answer. No raw-window gate was changed or retried.

Because audits are written only after all rows succeed, there are still zero
serialized audit rows and no `AUDIT_MANIFEST.json`. Nothing was selected,
promoted, counted, committed, or marked train-ready.

## P26 exact distribution and hashes

| Bucket | View | Prompt tokens | Strict supports |
|---|---|---:|---:|
| 64K | full | 64,528 | 6 |
| 64K | cf | 64,497 | 6 |
| 64K | ordered | 64,528 | 6 |
| 128K | full | 128,541 | 6 |
| 128K | cf | 128,510 | 6 |
| 128K | ordered | 128,541 | 6 |

- parents:
  `56d506aded8293d24b25371f691e4fb47cf29c50987fcd5a16f1a65fa394bc87`
- v1 sidecar:
  `684e3d4ec65bb3191f5ef1eb46a30d62112d732ae48c3966b0dff6d607373401`
- projected candidates:
  `a34323ec398dabf2f68b0fb6f5d500b6071ee601be9b560a847e246646f85549`
- v3 sidecar:
  `f1536b2d872d0b337189d070b701a48a36fa2f700d7abea99e2a67d2d38c8025`
- dense rankings:
  `cc06ca499ea1da5ac8896af8a5ce26c9b36eb068c8b0307f477b520ff9596198`
