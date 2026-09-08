# P57 ACME issuance succession v1 — 64k blocked

Date: 2026-09-08
Status: fail-closed. Official signed graph cannot compile. Unique leftover
82082 tokens is the 64k family, not 128k; do not pad. `production_eligible=false`.
Not on Hugging Face. Packed parents are not inventory.

## Counts (do not collapse views into tasks)

| Quantity | Value |
| --- | ---: |
| N_source-world | 0 |
| N_semantic-task | 0 |
| N_proof-family | 0 |
| N_training-view | 0 |

Program `ietf.acme_issuance_succession.v1`. Gold n=1 on RFC 8555
(issuance RFC that holds the answer). TDD
`tests/test_ietf_acme_succession.py` passes on a synthetic 8555-cites-leftover
graph. Official fetch inventory cannot produce that graph.

## Unique tokens

Family unique RFC tokens: **82082** (`rfc8555`+`rfc8737`+`rfc8738`+`rfc8823`+`rfc9444`+`rfc9773`).
RFC 8555 alone is **48577**, below the exact 64k band `[64000, 65536]`.
128k is blocked: unique 82082 must not be padded into `[128000, 131072]`.

## Blockers

1. **RFC target closure.** `_validate_rfc_target_closure` requires
   `published | referenced == requested`. Only RFC 8555 is `published_as`.
   Datatracker/header relations from 8555 do not target 8737/8738/8823/9444/9773
   (later RFCs cite 8555; they are sources, not targets). Compile raises
   `requested RFC relation target is not grounded`.
2. **No signed workflow** at
   `ietf_workflow_manifest.p57.acme.v1.signed.json`. Generate refuses to pack.
3. **Cannot hit 64k from 8555-only** without leftover family RFCs or padding.
   Padding is refused.

Inventory:
`/workspace/wynckeliao/longworld/data/source_inventory/p57_ietf_acme_family_v1/`
(`ietf_fetch_inventory.json` sha256
`46f4dbb8c6171d98a4812608f9b0bcb81aba186bc66ced30c60a716822ce28aa`).
Trust wrap
`/workspace/wynckeliao/.longworld-scaleout-20260906-private/ietf-acme/local_probe_trust.json`.
`isolate_evidence_ids` / HTTP/3 5-field packing refused.
