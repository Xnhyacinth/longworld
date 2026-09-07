# P57 IETF QUIC/HTTP3 unique-token preflight — 2026-09-06

Date: 2026-09-06
Status: source fetched; unique-token capacity is sufficient; **no oracle, generate, rank, or promotion**

## Decision

The QUIC transport + HTTP/3 family is a **distinct IETF specification graph** from
P40 OAuth and from the blocked RFC 9421 revision stack. Official RFC Editor
bytes were fetched into a new inventory. Exact Qwen tokens of the eight RFCs
sum to **239,288**. That is above the exact 128K window, so a later generator
must pack a **subset of distinct RFCs**, not concatenate the whole family and
not pad.

This is **not** a new train-ready world. The OAuth semantic-growth generator and
sidecar remain OAuth-specific. A QUIC/HTTP3 requirement oracle still has to bind
real `updates` / normative-reference edges and a remove-one flip. Do not reuse
OAuth evidence IDs on these bytes.

## Source

Inventory:
`data/source_inventory/p57_ietf_quic_http3_family_v1/`

Fetch request: `configs/p57_ietf_quic_http3_family_fetch_v1.json`
Authorization: `p57-ietf-quic-http3-family-v1`

Isolated probe trust:
`/workspace/wynckeliao/.longworld-scaleout-20260906-private/ietf-quic/`
(initialized; unused because this preflight did not sign a workflow manifest)

Tokenizer: `Qwen/Qwen3.5-4B` revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`, `add_special_tokens=False`,
`HF_HOME=/workspace/wynckeliao/.cache/huggingface`, offline.

## Per-record exact tokens

| Record | Tokens |
| --- | ---: |
| `rfc8999.txt` (version-independent properties) | 4,306 |
| `rfc9000.txt` (QUIC transport) | 95,537 |
| `rfc9001.txt` (QUIC TLS) | 34,701 |
| `rfc9002.txt` (loss detection / congestion) | 22,227 |
| `rfc9114.txt` (HTTP/3) | 39,460 |
| `rfc9204.txt` (QPACK) | 23,385 |
| `rfc9368.txt` (version negotiation) | 9,111 |
| `rfc9369.txt` (HTTP/3 over QUIC v1) | 10,561 |
| **RFC unique-pool sum** | **239,288** |
| `draft-ietf-quic-transport-34.txt` | 119,188 |
| Datatracker document + relations | 6,877 |

All eleven files have distinct SHA-256. Draft-34 versus RFC 9000 word Jaccard is
**0.759**, not the RFC 9421 ~0.92 publication-reflow collapse. Draft-34 is still
the predecessor of RFC 9000, so it must not be counted as a second specification
in the unique pool. The controlling unique-token count is the **RFC family
239,288**.

## Band implication

| Band | Unique RFC pool vs window |
| --- | --- |
| 32K `[32000, 32768]` | pool is larger; pack a distinct-RFC subset |
| 64K `[64000, 65536]` | same |
| 128K `[128000, 131072]` | pool 239,288 > 131,072; subset required, no leftover-fill |

Example subsets are not claimed as packed layouts. A later generator must
measure exact serialized prompts, not this table.

## Next (not done)

A signed HTTP/3 requirement graph now exists:
`reports/p57_ietf_http3_quic_requirement_oracle_20260906.md`.
Generate / project / rank / promotion are still pending. Do not clone P40 OAuth
packing record IDs onto this family.
