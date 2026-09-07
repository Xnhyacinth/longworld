# P57 parallel world/domain/task scale-out — 2026-09-07

This is an execution status, not a train-ready or production receipt.
`production_eligible=false`. Packed parents are not inventory until promoted.

## Train-ready unchanged

NVIDIA v5 remains the only **promoted** new signed local-probe product this
wave. Micron now has NVIDIA-class 12/12 dense window green parents but is not
promoted or B5-exported yet. Amazon v3, HTTP/3 v4, and HTTP/3 v6 have real
source and packed parents but failed dense window audits. Do not count them.

HTTP/3 v6 leftover-at-ends packed in-band, then dense audit failed
`contiguous window 4k retrieves the gold answer: 1:37`. That is off `0:10`
but still reconstructs. Do not lower the 4k gate.

## Parallel tracks

| Track | What landed | Filter result |
| --- | --- | --- |
| New entity: Micron IR | Packed `finance_micron_asset_trajectory_fy2022_2025_v1` | Exact bands 16317/32323/64656/129633. 12/12 `global_proof_green`. 4k/8k contiguous windows insufficient (Amazon/Meta zipper class **passing**). Not promoted yet. Isolated trust `micron/`. |
| New task: TLS 1.3 handshake graph | Signed `p57_ietf_tls13_handshake_graph_v1` | Closed published_as 8446; obsoletes 5077/5246/6961; updates 5705/6066. Family unique-delta still **204,608**. No TLS oracle/packing yet. Do not clone HTTP/3 packing. |
| New task: HTTP Semantics | Family unique-delta **171,076** (9110/9111/9112). Closed graph signed `p57_ietf_http_semantics_graph_v1` | published_as 9110 plus authentic Updates/Obsoletes. Distinct from HTTP/3. No oracle yet. |
| New task: HTTP/2 graph | Signed `p57_ietf_http2_graph_v1` | Unique RFC delta **103,810** (7540/8740/9113). **128k leftover blocked**. 64k leftover is possible. No oracle yet. |
| New task: PKIX path-validation graph | Signed `p57_ietf_pkix_path_graph_v1` | Graph pool 170,254; unique-delta vs PKIX family **82,943** (3280/4325/4630). 5280 overlaps the family inventory. No oracle yet. |
| New family: SSH 4251–4254 | Inventory `p57_ietf_ssh_family_v1` | Unique **52,837**. **128k blocked**. 64k leftover possible. Not a closed published_as graph. |
| New family: DNSSEC 1034/1035/4033–4035 | Inventory `p57_ietf_dnssec_family_v1` | Unique **126,639**. **128k blocked** (1,361 short). Do not pad. |
| New task: PKIX / OCSP / CT | Family inventory only | RFC pool **209,796**. No closed published_as graph yet. |
| New task: ACME | Inventory `p57_ietf_acme_family_v1` | RFC pool **82,082**. **128k blocked**. Do not pad. |
| New entity: Meta IR | Packed `finance_meta_asset_trajectory_fy2022_2025_v1` | Exact bands 16324/32437/64730/129456. 12 views ranked. Dense audit failed `contiguous window 8k retrieves the gold answer: 0:16`. Same zipper class as Amazon. Do not lower the 8k gate. |
| Tesla IR | issuer site 403; not Q4 FilingId CMS | Skipped. No sec.gov hammer. |
| Amazon 8k / HTTP/3 4k | existing packed parents | Still fail-closed. Do not lower windows. |

Isolated trusts live under
`/workspace/wynckeliao/.longworld-scaleout-20260906-private/`
(`ietf-tls13`, `ietf-pkix`, `ietf-acme`, `ietf-http-semantics`,
`ietf-http-semantics-graph`, `ietf-http2`, `ietf-pkix-path`, `ietf-ssh`,
`ietf-dnssec`, `meta`, `micron`).

## What is not done

- Micron probe promote + B5 export in a unique release dir (NVIDIA recipe)
- TLS / HTTP Semantics / HTTP/2 / PKIX-path requirement oracles (authentic edges, remove-one)
- Meta 8k window pass, promotion, CURRENT_RELEASE
- Amazon leftover reservation so 2022/2023 factless rows survive into 128k spread
- HTTP/3 leftover thick enough that 4k cannot cover artifacts `1:37`
- DNSSEC authentic obs-target expansion to clear 128k unique leftover
- Any HF / production update
