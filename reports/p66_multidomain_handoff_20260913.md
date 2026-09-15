# P66 multi-source candidate handoff closeout

## Decision

The signed handoff contains 200 local SFT candidates from three admitted
domains: 164 byte-identical P65 CodeForge/Finance rows and 36 corrected P66
Cyber/OSV rows. It does not count IETF, Macro, or ResearchLab as training data.
Those attempted batches remain scientific diagnostics because their current
contracts do not satisfy the repository's dependency and fidelity gates.

This handoff is a local candidate artifact. Strict long dependency, training
release, framework preprocessing, and production eligibility are all false.

## Included inventory

| Domain | Train | Eval | Rows | Worlds | Contexts | Full-chat capacity 64K / 128K / 256K | Full-chat exact 64K / 128K / 256K | Evidence class |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| CodeForge | 96 | 34 | 130 | 7 | 109 | 12 / 47 / 71 | 1 / 2 / 2 | content-backed finite scoped certificate |
| Finance disclosure versions | 18 | 16 | 34 | 2 | 5 | 0 / 14 / 20 | 0 / 0 / 0 | natural long local candidate; strict dependency unverified |
| Cyber/OSV | 18 | 18 | 36 | 6 | 6 | 12 / 12 / 12 | 12 / 12 / 12 | short-window resistant, compact target records sufficient |
| **Total** | **132** | **68** | **200** | **15** | **120** | **24 / 73 / 103** | **13 / 14 / 14** | mixed local-candidate evidence |

There are 200 canonical tasks and 200 training views. Full-chat length is
46,017--258,694 tokens; p10 is 64,839, median is 137,375, and p90 is
228,294. Total full-chat tokens are 29,693,964. The 159 rows outside the
three narrow exact intervals are retained only in their ordinary capacity
bins. Lengths were recomputed with the pinned Qwen/Qwen3.5-4B revision,
thinking disabled, complete assistant targets, and no truncation.

Cyber contributes the only admitted P66 increment: 36 rows, balanced 18/18
between train and eval, with 12 rows in each exact 64K/128K/256K interval.
Its six worlds are source-disjoint and its 4K/8K/16K, single-record,
last-packed-record, and remove-target controls solve 0/36. A compact context
containing all four queried target records solves 36/36, so these rows are
retrieval/multi-record integration curriculum and do not establish strict
long dependency.

## Scientific quarantine

The pre-quarantine P66 exploration produced 411 training views over 195
canonical tasks and 48 worlds. Only the 36 corrected Cyber views entered this
handoff.

| Attempted domain | Excluded views | Reason |
|---|---:|---|
| IETF | 3 | The counterfactual conflicts with publication/code information present in the question, output key order violates the prompt, and compact/latest controls are unmeasured. |
| Macro vintage | 324 | Answer programs consume observations while answer-irrelevant relation blocks create the named length bands. Observation-only content does not reach those declared lower bounds. |
| ResearchLab | 48 | The rebuilt diagnostic receipt marks shortcut controls unmeasured; substantive visible-prose filtering and delta/subset deduplication remain incomplete. |

No empty IETF, Macro, or ResearchLab JSONL is emitted or registered in
`dataset_info.json`.

## Provenance and replay

The handoff binds the persistent-report-signed P65 receipt
`56c28c73ce40963375fd60e53e9f47f2351170f55b18b276b35b1b688ad32f15`
and corrected Cyber receipt
`82b496438abfeecf7ed9f9a928503d29bd86839964bccadc128b9b172ef885ba`.
Every bound output, source, config, and code hash is rechecked. The four P65
domain/split JSONLs are byte-identical to their signed source handoff.

The admitted source catalog contains only corrected Cyber and completed native
replay at `reports/p66_pipeline_admitted_v1/PIPELINE_RECEIPT.json`. Catalog
SHA-256 is
`fce6639aa5ce1de26ff260e351e786f5320be30ab2347f20150063a6b6683f13`.

The signed handoff receipt is
`data/candidates/p66_multidomain_handoff_v1/BUILD_RECEIPT.json`, SHA-256
`8814beef038e25e5bcb599425962683a52b097d7ce2310f81ffae2e793911c57`.
Native handoff validation passed, and a clean temporary rebuild reproduced all
nine output files byte-for-byte, including the signed receipt.

The 12 handoff tests and 9 shared catalog-runner tests pass; Ruff is clean.
LLaMA-Factory and Hugging Face `datasets` are not installed in this project
environment, so no framework-native preprocessing smoke was claimed. The
repository's pinned tokenizer path did load every complete message and verify
all 200 full-chat lengths without truncation.
