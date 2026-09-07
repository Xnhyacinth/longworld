# P57 IETF HTTP/3–QUIC requirement oracle — 2026-09-06

Date: 2026-09-06
Status: signed source workflow + answer program exist. **No generate / project /
rank / promotion.** Not train-ready.

## Outcome

HTTP/3 connection establishment is now a distinct IETF requirement graph from
P40 OAuth. Official bytes were fetched into a new inventory. RFC 9114's
Datatracker relations (not the HTTP/3 draft's sibling-draft refs) ground:

| Kind | Source | Target |
| --- | --- | --- |
| `published_as` | `draft-ietf-quic-http-34` | RFC 9114 |
| `normative_reference` | RFC 9114 | RFC 9000 (QUIC transport) |
| `normative_reference` | RFC 9114 | RFC 9204 (QPACK) |
| `normative_reference` | RFC 9114 | RFC 7301 (ALPN) |
| `informative_reference` | RFC 9114 | RFC 8446 (TLS 1.3) |

RFC 9001 is **not** a RFC 9114 edge. TLS 1.3 is stated in RFC 9114 §3.2 and
bound through the informative `[TLS]` / RFC 8446 reference. Do not mint a
9001 relation.

Answer program: `ietf.http3_quic_effective_requirement.v1`
Schema: `longworld.ietf-http3-quic-requirement-task.v1`

Fields: transport, tls_version, alpn, header_compression, settings. Remove-one
evidence or relation flips the full answer. SETTINGS is RFC 9114-local plus
publication; the other fields require the corresponding RFC 9114 edge.

## Source

Inventory:
`data/source_inventory/p57_ietf_http3_quic_requirement_graph_v1/`

Fetch request: `configs/p57_ietf_http3_quic_requirement_graph_fetch_v1.json`
Authorization: `p57-ietf-http3-quic-requirement-graph-v1`

Isolated probe trust:
`/workspace/wynckeliao/.longworld-scaleout-20260906-private/ietf-quic/`

Signed workflow:
`ietf_workflow_manifest.p57.http3-quic-requirement.v1.signed.json`

`production_eligible=false`. Fetch inventory SHA-256
`de96d1c8ee143c87427539ac09a8c2dfa69bad5c5481b38f5011dc2ca1b41634`.
Signed workflow SHA-256
`0fac710151335c26e948d9e64917192d9c27497bf0019b132ada79a89fa2823f`.

The unique-token family inventory
`p57_ietf_quic_http3_family_v1` remains the capacity preflight. It was not
overwritten and was not signed, because its RFC set is not relation-closed
under the draft-only Datatracker extractor.

## Fetch extension

Optional request field `rfc_datatracker_sources` (subset of `rfc_numbers`)
fetches Datatracker document + relations for published RFCs. Absent, existing
OAuth inventories are unchanged. HTTP/3 needs this because
`draft-ietf-quic-http` refs `draft-ietf-quic-transport` / `draft-ietf-quic-qpack`,
while RFC 9114 refs RFC 9000 / RFC 9204.

## Next (not done)

1. New packing/generator for this program. Do not clone P40 OAuth evidence IDs
   or `ietf.oauth_effective_requirement.v1` onto these bytes.
2. Exact-band packing must use a **subset** of the family unique pool (239,288
   Qwen tokens). RFC 9114+9000 already overflow 128K if concatenated whole.
3. Project / dense audit / `p38` or a new HTTP/3 profile only after packing
   stays in-band without padding.
