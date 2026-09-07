# P57 TLS 1.3 handshake succession v4 — unique 64k/128k probe product

Date: 2026-09-07
Status: local probe product signed. World
`ietf-tls13-handshake-succession-v4` passed dense audit, unique-profile
selection, promotion, `p57-ietf-tls13-64k-128k-extension-probe-1-v1` quality
gate, and B5 export. `production_eligible=false`. Not on
`docs/CURRENT_RELEASE.md` / Hugging Face. Packed parents are not inventory.

## Counts (do not collapse views into tasks)

| Quantity | Value |
| --- | ---: |
| N_source-world | 1 |
| N_semantic-task | 1 |
| N_proof-family | 1 |
| N_training-view | 6 |

This is one succession query at two natural lengths × three views, not six
independent tasks. 16k/32k were never packed: seven thick relation endpoints
do not fit those bands.

Program `ietf.tls13_handshake_succession.v1`. Adapter
`standards.ietf_tls13_handshake_succession.v1`. Isolated trust
`/workspace/wynckeliao/.longworld-scaleout-20260906-private/ietf-tls13/`.

## Length-view pairing vs nested relation growth

64k and 128k share the same authentic relation set (11), essentials (6), and
succession answer. Leftover is authentic RFC body, not padding. The unique
profile is in `LENGTH_VIEW_PAIR_PROFILE_IDS` so selection/quality-gate do **not**
require 64k→128k nested relation growth. Canonical/strict 4k/8k window
insufficiency is unchanged: all six views still have
`contiguous_windows_insufficient=true` and `global_proof_green=true`.
`production_eligible=false` is unchanged.

Do not reuse `p17-finance-128k-extension-probe-1-v1` (requires 16/32/64/128).
Do not mix this world into CURRENT_RELEASE.

## Exact promoted rows

Directory:
`/workspace/wynckeliao/longworld/data/releases/p57-ietf-tls13-handshake-succession-probe-1-v1-promoted-v1/`

Split: 6 train / 0 eval. Views: 64/128 × full/cf/ordered. Gate `ok=true`.
B5 export `n=6`, `tokens_est=581947`, `token_spread=0.0`. Exact Qwen context
tokens across the six views: **581,651**.

| File | SHA-256 |
| --- | --- |
| `train.jsonl` | `01ef1c0f38aa021a47017902146a908f5cd62d729a8f0a7640581ed4538cccf4` |
| `eval.jsonl` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `quality_report.json` | `08b37d29cce4bd2c09c7864cbe56e4f14dbec8beb599be1dd365b5f4641cb98a` |
| `release_gate_receipt.json` | `efd1f7e280eafd3dd03ddf06d64425b138f9717066581b68998c421e8979682c` |
| `llamafactory/B5.json` | `76dd5bd93a3fd3bdd4a6a24e4aea863713b2fdd4089f210b781fc1f720df415e` |
| `llamafactory/training_export_manifest.json` | `015ab24c92de2c56de2a723f9e9102b16a38023fc44f4fcc6611cc53f9ed7a31` |

`promoted_row_set_sha256`
`cb4990de2b7b129cc7c0455e4c94576ffaa5b59759150be07bad2f75c5f6aeea`.
Release profile SHA-256
`e6498c7886a440afd68272229deb5d5e02643115abc831bb58605196a637a7bc`.
`diagnostic_only=true`; `n_clones=0`; near-dup 0.0.

## Boundary

Do not add this product to CURRENT_RELEASE or Hugging Face until a
production/KMS receipt exists. Do not clone HTTP/3 isolate/5-field packing.
Do not pad HTTP/2, DNSSEC, or ACME leftover to 128k.
