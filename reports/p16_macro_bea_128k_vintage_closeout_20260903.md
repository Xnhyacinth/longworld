# P16.1 Macro BEA workbook-vintage 128K closeout

Date: 2026-09-03

Outcome: **SUCCESS**. The existing Macro vintage adapter filled an authentic
four-band world from unique BEA GDP/GDI workbook cell-revision trajectories
joined by an as-of calculation. Exact-128K `full` / `cf` /
`ordered_artifact_view` cells exist at **128,251** Qwen tokens. Dense/strict
audit accepted **12/12** projected rows. Near-duplicate ratio is **0.0**. No
padding, no CISA KEV/uv relabel, and no P14 overwrite.

## Topology

Program: `macro.as_of_revision_path.v2` on official BEA real GDP percent change
for **2005Q4**. Each band reconstructs the complete revision path from the first
vintage through an answer-changing decision date, using both
`revises_observation` and `supersedes_without_observed_value_change` edges.
Length comes from additional same-series period trajectories packed as
`natural_background`, not from cloned documents.

| Band | Context tokens | Target vintages | Essential artifacts | Proof depth | Unique docs | Background trajectories |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 16k | 16,102 | 5 | 9 | 5 | 40 | 7 |
| 32k | 32,102 | 7 | 13 | 7 | 79 | 10 |
| 64k | 64,102 | 11 | 21 | 11 | 156 | 11 |
| 128k | 128,251 | 15 | 29 | 15 | 311 | 14 |

128K essential span is 126,135 tokens (minimum required 64,001). Causal-gold
roles stay on the target revision path; background remains `natural_background`.

## Unique-token preflight

Pinned tokenizer: `Qwen/Qwen3.5-4B` revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`.

Same-series unique pool for `BEA_REAL_GDP_PERCENT_CHANGE`:

- 1,054 observations + 956 relations = 2,010 unique documents (0 SHA-256 repeats)
- 819,178 joined document-context Qwen tokens (body 809,133)
- four-band target prefixes 5 / 7 / 11 / 15 (9 eligible executable lengths)

The unique pool exceeds `[128000, 131072]`. The adapter selected a subset that
lands inside the band. Remaining unused records were not padded in.

## Candidate-local audit

Trust identity: byte-identical copy of
`/workspace/wynckeliao/.longworld-agent-probe/p12-probe-12-v2/local_probe_trust.json`
(SHA-256 `0e43b7a8de8d957ddabc3e2abde8e13244ca488164e2892bc22abfcc81f63ae5`)
loaded from a mode-0700 private tree because the original parent directory is
0750 with a POSIX ACL. Combined local-probe roles only.

Dense ranker: `sentence-transformers/all-MiniLM-L6-v2` revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41` via
`/tmp/p13-st-venv/bin/python` (`sentence-transformers==6.0.0`). No `uv sync`.

All 12 rows: `near_dup_sentence_ratio=0.0`, dense top-k prefix answers
`unknown`/`unknown`/`unknown`, `embedding_topk_insufficient=true`,
`full_pool_strict_replay_sufficient=true`, `local_window_insufficient=true`,
`contiguous_windows_insufficient=true`, BM25/TF-IDF top-k insufficient,
remove-one and counterfactual green. `train_ready=false`,
`production_eligible=false`.

P14 views at `p14-current-trust-macro-bea-real-gdp-2005q4-*` were not modified.

## Artifacts

| Path | SHA-256 |
| --- | --- |
| `configs/p16_macro_bea_gdp_gdi_vintage_128k_v1.json` | `65ad0a45f2870ffb4313785f3a305647f1ed223c2f5513919122e2d69a83fcab` |
| unique-token preflight | `0fcae028575eec2d86f8f7171e3d47ce89d2aa7a7f7d4c5753ef4c8dccca23da` |
| BEA xlsx | `c6b10cc799e213974cb73fb7083221e71b8298e98cb5c3b36153a42cd09af3fe` |
| workflow manifest | `d7b699557fb6ff63fdeabb2fd3e1a28502b8335f88fa279041d6c4b621ae7b27` |
| source `candidates.jsonl` | `ba7998e6a6145fadf84fff0a64ab632d489faad907f1ca257ec9c4d9ec0387b6` |
| source sidecar | `27158e5c172dc793b494ba78c5415560834a0ce03f7dfcbe2e6a50c47aae3042` |
| packing plan | `47a2f794d85f885b05b60e93a49ee02beb2675d67e827545ba78e5371de6118a` |
| prepared candidates | `47de6e4c1e7c8d881204cfb1df4d6933e5e43211d2f0273f9f516c1c4b7e2c13` |
| projected candidates (12 rows) | `0ccea0d7c4b126de405a6e38148a55169f5a629e7b0e3423b02622240d443711` |
| v3 sidecar | `bfed53ee993e1793741fd0a2b29fe256c0cbb175ff8a348a8ac48ecef13a7768` |
| rankings | `4b5a1ca5a90eb65290884ccbe158d735e11deb3e43c8c47b886516d25ce888f5` |
| audits.jsonl | `561fd8c447de9772567548cf35cc99a2f6e2b5e27fc50298460d0034a4bd0fc6` |
| AUDIT_MANIFEST.json | `ee105ec79e6ed5ed0a04a672f20f8c60782a9551937ba5cc09318fa457848c45` |

Release directories (external data workspace, not Git):

- `data/releases/p16-macro-bea-real-gdp-2005q4-source-128k-v1`
- `data/releases/p16-macro-bea-real-gdp-2005q4-candidate-128k-v1`
- `data/releases/p16-macro-bea-real-gdp-2005q4-views-v1`

This slice does not select, promote, export B-conditions, or upload HF.
