# P80 parallel synthesis and real-source binding

Date: 2026-09-24. Status: **verified candidate bank; train_ready=false**.
No GPU training or release promotion was run.

## Canonical candidate bank

`data/candidates/p80_unified_with_row_join_v1/` first appends two P80
scaling lanes to frozen P79, then a verified real two-document JOIN lane.
The hash-bound `manifest.json`, `merged/manifest.json`, `coverage.json`,
and lane receipts are the accounting authority. Existing P79 reader bytes
were copied and checked, not re-tokenized under changed code.

| Measure | P79 | P80 | Change |
| --- | ---: | ---: | ---: |
| Final reader views | 3,300 | 4,729 | +1,429 |
| Global independent semantic tasks | 3,248 | 4,650 | +1,402 |
| Source/world groups | 107 | 377 | +269 simulated worlds, +1 Wiki group |
| Wiki views | 513 | 866 | +353 |
| Controlled simulation views | 288 | 1,364 | +1,076 |
| Finance views | 1,682 | 1,682 | 0 |
| CodeForge views | 817 | 817 | 0 |

P80 has 3,675 train and 1,054 eval **candidate** views. Physical final-chat
bins: `<32K`: 680, `32–64K`: 999, `64–128K`: 2,008, `128–256K`: 1,042;
observed range 2,843–261,954 tokens. The labels come from measured reader
messages, not target budgets or certified evidence distances. There are 14
recorded domain labels, 34 topic labels and 28 operation labels; the new
operation is `cross_document_table_join`. Only 20 source groups have more
than one operation. These labels
are inventory descriptors, not proof of semantic diversity.

## What ran

- Four-process controlled simulation produced 269 fresh worlds/1,076 tasks
  across seven native families, depth 1/2 where feasible, and 32/64/128K
  target budgets. Of 312 planned shards, 43 were infeasible under the
  consumed-record and minimum-distractor budget; they were recorded and
  excluded. Native replay and output verification passed. The same record
  grammar still underlies these worlds, so fresh seeds are not new domains.
- Four-worker Wiki execution on the 18 frozen source groups produced 684
  independent native tasks and 760 length views. Existing source/recipe
  quota 32 was a measured cap; raising it to 96 exposed more valid table
  rows. Relative to P79, 423 final reader views were exact duplicates,
  while 337 views and 310 globally new semantic tasks survived strict
  reader-message, source, answer and split comparison. The increment is
  335 lookup views and two table-pair views; it adds no source group, domain,
  or topic. The native plan recorded 77 rejected candidate rows and 71
  unsupported source/recipe cells.
- Finance final-chat tokenization can now use bounded parallel threads in
  the unified merger. On a real eight-row NVIDIA fixture, serial and two-
  worker output bytes matched; warmed wall time was 4.18s versus 2.94s.
  This is a local small-run measurement, not a batch throughput guarantee.
- The P80 unified append verifies frozen P79 batch/plan/lane hashes and
  only executes new lanes. Re-running P80 with `--resume` verified the same
  manifest and reader hashes.
- A generic frozen-table row binder ran against 18 Wiki source groups. One
  bridge group yielded 16 deduplicated two-document JOIN tasks, each
  69,681–69,687 final-chat tokens; 17 groups yielded zero. Final reader
  replay, unique first-row binding, target alternatives, and independent
  masking of selector and target cells passed. Source snapshot pins,
  train/eval assignments, final answer and token equations were rechecked
  after export. The separate verified v3 probe was appended as the final
  P80 lane; these 16 tasks are not length duplicates.

## Real dependency and source expansion boundary

Connected Wiki discovery searched 27 anchor entities and previewed 30
novel pages. None supported the current strict complementary-table rule;
zero new source groups or tasks were admitted from that route. This is a
source-parser/row-linking bottleneck, not a shortage solved by a larger
per-source task quota. The successful bridge JOIN used already frozen
documents, not pages from this live discovery route. Its two-cell masking
shows bounded visible-table dependence; it does not prove that no other
natural-language route exists in the full text.

The merged bank remains candidate-only: the native oracles and scoped
interventions do not prove global model-visible information necessity.
Finance's 1,682 rows still lack strict long-dependency verification;
controlled simulation relies on one record grammar. Neither physical
length nor task count justifies a model-training claim. The near-term
expansion priority is to turn real source pairs with complementary typed
rows into hash-bound reader candidates, and to route additional document
genres through native parsers only after their source/evidence checks pass.
Historical Cyber, IETF, GovInfo, Macro and ResearchLab candidates were not
quietly imported: current audits hold or sharply limit their long-context
quality, and reusing them would overstate accepted diversity.

## Next scaling gate

Keep one scheduler and source ledger, and expand supported
`source_component × recipe × length` cells. The measured 1/18 JOIN yield
means the next source router must search for two pages sharing an unambiguous
row entity **and different answer-bearing columns**, then preserve table
headers, units and revisions. Batch-probe those frozen pairs before long
reader materialization; record zero-yield and split-conflict reasons. Add
new document genres through native parsers, not a domain-name substitution
over the simulated record grammar. Track qualified source components,
independent tasks, operation distribution, and final length bins separately.

For future admission, store source/answer replay, bounded reader-text
intervention, alternative-support search and physical token position as
separate fields. This P80 bank has only the checks listed in its native
receipts. No fixed ratio of real/simulated rows or GPU run follows from
these counts; first promote a quality-filtered subset and compare it under
equal training budgets against the frozen P79 bank.

## Inspect and reproduce

```bash
cat data/candidates/p80_unified_with_row_join_v1/manifest.json
cat data/candidates/p80_unified_with_row_join_v1/merged/manifest.json
cat data/candidates/p80_unified_with_row_join_v1/coverage.json
less -R data/candidates/p80_unified_with_row_join_v1/merged/sample_index.jsonl
cat data/candidates/p80_wiki_task_delta_v2/manifest.json
cat data/candidates/p80_wiki_row_binding_probe_v3/manifest.json
cat data/candidates/p80_wiki_task_scale_v1/result.json
cat data/capability_records/p80_connected_wiki_intake_v2/acquisition_manifest.json
```

```bash
uv run python scripts/run_unified_synthesis_batch.py \
  --config configs/p80_unified_with_row_join_v1.json \
  --output data/candidates/p80_unified_with_row_join_v1 \
  --base-batch data/candidates/p80_unified_scale_v1 \
  --workers 4 --resume
uv run python scripts/report_unified_synthesis_coverage.py \
  data/candidates/p80_unified_with_row_join_v1/merged \
  --output data/candidates/p80_unified_with_row_join_v1/coverage.json
```
