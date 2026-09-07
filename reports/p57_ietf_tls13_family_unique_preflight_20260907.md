# P57 IETF TLS 1.3 family unique-token preflight — 2026-09-07

Date: 2026-09-07
Status: source fetched; unique-token capacity is sufficient; **no oracle, generate, rank, or promotion**

## Decision

The TLS 1.3 / DTLS 1.3 family is a **distinct IETF specification graph** from
P40 OAuth, the blocked RFC 9421 revision stack, and the HTTP/3/QUIC requirement
graph. Official RFC Editor bytes were fetched into a new inventory. Exact Qwen
tokens of the fourteen RFCs sum to **286,235**. After excluding RFC 8446, which
already sits in the HTTP/3 requirement graph, the unique-delta pool is
**204,608**. Both counts are above the exact 128K window, so a later generator
must pack a **subset of distinct RFCs**, not concatenate the whole family and
not pad.

This is **not** a new train-ready world. Do not clone HTTP/3 packing, OAuth
evidence IDs, or `p40`/`p43` profiles onto these bytes.

## Source

Inventory: `data/source_inventory/p57_ietf_tls13_family_v1/`

Fetch request: `configs/p57_ietf_tls13_family_fetch_v1.json`

Authorization: `p57-ietf-tls13-family-v1`

Isolated probe trust:
`/workspace/wynckeliao/.longworld-scaleout-20260906-private/ietf-tls13/`

Tokenizer: `Qwen/Qwen3.5-4B` revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`, `add_special_tokens=False`,
`HF_HOME=/workspace/wynckeliao/.cache/huggingface`, offline.

Machine measurement: `reports/p57_ietf_tls13_family_unique_preflight_v1.json`

## Band implication

| Pool | Tokens | vs 128K `[128000, 131072]` |
| --- | ---: | --- |
| All TLS-family RFC bodies | 286,235 | subset required |
| Unique-delta excluding HTTP/3 RFCs | 204,608 | subset required; RFC 8446 not double-counted |

Draft-ietf-tls-tls13-28 is the predecessor of RFC 8446 and is **not** counted
in the unique pool.

## Next (not done)

Bind a TLS handshake/requirement oracle to authentic `updates` / normative
reference edges with a remove-one flip. Packed parents are not train-ready.
