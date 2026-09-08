# P57 SSH architecture succession v1 — unique 32k pack

Date: 2026-09-08
Status: local probe parents packed and three views projected. MiniLM ranked
3/3. Official dense audit failed complexity (`essential_ids >= 2`) because
gold n=1 keeps all answers on one RFC 4251 span. `production_eligible=false`.
Not on Hugging Face. Packed parents are not inventory.

## Counts (do not collapse views into tasks)

| Quantity | Value |
| --- | ---: |
| N_source-world | 1 |
| N_semantic-task | 1 |
| N_proof-family | 1 |
| N_training-view | 3 |

One RFC 4251-gold architecture query at one natural length × three views.
64k was never packed: unique leftover is **52837** tokens
(`rfc4251`+`rfc4252`+`rfc4253`+`rfc4254`) and must not be padded into the
exact 64k band.

Program `ietf.ssh_architecture_succession.v1`. Schema
`longworld.ietf-ssh-architecture-succession-task.v1`. Adapter
`standards.ietf_ssh_architecture_succession.v1`. Isolated trust
`/workspace/wynckeliao/.longworld-scaleout-20260906-private/ietf-ssh/`.

## Exact packed tokens

Directory:
`/workspace/wynckeliao/longworld/data/candidates/p57_ietf_ssh_succession_v1/`

Parent 32k full: **32268**. Projected views:

| View | Exact Qwen context tokens |
| --- | ---: |
| full | 32268 |
| ordered_artifact_view | 32268 |
| cf | 32241 |
| **sum** | **96777** |

Gold n=1: all evidence quotes are official RFC 4251 bytes
(`ietf:rfc:4251:chars:0000000-0018674`). Leftover-only RFC 4252 and RFC 4254
are each one whole span. RFC 4253 (16488 tokens) is omitted: packing it as a
whole span with 4252+4254 overflows 32k. Last leftover of RFC 4251 is pinned.
Draft `draft-ietf-secsh-architecture-22` is identity-paragraph only.
`isolate_evidence_ids` is refused.

## Audit status

Task-level oracle: `strict_replay=true`, `remove_one_evidence_fails=true`
(evidence-only replay; publication is listed but not required to resolve
fields). MiniLM ranked 3/3 projected views
(`all-MiniLM-L6-v2` rev `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`).
Official `create_task_dense_audit` failed:
`task proof complexity is insufficient` because gold n=1 has
`len(essential_artifact_ids) == 1` (gate requires ≥ 2).
