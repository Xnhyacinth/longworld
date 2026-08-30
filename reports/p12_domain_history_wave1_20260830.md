# P12 Domain Cumulative History Wave 1

Date: 2026-08-30 UTC

## Status

This wave materializes one unique, source-bound Cyber task at three cumulative
length bands. These artifacts are executable **candidate histories**, not
promotion-v2 rows or complete worlds. Every row declares `train_ready=false`,
`production_eligible=false`, `promotion_eligible=false`, and
`complete_world=false`.

The materializer verified the source-role signature on the Cyber workflow
manifest and then recomputed the SHA-256 of the raw official CISA KEV response.
The resulting candidate manifest is intentionally unsigned, so candidate rows
declare `real_source_verified=false` and only
`source_verified_at_materialization=true`. Promotion must independently bind
and sign the source evidence.

## Materialized Cyber history

Source snapshot:
`data/source_inventory/p12_wave3_cyber_log4shell_v1/cisa-known-exploited-vulnerabilities.json`

Workflow: deterministic CISA KEV catalog audit ordered by source `dateAdded`
with an explicit CVE-ID lexical tie-break for records observed on the same day.
The answer program returns the slice size, date-order validity, first/last
records under that declared ordering, maximum remediation window, and yearly
entry/ransomware counts and checkpoint records.

| Band | Qwen tokens | Unique records | Explicit catalog relations | Verified-derived order relations | Essential/support | Replayed proof depth |
| ---- | ----------: | -------------: | -------------------------: | -------------------------------: | ----------------: | -------------------: |
| 16K  |      16,201 |             82 |                         81 |                               80 |                82 |                   81 |
| 32K  |      32,125 |            159 |                        158 |                              157 |               159 |                  158 |
| 64K  |      64,125 |            314 |                        313 |                              312 |               314 |                  313 |

The 16K context is a byte prefix of 32K, and 32K is a byte prefix of 64K.
Every extension adds whole, distinct source records. No record is duplicated,
truncated, randomly concatenated, or replaced with generic padding.

All three candidates pass strict context replay, executed counterfactual replay,
counterfactual answer change, per-essential remove-one failure, single-record
insufficiency, surface-gold negative, semantic body corruption, context digest,
record/relation/graph replay, no-duplicate, declared exact-band, source-binding
shape, and non-promotion boundary gates. Cross-band cumulative-growth errors: 0.

## Capacity rejections

The existing Clinical and Regulation executable tasks pass their source,
answer, counterfactual, remove-one, and corruption audits, but do not contain
enough unique verified text for one 16K history:

| Domain     | Unique source records | Pinned Qwen source-body tokens | Result                                |
| ---------- | --------------------: | -----------------------------: | ------------------------------------- |
| Clinical   |                     3 |                          5,167 | `insufficient_verified_source_tokens` |
| Regulation |                     4 |                            748 | `insufficient_verified_source_tokens` |

They were not padded. The next wave must ingest additional official clinical
approval/review history and complete regulation docket/document histories.

## Reproducibility and digests

Two independent local materializations under the protected source role produced
identical bytes:

- `candidates.jsonl`: `66167dfd206b13be5a7a563d18ede30c70ec268dde0896055c4bd7b05cdee059`
- `MANIFEST.json`: `123066c16652d38eb6d6c0f1d617720d89bd11aa527653fe5125e7d559a3c343`
- `capacity_rejects.jsonl`: `8603f760f152986d019fc861e9966d9a296b75e510289b32b783f804b51be90f`
- Signed source manifest: `0baabe7d86f79c17c6ea3ec80410eda444e51efbb1e2314cf7822ac06a609836`
- Raw source response: `25527b196142e9bfe5f3597ea31c302129b86e4240608ef76730b2533b61aae3`
- Qwen tokenizer asset manifest: `bbcbdfe073f579453f3c891f989a43fbb15cc88952e9f8ae294f04f6ca2036cb`

Materialized files are under
`data/domain_history_candidates/p12_wave1/` and remain ignored non-release
artifacts.
