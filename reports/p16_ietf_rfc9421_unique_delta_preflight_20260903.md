# P16.4 IETF RFC 9421 unique-delta preflight — 2026-09-03

## Decision

**BLOCKED.** Unique Qwen tokens after collapsing byte-identical and
near-identical drafts (including RFC-editor reflow of draft-19) are
**72,113**, below the exact 128K band `[128000, 131072]`. There is no uniquely
byte-bound substantive normative hunk. No sidecar adapter, generate, rank, or
audit was run. RFC leftover was not padded. RFC 9110 was not copied in as
gold.

This is the same failure mode as ResearchLab Megatron: v1≈v2 collapse leaves
~50k unique tokens and cannot fill an exact band. Here drafts 17/18/19 collapse
with RFC 9421 into one specification.

## Source (already on disk; not re-fetched)

Inventory:
`/workspace/wynckeliao/longworld/data/source_inventory/p12_wave3_ietf_rfc9421_v1/`

| File | SHA-256 |
| --- | --- |
| `ietf_workflow_manifest.signed.json` | `493e7bd809c3891143f088c46b9faaafe4102117304315fc3b41d1c90a75ad04` |
| `ietf_fetch_inventory.json` | `82e1953b96ebca88bafc1320b8d1099ecdc18d67dc031a912769874a406c0341` |
| `draft-ietf-httpbis-message-signatures-17.txt` | `46488861e986108f9eed3828f73f1930221934eb8da568ae39e0827688280992` |
| `draft-ietf-httpbis-message-signatures-18.txt` | `d78911703dbd12d6af17168a879a1a818cb8c35d91b5c5ccf1e70dbd179e1daf` |
| `draft-ietf-httpbis-message-signatures-19.txt` | `67d1969858ba36a7b2ce0decc050c037ec23591d61040c7467b475d8d64ce967` |
| `rfc9421.txt` | `612655786bf4293bfc486e4177571467fbb3de6e6f0eea90cb74c346a34fdf3c` |
| fetch request config | `ecaee280986b274c88dbb92bfcbd55ddedaae7c2bd00698686b6ca77e56b91bd` |

Signed manifest attestation: scheme `hmac-sha256-v2`, purpose
`source_manifest`, role `source`, environment `probe`, key_id
`probe-source-3a689228f9679b0522a18cadd07f1e97`, digest
`5f7ec44183395c183a64f5742e4d1c0caba46164a8525700e14048b3d3b5243b`.
Trust file was not mixed with ResearchLab Sparks 128K.

Tokenizer: `Qwen/Qwen3.5-4B` revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`, `HF_HOME=/workspace/wynckeliao/.hf`,
offline, `add_special_tokens=False`.

## Per-record exact tokens

| Record | Kind | Exact Qwen tokens |
| --- | --- | ---: |
| `ietf:draft:draft-ietf-httpbis-message-signatures-17` | draft_revision | 71,429 |
| `ietf:draft:draft-ietf-httpbis-message-signatures-18` | draft_revision | 72,113 |
| `ietf:draft:draft-ietf-httpbis-message-signatures-19` | draft_revision | 72,104 |
| `ietf:rfc:9421` | rfc | 63,742 |
| datatracker relation | supporting | 2,721 |
| datatracker document | supporting | 769 |
| Naive uncollapsed primary sum | | 279,388 |

None of the four primary records are byte-identical.

## Near-identical collapse

Line-level body ratio ≥ 0.90 collapses drafts 17/18/19 into one cluster. Draft-19
versus RFC 9421 is only 0.631 at line level because the RFC Editor reflowed the
text; word-level quick-ratio is 0.959 and word Jaccard is 0.921. That is
publication wrapping, not a second specification.

| Collapse | Unique exact tokens | Fills `[128000,131072]` |
| --- | ---: | --- |
| Byte-identical only (4 records) | 279,388 | no (near-dup / over cap) |
| Line-level I-D cluster + RFC kept separate | 72,113 + 63,742 = **135,855** | no (over 131,072 and double-counts reflow) |
| Word-level substance collapse (17/18/19/RFC) | **72,113** | **no** |

The controlling unique-token count is **72,113**. Concatenating the RFC as
leftover would be the Megatron v1=v2 failure mode and still misses the exact
128K window.

Adjacent line-level unique-delta tokens: 17→18 = 7,688; 18→19 = 2,190;
19→RFC = 42,716 (reflow). Those deltas are not extra unique mass once the
documents are collapsed.

## Unique substantive delta

Topology on the signed graph:

- `revision_of` 18→17
- `revision_of` 19→18
- `published_as` 19→RFC 9421
- no `updates` / `obsoletes` (RFC 9421 header has neither; inventory contains
  only RFC 9421)

`build_ietf_normative_change_task` requires exactly one uniquely byte-bound
normative revision hunk. Both `revision_of` edges have **four**
anchor-matched MUST/MAY wording swaps (Dictionary vs List-or-Dictionary, and
Signature vs Signature-Input trailer), so the hunk is not unique. The 18→19
edge is header-only (line body ratio 0.988). Header/boilerplate identity
strings were not treated as unique proof.

Current `audit_ietf_workflow_manifest` also fail-closes because the on-disk
fetch receipt omits `observed_at`. Completing timestamps via a live IETF
re-fetch would not add unique tokens, so the inventory was not re-fetched.

## Stop conditions honored

- No sidecar adapter.
- No generate / rank / audit.
- No padding, no RFC leftover fill, no RFC 9110 gold.
- No `uv sync`.
- Probe trust was not mixed with Sparks 128K artifacts.
- Exact-band, near-dup, and unique-hunk gates were not weakened.

Machine-readable twin: `reports/p16_ietf_rfc9421_unique_delta_preflight_v1.json`.
Pin: `configs/p16_ietf_rfc9421_unique_delta_preflight_v1.json`.
