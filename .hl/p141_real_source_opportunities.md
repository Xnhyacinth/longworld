# P141 frozen real-source opportunity index

P141 reads the existing P131 exact-oldid Wikipedia HTML campaign, P115 annual
filing source-shape matrices, P127 raw-TeX paper source matrix, and P139
capability coverage. It adds no reader QA. Its unit is a **typed native source
unit** (one accepted HTML table, one filing metric cell, or one paper archive
with same-file reference targets), not a domain label, training row, or proof
of long-context dependence.

The frozen batch yielded **170 source units**: 144 HTML tables from 47 Wiki
source groups, three previously bounded filing metric cells from one issuer,
and 23 paper archives with parser-recognized same-file targets. The source
inventory considered 98 Wiki pages, eight filing issuers and 34 papers. Its
490 rejection-ledger rows include 51 Wiki pages with no accepted HTML grid,
277 too-small/no-header tables, 22 short alternative numeric-support windows,
15 absent filing report units, and 11 paper archives with no usable same-file
target or parser failure. Rejection rows count reasons/page absences, so they
are not mutually exclusive source-unit totals.

The report separates source/domain/topic/operation-opportunity counts and
retains row/header/origin-cell/citation shape, filing metric/period/alternative
scope, and paper reference/target capacity. For Wiki, the 144 complete-set
*opportunities* are deliberately broad structural possibilities: P131's
strict compiler admitted only 38 short tasks. For papers, 23 archives expose
the relevant shape but P127 admitted only two reader tasks after natural-cue,
leakage, distance, answer, and deletion gates. Three P115 filing cells had
already passed its bounded check; P141 does not elevate that check.
Paper `topic` values are work IDs, so their count is not scientific-topic
diversity; the report retains them for stable source indexing only.

The P139 long cells provide a concrete next-batch priority. At 128–256K,
selected complete-set scan has seven Wiki and three filing tasks; selected
paper reference trace has one. Scaling those cells requires new linked source
material, typed row/metric/reference binding, final-token evidence placement,
and reader-side alternative-support checks. Reordering or padding the current
short HTML tables cannot certify a long task. Source rights and model gain
remain unverified; the output is `train_ready=false`.

Reproduce and inspect:

```bash
cat data/candidates/p141_source_opportunity_v1/report.json
cat data/candidates/p141_source_opportunity_v1/manifest.json
less -R data/candidates/p141_source_opportunity_v1/opportunities.jsonl
less -R data/candidates/p141_source_opportunity_v1/rejections.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/p141_source_opportunity_index.py --config configs/p141_source_opportunity_v1.json --output data/candidates/p141_source_opportunity_v1 --verify-only
```
