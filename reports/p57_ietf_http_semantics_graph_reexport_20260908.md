# P57 HTTP Semantics graph re-export — RFC header-parser fix

Date: 2026-09-08
Status: signed source graph re-exported. `production_eligible=false`. No
succession v2 pack. No Hugging Face. No Git commit.

## Outcome

RFC 9110 official bytes now compile `obsoletes` including 7538, 7615, and 7694.
Those numbers were previously dropped as author-column continuations, and
`April 2015` / `September 2015` were misread as RFC 2015.

The parser fix was already in `longworld/core/standardsworkflow.py`
(`_strip_rfc_header_right_column`, `_rfc_relation_headers`). This task only
re-compiled and source-signed the existing inventory.

Regression:
`tests/test_standardsworkflow.py::test_rfc_relation_headers_keep_author_column_continuations_and_drop_dates`
passed before export.

## New signed manifest

Path:
`/workspace/wynckeliao/longworld/data/source_inventory/p57_ietf_http_semantics_graph_v1/ietf_workflow_manifest.p57.http-semantics.v2.signed.json`

| Field | Value |
| --- | --- |
| SHA-256 | `5621cfb4ae7bd4ce26dd1180ae15e82fa9ee3dcb1c6fcabc1075c281aa7fb941` |
| `generated_at` | `2026-09-08T05:33:53.289611Z` |
| `production_eligible` | `false` |
| attestation | `source_manifest` / role `source` / env `probe` — verified |
| `n_relations` | 22 (v1 had 19; +3 RFC 9110 header obsoletes) |

v1 was **not** overwritten. Succession v1 still pins
`ietf_workflow_manifest.p57.http-semantics.v1.signed.json`. Packed parents and
the promoted product were not touched.

## Compiled RFC 9110 header relations (official bytes)

From `ietf:rfc:9110` facts / relations in the v2 manifest:

| Kind | RFC numbers |
| --- | --- |
| `obsoletes` | `[2818, 7230, 7231, 7232, 7233, 7235, 7538, 7615, 7694]` |
| `updates` | `[3864]` |

This matches the parser unit expectation and a compile of the on-disk
`rfc9110.txt`.

v1 compiled 9110 `obsoletes` were only `[2818, 7230, 7231, 7232, 7233, 7235]`
and `updates` `[3864]`. v1 also attached bogus `obsoletes:2015` facts on RFC
7538 and RFC 7615; those are gone in v2 (7538 still records out-of-graph
`obsoletes:7238` as `not_requested`; 7615 still records `obsoletes:2617` as
`not_requested`).

## Signing path

Same path as the original TLS / HTTP Semantics / HTTP/3 graphs:

1. Existing fetch inventory (no refetch):
   `p57_ietf_http_semantics_graph_v1/ietf_fetch_inventory.json`
   SHA-256 `8dd8cacbb884a856ceb67af99b7ae0626cbac3a0076d364fdd2a5f9ae3fd2f5e`
2. `scripts/export_ietf_workflow.py` → `build_ietf_workflow_from_fetch_inventory`
   then `attach_attestation(..., purpose="source_manifest")`
3. Wrapper:
   `scripts/run_with_local_probe_trust.py --role source`
   trust
   `/workspace/wynckeliao/.longworld-scaleout-20260906-private/ietf-http-semantics-graph/local_probe_trust.json`

Python: `/workspace/wynckeliao/longworld-worlds/.venv/bin/python`.
`HF_HUB_OFFLINE=1` `TRANSFORMERS_OFFLINE=1`.

## Timeline

No blocker. Fetch `completed_at` is `2026-09-07T04:18:12.214795Z`. Export
`generated_at` is `2026-09-08T05:33:53.289611Z`, which satisfies
`completed_time <= export_time`. The existing signing path supplied
`generated_at` as UTC now; inventory observation timestamps were not edited
and the timeline check was not loosened.

## Boundary

Do not overwrite v1. Do not pack succession v2 from this graph in this closeout.
Do not promote, quality-gate, or upload HF until a later task owns that work.
