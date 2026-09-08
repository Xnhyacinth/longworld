# P57 HTTP Semantics succession v1 — unique 128k probe product

Date: 2026-09-07
Status: local probe product signed. World
`ietf-http-semantics-succession-v1` passed dense audit, unique-profile
selection, promotion, `p57-ietf-http-semantics-128k-extension-probe-1-v1`
quality gate, and B5 export. `production_eligible=false`. Not on
`docs/CURRENT_RELEASE.md` / Hugging Face. Packed parents are not inventory.

## Counts (do not collapse views into tasks)

| Quantity | Value |
| --- | ---: |
| N_source-world | 1 |
| N_semantic-task | 1 |
| N_proof-family | 1 |
| N_training-view | 3 |

This is one 9110-gold succession query at one natural length × three views,
not three independent tasks. 16k/32k/64k were never packed: seven thick
relation endpoints plus the 9110 gold pin do not fit those bands.

Program `ietf.http_semantics_succession.v1`. Adapter
`standards.ietf_http_semantics_succession.v1`. Isolated trust
`/workspace/wynckeliao/.longworld-scaleout-20260906-private/ietf-http-semantics-graph/`.

## Length-view pairing vs nested relation growth

The unique profile is 128k-only. It is in `LENGTH_VIEW_PAIR_PROFILE_IDS` so
selection/quality-gate do **not** require 16k/32k clones or nested
causal-history growth. Canonical/strict 4k/8k window insufficiency is
unchanged: all three views have `contiguous_windows_insufficient=true` and
`global_proof_green=true`. Dense top-k is also insufficient
(`embedding_topk_insufficient=true`). `production_eligible=false` is
unchanged.

Do not reuse `p17-finance-128k-extension-probe-1-v1` (requires 16/32/64/128).
Do not reuse `p57-ietf-tls13-64k-128k-extension-probe-1-v1` (requires 64k).
Do not mix this world into CURRENT_RELEASE.

## Exact promoted rows

Directory:
`/workspace/wynckeliao/longworld/data/releases/p57-ietf-http-semantics-succession-probe-1-v1-promoted-v1/`

Split: 3 train / 0 eval. Views: 128k × full/cf/ordered. Gate `ok=true`.
B5 export `n=3`, `tokens_est=386711`, `token_spread=0.0`. Exact Qwen context
tokens across the three views: **386,493** (128811 / 128841 / 128841).

| File | SHA-256 |
| --- | --- |
| `train.jsonl` | `87a9efd64ad3eb07ee94bab67fa7a19229b1a4d2c101d109976d1abda23aed7a` |
| `eval.jsonl` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `quality_report.json` | `8bcf9310868e0c4cc714a6d4f72ddec9476a79c5fd617a7ae7325ab3642983d5` |
| `release_gate_receipt.json` | `68ae36270707f3e78f2045d56633b20981d42393d7f35a829568b88ccf26ed06` |
| `llamafactory/B5.json` | `b00affe8e4aa3e73ec7fde82e3952c9b7e3a702910250324b453b7c1d8304c47` |
| `llamafactory/training_export_manifest.json` | `2213f505de4dadf0f32853df9b82d5bca7ce91a07174ed35fad381f6b638eef9` |

`promoted_row_set_sha256`
`4a7f5f9af07de67ca978949855a2d295453c1a3b73f1c07dd8307e71a9b62b97`.
Release profile SHA-256
`94b987da050f6225bbb18b64125857a8875062b1b74bd5a433a451e5104faa1e`.
`diagnostic_only=true`; `n_clones=0`; near-dup 0.0.

## Boundary

Do not add this product to CURRENT_RELEASE or Hugging Face until a
production/KMS receipt exists. Do not clone HTTP/3 isolate/5-field packing.
Do not pad HTTP/2, DNSSEC, or ACME leftover to 128k. The signed HTTP
Semantics graph still omits RFC 7538/7615/7694 as compiled `obsoletes`
until it is re-exported after the header-parser fix.
