# P14 ResearchLab Gopher source-foundation closeout

## Outcome

Gopher (`arXiv:2112.11446`, v1--v2) has enough reachable, non-bibliography
LaTeX content for a future 16/32/64K ResearchLab task, but the current generic
paper exporter rejects the official fetch inventory fail-closed. No signed
manifest or source-workflow bundle was produced, generation remains disabled,
and this source does not count toward ResearchLab quota.

## Official fetch inventory

The bounded request is
`configs/p14_researchlab_gopher_public_fetch_request_v1.json`. The existing
fetcher retrieved the official arXiv Atom records and source archives into
`data/source_inventory/p14_paper_gopher_revision_preflight_v1/`.

| Revision | Prior | Archive bytes | Archive SHA-256 |
| --- | --- | ---: | --- |
| v1 | none | 8,032,322 | `34bee7d4c894b40e7469e92d56bae38246029215a3ed9310da7de7c7aafe2513` |
| v2 | v1 | 8,131,416 | `fc8172c4f9317debdd9714014fa4ba11ba9805f86b2b70136430a69102fe6249` |

Both archives are below the 16 MiB acquisition cap. The safe archive parser
exports one reachable `main.tex`; bibliography, cache, author,
acknowledgement, contribution, and unreachable archive members are excluded
from the capacity calculation. The resulting fetch inventory SHA-256 is
`7ab7c98cd4629be8a693ea56f304c0431e2b6c961821644a515282752d63dd43`.
Its flags remain `generation_integration=disabled`,
`semantic_facts_train_ready=false`, and `production_eligible=false`.

## Fail-closed exporter result

The signed export command stops with:

```text
longworld.core.provenance.ProvenanceError: paper revision has no reliable new semantic LaTeX body
```

The existing semantic sentence extractor finds 582 candidates in v1 and 589
in v2. Eight v2 sentences are exact-unique and absent from v1, but none has a
numeric value accepted by `format_revision_added_delta`. Consequently the
work has a real metadata `v2 revision_of v1` edge but no current-contract
`revision_added_text` fact. This closeout does not bypass that contract with a
hand-written v1 manifest and does not weaken the exporter.

## Section capacity preflight

Using the pinned local `Qwen/Qwen3.5-4B` tokenizer revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`, the eligible compiled source has
52 complete section/subsection units and 90,633 tokens. Every proposed unit
has one exact content claim whose anchor occurs once in the v2 source.

| Stage | Strictly nested complete units | Claim-body tokens | Packed source-order claim span |
| --- | ---: | ---: | ---: |
| 16K | 6 | 14,365 | 14,275 |
| 32K | 12 | 30,158 | 29,954 |
| 64K | 24 | 61,644 | 60,491 |

The 16K units are Models, State-of-the-Art Comparisons, main-text
Distributional Bias, Dataset Pipeline, MMLU, and Compute Usage. The 32K stage
adds BIG-bench, Safety Benefits/Risks, TruthfulQA, Training Dataset,
Conclusion, and Efficient-Training Future Work. The 64K stage adds Results
Overview, appendix Distributional Bias, Inference/Training Cost Reduction,
Model Card, Dialogue/Toxicity, Toxic Generations, Fact-Checking,
Introduction, Background, Scale Improvements, and main-text Toxicity.

The raw room to each exact-band upper bound for the required bounded endpoint,
revision relation, compile-order control, decision, and prompt is respectively
2,019, 2,610, and 3,892 tokens. This is only a combined allowance. At 16K, the
675-token v1 Training Dataset endpoint leaves at most 1,344 tokens for all
other controls, so it does **not** preserve a separate 2K control allowance.
The smallest observed changed section endpoint is Towards Efficient
Architectures (v1 354 tokens; v2 385), but using both sides still leaves only
1,280 tokens beyond the endpoints under the current 16K plan. Any future task
must resolve this with a naturally smaller endpoint/stage combination before
implementation, not by padding, splitting sections, or relaxing a gate.

## Reproduction

```bash
uv run --frozen python scripts/fetch_paper_workflow.py \
  --request configs/p14_researchlab_gopher_public_fetch_request_v1.json \
  --out-dir data/source_inventory/p14_paper_gopher_revision_preflight_v1

uv run --frozen python scripts/run_with_local_probe_trust.py \
  --trust-file /root/.longworld-p12-active/p12-probe-12-20260829-v1/local_probe_trust.json \
  --role source --allow-combined-roles -- \
  .venv/bin/python scripts/export_paper_workflow.py \
  --fetch-inventory data/source_inventory/p14_paper_gopher_revision_preflight_v1/paper_fetch_inventory.json \
  --out data/source_inventory/p14_paper_gopher_revision_preflight_v1/paper_workflow_manifest.p14.gopher-source-foundation.signed.json
```

The second command is expected to fail with the exporter error above. There is
no bundle-build command because no signed manifest exists.
