# P14 ResearchLab Llama 3 source foundation

## Outcome

The Llama 3 Herd of Models (`arXiv:2407.21783`, v1--v3) passes the existing
source-only contracts and is retained as the second ResearchLab entity
candidate. This closeout contains no task registration or generated training
rows. The signed source artifacts remain local-probe diagnostics with
`generation_integration=disabled`.

## Official source and lineage

The bounded request is
`configs/p14_researchlab_llama3_public_fetch_request_v1.json`. The existing
fetcher retrieved and safely parsed all three official arXiv source archives.

| Revision | Prior | Occurred at | Archive bytes | Archive SHA-256 |
| --- | --- | --- | ---: | --- |
| v1 | none | 2024-07-31T17:54:27Z | 6,876,595 | `76edebee16d35fcbf94f07f50f0ddc083023c80e78473a58d20c39821637c5a8` |
| v2 | v1 | 2024-08-15T13:57:20Z | 6,884,111 | `eaa11ed93bb5449a05ecff8c91384a17ad4fbe56c22a29c4b4594e245e151ea6` |
| v3 | v2 | 2024-11-23T23:27:33Z | 6,883,599 | `da6dc9f7b180ba164d002239c8e0af5a181c07ae5f9af093743d4d2b99f02430` |

The signed manifest contains three revision records and the exact v2→v1 and
v3→v2 `revision_of` relations. The current exporter also emits exact
`revision_added_text` facts for v2 and v3, but both selected facts are from
acknowledgements/contributor additions. They satisfy the unchanged generic
source contract only. They must not enter a future task answer, essential
claim set, or length budget.

## Compiled content capacity

The v3 archive exports 80 TeX files. Recursive resolution from `paper.tex`
finds 56 compiled-reachable files. After excluding contributors, wrapper-only
files, prompt assets, and unreachable appendices, 44 scientific-content files
remain with 81,531 tokens under the pinned local `Qwen/Qwen3.5-4B` tokenizer
revision `a7b0d22b993d71000cf2eadfb37222a67cee521e`.

The only compiled-reachable scientific file whose payload changes from v2 to
v3 is `pretraining/model_scaling.tex`:

| Revision | File tokens | Payload SHA-256 | Changed table value |
| --- | ---: | --- | --- |
| v2 | 5,763 | `7b4a874fb44e3c34e8c0d6fb1cee694684052f9f4c450ed2346b44a268db1a5b` | tensor-parallel row value `4` |
| v3 | 5,763 | `fa2165a779a5b42fd237a066bf7b9fd57af55304fb8153c703d4fd22c8f680a7` | tensor-parallel row value `8` |

This complete file pair is the required future v3→v2 endpoint. The payload
SHAs differ and the changed numeric cell is exact and answer-verifiable.
Using an unchanged 194-token table on both sides is forbidden because it would
create only a surface dependency.

## Strictly nested stage preflight

The large 5,763-token v2 endpoint is counted separately below. To retain at
least 2K tokens beyond the endpoint for the relation, compile-order control,
decision, answer program, and prompt, the source bodies are reduced with
complete scientific files rather than truncation or padding.

| Stage | v3 claim files | v3 body | Body + v2 endpoint | Remaining to upper bound | Ordered claim span |
| --- | ---: | ---: | ---: | ---: | ---: |
| 16K | 4 | 8,284 | 14,047 | 2,337 | 8,133 |
| 32K | 8 | 23,981 | 29,744 | 3,024 | 23,830 |
| 64K | 14 | 55,844 | 61,607 | 3,929 | 55,679 |

The nested file sets are:

- 16K: `overview.tex`, `pretraining/model_scaling.tex`,
  `results/video_recognition.tex`, and
  `results/tables/speech_ast_results.tex`.
- 32K adds `posttraining.tex`, `results/tables/benchmarks.tex`,
  `inference/fp8.tex`, and `vision/data.tex`.
- 64K adds `introduction.tex`, `pretraining/data.tex`,
  `pretraining/model_architecture.tex`, `results/finetuned.tex`,
  `results/safety.tex`, and `results/speech.tex`.

Every selected file contains a distinct exact scientific-content claim, such
as pre-training topology, post-training procedure, benchmark score, FP8
compute share, vision-data pipeline, or safety evaluation. Claim anchors are
unique in the v3 source. The compiled source-order spans exceed the unchanged
8K/16K/22K thresholds. Exact prompt admission remains a task-implementation
gate; this source closeout does not claim a 9/9 world.

## Signed artifacts

- Fetch inventory:
  `data/source_inventory/p14_paper_llama3_revision_preflight_v1/paper_fetch_inventory.json`
  (`sha256=0c64b9949719e50dd3097f62652a97e24246d3fac45903e30b35997cbb0bbfc7`)
- Manifest:
  `data/source_inventory/p14_paper_llama3_revision_preflight_v1/paper_workflow_manifest.p14.llama3-source-foundation.signed.json`
  (`sha256=8ce5e8b7c252f4b3318a5e9a827272b8da36a2f93b57c776f732467eb7dca464`)
- Bundle:
  `data/source_inventory/p14_paper_llama3_revision_preflight_v1/paper_source_workflow_bundle.p14.llama3-source-foundation.signed.json`
  (`sha256=2ca7b9448646ddda01281890e6fb5f595b2f6acad6fadcc7c6f8c94aa241ed3c`)
- Adapter revision: `sourceworkflow@2`
- Attestation environment: `probe`
- Source attestation key ID:
  `probe-source-3a689228f9679b0522a18cadd07f1e97`

The bundle reloads as one `paper_workflow` containing three records and two
relations.

## Reproduction

```bash
uv run --frozen python scripts/fetch_paper_workflow.py \
  --request configs/p14_researchlab_llama3_public_fetch_request_v1.json \
  --out-dir data/source_inventory/p14_paper_llama3_revision_preflight_v1

uv run --frozen python scripts/run_with_local_probe_trust.py \
  --trust-file /root/.longworld-p12-active/p12-probe-12-20260829-v1/local_probe_trust.json \
  --role source -- \
  .venv/bin/python scripts/export_paper_workflow.py \
  --fetch-inventory data/source_inventory/p14_paper_llama3_revision_preflight_v1/paper_fetch_inventory.json \
  --out data/source_inventory/p14_paper_llama3_revision_preflight_v1/paper_workflow_manifest.p14.llama3-source-foundation.signed.json

uv run --frozen python scripts/run_with_local_probe_trust.py \
  --trust-file /root/.longworld-p12-active/p12-probe-12-20260829-v1/local_probe_trust.json \
  --role source -- \
  .venv/bin/python scripts/build_source_workflow_bundle.py \
  --paper-manifest data/source_inventory/p14_paper_llama3_revision_preflight_v1/paper_workflow_manifest.p14.llama3-source-foundation.signed.json \
  --adapter-revision sourceworkflow@2 \
  --out data/source_inventory/p14_paper_llama3_revision_preflight_v1/paper_source_workflow_bundle.p14.llama3-source-foundation.signed.json
```
