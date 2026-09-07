# P57 IETF PKIX family unique-token preflight — 2026-09-07

Date: 2026-09-07
Status: source fetched; unique-token capacity is sufficient; **no oracle, generate, rank, or promotion**

## Decision

The PKIX certificate / OCSP / Certificate Transparency family is a **distinct
IETF specification graph** from QUIC/HTTP/3, TLS 1.3 handshake packing, OAuth,
and RFC 9421. Official RFC Editor bytes were fetched into a new inventory.
Exact Qwen tokens of the ten RFCs sum to **209,796**. None of those RFC files
overlap the HTTP/3 or TLS 1.3 inventories, so the unique-delta pool is the
same **209,796**. That is above the exact 128K window; a later generator must
pack a **subset of distinct RFCs**, not concatenate the whole family and not
pad.

This is **not** a new train-ready world. Do not clone HTTP/3 or OAuth packing
onto these bytes.

## Source

Inventory: `data/source_inventory/p57_ietf_pkix_family_v1/`

Fetch request: `configs/p57_ietf_pkix_family_fetch_v1.json`

Authorization: `p57-ietf-pkix-family-v1`

Isolated probe trust:
`/workspace/wynckeliao/.longworld-scaleout-20260906-private/ietf-pkix/`

Predecessor draft in the fetch is `draft-ietf-pkix-rfc2560bis-20` (OCSP;
RFC 6960). It is **not** counted in the unique RFC pool.

Tokenizer: `Qwen/Qwen3.5-4B` revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`, `add_special_tokens=False`.

Machine measurement: `reports/p57_ietf_pkix_family_unique_preflight_v1.json`

## Band implication

| Pool | Tokens | vs 128K `[128000, 131072]` |
| --- | ---: | --- |
| PKIX-family RFC bodies | 209,796 | subset required |
| Unique-delta vs HTTP/3 and TLS 1.3 | 209,796 | fully disjoint RFC files |

## Next (not done)

Bind a path-validation / critical-extension requirement oracle to authentic
normative edges with a remove-one flip. Packed parents are not train-ready.
