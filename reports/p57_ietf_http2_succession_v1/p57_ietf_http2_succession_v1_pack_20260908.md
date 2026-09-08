# P57 HTTP/2 succession v1 — unique 64k probe pack

Date: 2026-09-08
Status: local probe parents packed and three views projected. Compiler lives
in `reports/p57_ietf_http2_succession_generate.py` (core adapters not
registered). `production_eligible=false`. Not on `docs/CURRENT_RELEASE.md` /
Hugging Face. Packed parents are not inventory.

## Counts (do not collapse views into tasks)

| Quantity | Value |
| --- | ---: |
| N_source-world | 1 |
| N_semantic-task | 1 |
| N_proof-family | 1 |
| N_training-view | 3 |

One RFC 9113-gold succession query at one natural length × three views.
128k was never packed: unique leftover is 103810 tokens
(`rfc7540`+`rfc8740`+`rfc9113`) and must not be padded.

Program `ietf.http2_succession.v1`. Schema
`longworld.ietf-http2-succession-task.v1`. Intended adapter
`standards.ietf_http2_succession.v1` is **not registered** in
`longworld/core` (owned by the NVIDIA adapter track). Isolated trust
`/workspace/wynckeliao/.longworld-scaleout-20260906-private/ietf-http2/`.

## Exact packed tokens

Directory:
`/workspace/wynckeliao/longworld/data/candidates/p57_ietf_http2_succession_v1/`

Parent 64k full: **65062**. Projected views (not official V3 sidecar):

| View | Exact Qwen context tokens |
| --- | ---: |
| full | 65062 |
| ordered_artifact_view | 65062 |
| cf | 65028 |
| **sum** | **195152** |

Gold n=1: all evidence quotes are official RFC 9113 bytes. Leftover-only
RFC 8740 is one whole span. Thick leftover RFC 7540 is 8192-token chunks,
not exploded tinies. Last leftover chunk of RFC 9113 is pinned.
`isolate_evidence_ids` is refused.

## Audit status

Task-level oracle: `strict_replay=true`, `remove_one_evidence_fails=true`,
`remove_one_relation_fails=true`. MiniLM ranked 3/3 projected views
(`all-MiniLM-L6-v2` rev `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`).
Official `create_task_dense_audit` / V3 sidecar rebuild is blocked until
the HTTP/2 adapter is registered in `taskreplaysidecar.py` /
`taskproof.py` / `taskpromotion.py`.
