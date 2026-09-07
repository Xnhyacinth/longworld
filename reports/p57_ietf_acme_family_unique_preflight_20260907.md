# P57 IETF ACME family unique-token preflight — 2026-09-07

Date: 2026-09-07
Status: source fetched; **128K unique-token capacity is insufficient**; no
oracle, generate, rank, or promotion

## Decision

The ACME issuance family is a distinct IETF specification graph from
QUIC/HTTP/3, TLS 1.3 handshake packing, PKIX path validation, OAuth, and
RFC 9421. Official RFC Editor bytes were fetched. Exact Qwen tokens of the six
RFCs sum to **82,082**. That fills 32K/64K windows but is **below** the exact
128K window `[128000, 131072]`. Do not leftover-fill, concatenate drafts as a
second unique spec, or clone another family's packing onto these bytes.

This is **not** a train-ready world and is not a 128K candidate until a larger
authentic ACME-unique RFC pool exists.

## Source

Inventory: `data/source_inventory/p57_ietf_acme_family_v1/`

Fetch request: `configs/p57_ietf_acme_family_fetch_v1.json`

Authorization: `p57-ietf-acme-family-v1`

Isolated probe trust:
`/workspace/wynckeliao/.longworld-scaleout-20260906-private/ietf-acme/`

RFCs: 8555, 8737, 8738, 8823, 9444, 9773. Draft-ietf-acme-acme-18 is the
predecessor of RFC 8555 and is not counted in the unique pool.

Tokenizer: `Qwen/Qwen3.5-4B` revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`.

Machine measurement: `reports/p57_ietf_acme_family_unique_preflight_v1.json`
