# P76 source-native synthesis: execution ledger

Date: 2026-09-24. Status: active local data engineering. No new GPU training,
promotion or publication was run.

## Scope and counting

The batch unit is an independent semantic task with a native source/oracle,
not a product name or `world × length × view`. `world` means a bounded source
and state cluster. Source kind, domain, topic, operation, capability, final
input length, evidence status and reader-text necessity are separate axes.
Unknown labels remain unknown. Strict long-dependency and local-train
eligibility are separate from syntactic source support.

Three read-only reviews audited the native pipelines, evidence semantics and
primary related work before implementation. The chosen architecture is a thin
index over native Finance, CodeForge, OSV, Wiki and simulation oracles. P75's
reader exporter only admitted Wiki locate/source-compare, while P64/P66 have
separate domain-native tasks. Forcing them all through the current Wiki-like
`SemanticWorld` would discard table, code and event semantics.

## Unified inventory (historical products, no new training release)

`scripts/build_p76_batch_index.py` streamed the P64, P66, P71 and P75 local
indexes into `data/candidates/p76_batch_index_v1/`. The result is 8,726 source
rows, 8,596 unique semantic tasks, 130 duplicate P66 CodeForge views of P64,
zero rejected rows, 1,532 known source groups and 1,519 known native world IDs.
The latter two counts are metadata counts, **not** 1,532 independent real
source worlds. Source kinds are 6,016 simulated-record tasks, 2,556 real
workflow tasks and 24 real Wiki reader candidates. The batch manifest sets
`train_ready=false`.

Exact final length distribution over unique tasks: `<32K` 1,697; `32–<64K`
655; `64–<128K` 5,237; `128–<256K` 1,007. These are physical lengths, not
certified evidence distances. `scripts/report_p76_distribution.py` joins
native P64 operation metadata and exposes the source-kind × domain × operation
× length cells in `distribution.json`. There are 804 selected CodeForge,
1,682 Finance, 36 OSV and 34 finance-disclosure tasks; 6,040 tasks (P71+P75)
have no domain field in their current sample indexes, and all 8,596 lack a
sample-level topic label. Those gaps are explicit, not inferred from product
names. Of the 2,486 selected P64 real-workflow tasks, native operations span
11 Finance families and 6 CodeForge programs. P66's 70 net-new task operations
are not present in this joined report.

Reproduce:

```bash
uv run python scripts/build_p76_batch_index.py --input p64=data/sft/p64_primary_training_v2/sample_index.jsonl --input p66=data/hf/LongWorld-Worlds-State/snapshots/2026-09-13/p66_multidomain_candidates/sample_index.jsonl --input p71=data/capability_records/p71_pool_v1/sample_index.jsonl --input p75=data/p75_real_reader_candidates_v1/sample_index.jsonl --output-dir data/candidates/p76_batch_index_v1
uv run python scripts/report_p76_distribution.py --index data/candidates/p76_batch_index_v1/sample_index.jsonl --finance data/hf/LongWorld-Synthesis-Workspace/candidates/p64_finance_taskbank_v2 --codeforge data/hf/LongWorld-Synthesis-Workspace/candidates/p64_codeforge_taskbank_v2 --output data/candidates/p76_batch_index_v1/distribution.json
less -R data/candidates/p76_batch_index_v1/coverage.json
less -R data/candidates/p76_batch_index_v1/distribution.json
```

## Newly frozen real-source pool and routing audit

`scripts/freeze_wiki_title_bundle.py` adds explicit list-page freezing. Eight
curated groups (astronomy, bridges, mountains, museums, parks, universities and
two expansions) froze 28 real pages, with per-page revision pins and CC BY-SA
source metadata. The title-bundle path does not invent category membership.
The first raw extraction yielded 2,304 facts, but the initial value-only source
screen found only 279 table facts with structural row/column support and no
obvious value template debris. The corrected subject-and-value screen over the
canonical v4 pool admits 255 units: astronomy (128), national parks (78)
and universities (49) dominate; bridge values retain template junk, while mountain and
museum pages produce no admitted clean table units under the current parser.
Even the 255 figure is a source-routing diagnostic, **not** 255 tasks or a
semantic entailment guarantee.

An additive parser rule for the exact `Year offoundation` header admitted four
additional clean Wales foundation years. The same four page revisions then
produced different facts while retaining the old snapshot ID, revealing that
the previous ID covered revisions and document IDs but not rendered text or
extracted facts. `build_snapshot` now binds both body and fact content into
new IDs. The reparsed university source was frozen separately under
`data/capability_records/p76_wiki_titles_v3/`. A metadata review also found
that explicit-title bundles were mislabelled `mediawiki_category` in
`source.kind`; a content-preserving, hash-bound repair of all eight groups is
now under `data/capability_records/p76_wiki_titles_v4/`, with parent digests in
`repair_manifest.json`. The v1/v2/v3 artifacts are not overwritten or treated
as independent sources. The v4 screen reports 2,308 facts and 255 clean
structurally supported table facts; university lists contribute 38 clean units
(32 England years, four Wales years and two locations).

```bash
uv run python scripts/screen_p76_wiki_sources.py --snapshots-dir data/capability_records/p76_wiki_titles_v4 --output data/capability_records/p76_wiki_titles_v4/source_screen_v2.json
less -R data/capability_records/p76_wiki_titles_v4/source_screen_v2.json
```

This demonstrates why sampling topics or downloaded pages blindly cannot
achieve the requested task distribution. The next source planner should route
by parser success and available operations, keep unavailable cells as explicit
rejects, and cache source parse/evidence once per revision. Table-pair task
compilation uses only the structurally supported, clean year cells.

## New reader task compilation and combined index

`scripts/export_p76_wiki_tables.py` compiled 24 independent observatory
cross-document year comparisons into 48 candidate views: one final input at
38,111–38,139 tokens and one at 66,966–66,994 tokens per task. It also
compiled four university comparisons from the corrected v4 title bundle as
four native-length eval candidates at 17,525–17,539 tokens. The two task
source groups have no page-title overlap; observatory stays train and
university eval. No row was rejected in these two small, selected source
groups. The university group cannot fill 32K/64K/128K with its native pages,
and the astronomy 128K cell is explicitly infeasible without the excluded
177K-token code directory. These infeasible cells were not padded.

Each question names two pages and their actual year-column labels; the answer
contains both named years and the earlier entry. Formal oracle, final-reader
table-parser replay and serialized answer agree in the audit. Removing all
parser-visible target year cells on either side makes that parser unable to
return the full answer. This is a **bounded table-parser dependence check**;
it does not exclude every paraphrased disclosure elsewhere in the original
prose or establish independent model-reader success. The 64K astronomy views
have a 40,581–47,407-token observed evidence envelope; this is evidence
position spread, not a lower bound on unrestricted reader proof distance.
Both exporters set `train_ready=false`.
The existing assistant-only SFT masker verified final chat lengths, label
boundaries, hash-bound source spans and 104 exact proof-span/token mappings
across all 52 new views. It counted 1,845 supervised tokens (1,714 train,
131 eval); the records are in each candidate directory's `verification.json`.

Combining the immutable P64, P66, P71, P73 shared-v2, P75 and new P76 indexes
gives 9,170 source rows, 9,040 distinct sample views and 9,016 unique
semantic tasks. The 154 repeated task rows consist of 130 P66 CodeForge
duplicates and 24 second-length astronomy views. There are 1,549 known native
world IDs and 1,561 source-group IDs; neither is a count of qualified real
worlds. The independent-task source-kind distribution is 6,016 simulated
records, 392 simulated shared-world tasks, 2,556 real workflows and 52 real
Wiki tasks. Only 28 new tasks have sample-level topic labels; the other 8,988
remain unknown. The combined manifest remains `train_ready=false`.

These CLIs refuse an existing output directory. For a rerun, replace each
`--output-dir` path with a fresh directory and point later commands at those
rebuilt paths.

```bash
uv run python scripts/export_p76_wiki_tables.py --output-dir data/candidates/p76_wiki_table_pairs_v5_astronomy --split train --domain astronomy --topic astronomical_observatories
uv run python scripts/export_p76_wiki_tables.py --snapshot data/capability_records/p76_wiki_titles_v4/universities_snapshot.json --output-dir data/candidates/p76_wiki_table_pairs_v5_universities --split eval --domain education --topic universities
uv run python scripts/verify_p75_real_reader.py data/candidates/p76_wiki_table_pairs_v5_astronomy
uv run python scripts/verify_p75_real_reader.py data/candidates/p76_wiki_table_pairs_v5_universities
uv run python scripts/build_p76_batch_index.py --input p64=data/sft/p64_primary_training_v2/sample_index.jsonl --input p66=data/hf/LongWorld-Worlds-State/snapshots/2026-09-13/p66_multidomain_candidates/sample_index.jsonl --input p71=data/capability_records/p71_pool_v1/sample_index.jsonl --input p73=data/capability_records/p73_shared_v2/sample_index.jsonl --input p75=data/p75_real_reader_candidates_v1/sample_index.jsonl --input p76_wiki_astronomy=data/candidates/p76_wiki_table_pairs_v5_astronomy/sample_index.jsonl --input p76_wiki_universities=data/candidates/p76_wiki_table_pairs_v5_universities/sample_index.jsonl --output-dir data/candidates/p76_batch_index_v3
uv run python scripts/report_p76_distribution.py --index data/candidates/p76_batch_index_v3/sample_index.jsonl --finance data/hf/LongWorld-Synthesis-Workspace/candidates/p64_finance_taskbank_v2 --codeforge data/hf/LongWorld-Synthesis-Workspace/candidates/p64_codeforge_taskbank_v2 --output data/candidates/p76_batch_index_v3/distribution.json
less -R data/candidates/p76_wiki_table_pairs_v5_astronomy/sample_index.jsonl
less -R data/candidates/p76_wiki_table_pairs_v5_astronomy/audit.jsonl
less -R data/candidates/p76_wiki_table_pairs_v5_astronomy/verification.json
less -R data/candidates/p76_wiki_table_pairs_v5_universities/sample_index.jsonl
less -R data/candidates/p76_wiki_table_pairs_v5_universities/verification.json
less -R data/candidates/p76_batch_index_v3/coverage.json
less -R data/candidates/p76_batch_index_v3/distribution.json
```

## Second source scout, common parser fix and dense scan

Five more explicit-title groups (museums, libraries, botanical gardens,
airports, hospitals) froze 17 additional unique pages. Only the hospital
group had a dense enough usable year table. Its first snapshot exposed a
shared parser error: repeated `Name | Location | Established/New building`
headers were treated as data rows, so facts below them inherited an older
`Established` header. This falsely inflated source support. The common
`structured_lines` parser now recognizes repeated headers, and the relation
checker stops at the nearest table header. The same hospital page revisions
were frozen as a new, content-bound v6 snapshot; extracted facts fell from
348 to 277. The v5 hospital snapshot and its initial closed-universe reject
remain diagnostics, not task inputs.

The corrected source screen excludes template debris in both the value and
subject cells. Across the canonical v4 groups, four nonhospital v5 groups,
and v6 hospitals, the pool has 13 source groups, 45 unique pages, 2,675
extracted facts and 432 clean structurally supported table units. The strict
hospital scan universe is smaller than the source screen: 81 rows have an
exact `Established` header, a plain four-digit year and a plain citation-free
name cell. It excludes ten format-mismatched rows, with the exclusion scope
stated in the question. The final-reader independent table parser and native
fact inventory match exactly on those 81 rows.

`scripts/export_p76_wiki_scan.py` compiled eight complete-table interval
tasks each for Japan national parks (35 eligible rows), England universities
(32), and Greece hospitals (81). Answer cardinalities span 2–15; every
eligible candidate, including nonmatches, is in the audit ledger. An inserted
same-schema hit changes the full answer; a near-miss insertion does not. The
final-input lengths are 55,563–55,668 tokens for parks, 17,517–17,573 for
universities and 48,639–48,755 for hospitals. These are dense scan/integration
tasks, **not** a claim that their candidate tables themselves span 48K/55K.
The three scan manifests set `train_ready=false`.

The assistant-only verifier checked all 24 scan rows: 1,184 exact proof-span
token mappings and 1,593 supervised tokens (parks 644, universities 355,
hospitals 594). Together with the 28 pair tasks, this wave adds **52
independent real Wiki tasks and 76 reader views**. Combining them with the
same historical P64/P66/P71/P73/P75 indexes yields 9,194 source rows, 9,064
distinct sample views and 9,040 independent tasks, with zero index rejects.
The 154 repeated task rows are still 130 P64/P66 CodeForge duplicate views
and 24 second-length astronomy views. Of the 9,040 tasks, 52 new ones have
four explicit topics: observatories 24, national parks 8, universities 12,
hospitals 8. Historical tasks with absent topic labels remain unknown. The
combined batch is an inventory, not a quality-filtered training export.

```bash
less -R data/capability_records/p76_wiki_titles_v5/source_screen_v2.json
less -R data/capability_records/p76_wiki_titles_v6/source_screen_v2.json
less -R data/candidates/p76_wiki_table_scan_v2_parks/sample_index.jsonl
less -R data/candidates/p76_wiki_table_scan_v2_universities/sample_index.jsonl
less -R data/candidates/p76_wiki_table_scan_v2_hospitals/sample_index.jsonl
less -R data/candidates/p76_batch_index_v5/coverage.json
less -R data/candidates/p76_batch_index_v5/distribution.json
```

## Actual source-native batch execution and resume

`configs/p76_source_batch_v1.json` pins five qualified jobs and the prior P75
source manifest by SHA, source snapshot ID, page revisions, domain/topic,
recipe, split and length policy. `scripts/run_p76_source_batch.py` checks
the actual frozen snapshots before worker launch, forms connected components
by page title and rejects opposite train/eval exposure including prior P75
sources. Each worker reads and tokenizes its own source; the coordinator
handles only metadata and bounded ProcessPool dispatch. Receipts bind config,
source, relevant code and every output file. Partial or failed jobs require a
fresh output directory; resume reuses only fully verified jobs.

The first real `--workers 2` run at `data/candidates/p76_source_batch_v1/`
produced five jobs from four source groups: 52 independent tasks and 76
candidate rows. Three jobs are train, two eval. A second fresh `--workers 1`
run produced byte-identical train/eval/index/audit/reject/manifest/receipt
files for all five jobs and the same batch manifest. An immediate resume and
another resume after **read-only** SFT-mask verification changed zero files.
The batch outputs are also byte-identical to the separately generated v5
pair and v2 scan candidates. The generated `inventory_inputs.json` fed the
unified index directly; its round-trip coverage is 9,194 source rows, 9,064
distinct sample views, 9,040 independent tasks and zero index rejects. No
GPU training or release promotion was run; the batch manifest says
`train_ready=false`.

Read-only mask verification across the five batch job directories found 1,288
exact proof-span/token mappings and 3,438 supervised tokens, with all file
hashes and assistant-only boundaries valid. These are loader-contract checks,
not independent natural-language reader performance.

```bash
uv run python scripts/run_p76_source_batch.py --config configs/p76_source_batch_v1.json --output-dir data/candidates/p76_source_batch_v1 --workers 2
uv run python scripts/run_p76_source_batch.py --config configs/p76_source_batch_v1.json --output-dir data/candidates/p76_source_batch_v1 --workers 2 --resume
less -R data/candidates/p76_source_batch_v1/batch_manifest.json
less -R data/candidates/p76_source_batch_v1/inventory_inputs.json
less -R data/candidates/p76_batch_index_v6_from_source_batch/coverage.json
```

As above, the first command requires a **new** output directory on rerun.
The present five-job batch proves deterministic orchestration and native
reader compilation. It is still far below the source-diverse, quality-filtered
scale gate for a model utility experiment.

## Research decisions for scaling

The source-native route follows [QwenLong-L1.5](https://arxiv.org/abs/2512.12967)
(separate KG, table and open-ended generators) and [π²](https://arxiv.org/abs/2604.05114)
(execute a hidden table query and check against visible natural context). A
realistic request style from [WildLong](https://arxiv.org/abs/2502.16684) does
not certify factual dependency; its intent co-occurrence can be superficial.
[DeepReasonQA/LongPAS](https://aclanthology.org/2026.findings-acl.1306/)
supports path-sampled Wiki candidates and staged filters, but sampled path
length is not a minimum reader-text proof, and its SFT result includes a
LongBench v2 regression. [Oolong](https://arxiv.org/abs/2511.02817) motivates
candidate-complete aggregation rather than sparse multi-needle lookup.
[LongFaith](https://arxiv.org/abs/2502.12583) suggests answer-grounded citation
generation after gold/support exists; final admission still needs a gold-blind
reader. [MIMG/LongMIT](https://arxiv.org/abs/2409.01893) motivates composing
checked local questions into multi-hop tasks. [LongCrafter](https://arxiv.org/abs/2607.06160)
motivates evidence-first task routing across abilities. The
[book hierarchy recipe](https://arxiv.org/abs/2504.12637) caches local/global
representations per real book, but its task count does not certify per-question
long-distance necessity. [NExtLong](https://arxiv.org/abs/2501.12766) and
[Re³Syn](https://aclanthology.org/2025.acl-long.1518/) inform coherent
concatenation and hard related negatives for midtraining, not reader QA truth.
[ProLong](https://arxiv.org/abs/2410.02660) informs short replay and broad
readout evaluation; no fixed replay percentage is imported into this SFT plan.

For the next production stage, plan immutable source groups, source-native
parsers and oracles, task support matrix, cheap execution/degeneracy checks,
natural question rendering, coherent 32K/64K/128K composition, exact final
chat token mapping, text interventions and independent gold-blind reader
checks. Source-connected-component splits must precede train/eval export.
Use P71's deterministic shards/receipts and P64's bounded tokenization as
patterns, with late materialization of long body text. One task normally gets
one primary length; paired lengths are diagnostic and keep the same task ID.
The previously documented >=100 worlds / >=1,000 accepted independent tasks
is a proposed utility-experiment gate, not a count achieved by this wave.

## Next production contracts, in dependency order

1. **Source planner and native parse caches.** Index revisions, raw text,
   row/column/unit/notes and explicit parse rejects once per source; partition
   by source-connected component, including duplicate page titles across
   collection versions. Use the eight title-bundle screens as the first
   routing data, not a topic quota. Add table-rich reports and papers through
   native adapters, rather than forcing them into Wiki infobox rules.
2. **Task planners per material.** For Wiki/knowledge pages, evidence graph
   joins and carefully closed list comparisons; for numeric reports, period,
   unit and qualifier-aligned SQL/Python aggregation; for repositories, native
   dependency/revision trace; for grounded mixed, real rule text plus clearly
   simulated runs/events. Keep natural and explicit-program question styles
   separately tagged. A simulated topic label is not a new domain mechanism.
3. **Validation before long materialization.** Execute the native oracle, reject
   constant intermediates and ambiguous scope, check row/sentence support and
   source repetition, then render one coherent long context. Cross-doc tasks
   require both source facts in a gold-blind reader replay; global-set tasks
   require a closed candidate ledger and added-hit/near-hit interventions;
   state tasks require effective/disclosure-time replay. Bounded deletion and
   shortcut probes identify strict vs integration vs retrieval profiles.
4. **Length and scale.** Cache document token lengths and document layout by
   source revision, then plan whole section/table/file composition for a
   primary 32K, 64K or 128K view; a small paired-view subset audits position.
   Count final chat tokens and every proof location after materialization;
   record `insufficient_source_capacity` rather than padding. Use deterministic
   shard seeds, per-shard content hashes, bounded in-flight rendering and
   resume receipts from existing P71/P64 patterns. Run a source-disjoint
   quality batch and publish the coverage cube plus rejects *before* choosing
   training weights.

Current support matrix: Finance supplies native lookup/delta/ratio/aggregate;
CodeForge supplies several file/CI/merge programs; P71 supplies simulated
locate/join/aggregate/state/rule tasks; P75 supplies real Wiki locate plus two
source comparisons; the new table-pair recipe adds another cross-document
comparison pattern. Books, papers, dense complete-set scans beyond the
closed plain-year-table interval recipe, grounded
rule+event hybrids and source-diverse 128K certified dependency have no
qualified P76 output yet. They are explicit expansion work, not hidden behind
the 8,596-task inventory total.

## Parameterized source-pool compilation, 2026-09-24

The five hand-written P76 jobs were a fixed execution pilot. The new
`scripts/run_source_pool_batch.py` reads a pinned pool of source groups and
requested operations, probes each source against native task builders, and
expands only supported cells into the existing isolated worker/receipt runner.
The same batch merges accepted rows, indexes and audits into one candidate
dataset, normalizes length bins from actual final-chat token counts, checks
task/answer/split collisions and records both unsupported source-operation
cells and final task rejects. The control plane is parameterized; its current
executable adapters are Wiki table lookup, complete-table interval scan and
cross-document founding-year comparison. Finance, CodeForge, simulated and
grounded-mixed adapters are **not yet** on this new runner.

The pool config is `configs/p76_source_pool_v2.json`. Eight additional topic
groups were frozen by `scripts/freeze_p76_expansion_pool.py` from explicit
Wikipedia title bundles. The first 23 new pages yielded zero supported jobs:
the shared parser lacked city/province/locality/opened columns and non-leading
subject-name columns. The rejected source screen was preserved. After changing
those common parser rules, new revision-pinned snapshots of zoos, railway
stations, power stations and stadiums entered the same pool. This is a concrete
source-to-task yield improvement, not a count of downloaded pages as tasks.
The v1 expansion still records the unproductive castles, lighthouses, dams,
archaeology and other pages; those need native table/section support rather
than more title substitution.

The current combined run is
`data/candidates/p76_source_pool_v8_stadiums/`. Its input source pool is
`data/capability_records/p76_wiki_expansion_v3/source_pool.json`, pinned by the
acquisition manifests in expansion v1–v3. It selected 19 jobs from 18 source
groups; 11 groups actually contributed final rows. There are 298 independent
semantic tasks and 351 candidate views across astronomy, biology, education,
energy, healthcare, nature, sports and transport. Three operations contribute:
table-cell lookup, closed-table interval scan and cross-document year
comparison. The 351 views occupy `<32K` 126, `32–<64K` 156, `64–<128K` 24,
and `128–<256K` 45 physical final-chat tokens. Twenty-nine tasks recur in more
than one source group; 24 astronomy comparisons have a second length view.
By unique semantic task, the operation counts are 214 table-cell lookups, 56
closed-table scans and 28 cross-document comparisons. The shortest-view
distribution is `<32K` 126, `32–<64K` 156 and `128–<256K` 16; all 24 64K
views are paired views of shorter tasks. The current mix is lookup-heavy and
does not meet the balanced-operation or multiple-domain-per-64K target.
The batch retained 25 rejected task candidates, 74 unsupported source-operation
cells and one supported but zero-yield botanical-garden job. These are separate
from the accepted count. Task-level rejection is now allowed without discarding
other accepted tasks in the job.

An independent review found that a table-cell mask alone left a same-line
subject+answer disclosure for some lookups. The new filter rejects that
obvious shortcut; it does not claim to find paraphrases, all aliases or every
equivalent source. The merged output remains `train_ready=false`. Its 128K
views are measured input lengths, not certified 128K evidence dependencies;
the cross-document comparison evidence spans and dense scans are separate
capabilities. There was no GPU run, training comparison, promotion or HF
publication.

Reproduce or inspect without touching frozen outputs:

```bash
uv run python scripts/run_source_pool_batch.py --config data/capability_records/p76_wiki_expansion_v3/source_pool.json --output-dir data/candidates/p76_source_pool_v8_stadiums --workers 2 --resume
uv run python scripts/verify_p75_real_reader.py data/candidates/p76_source_pool_v8_stadiums/merged --no-write
less -R data/candidates/p76_source_pool_v8_stadiums/plan.json
less -R data/candidates/p76_source_pool_v8_stadiums/result.json
less -R data/candidates/p76_source_pool_v8_stadiums/merged/manifest.json
less -R data/candidates/p76_source_pool_v8_stadiums/merged/sample_index.jsonl
```

The next scale work is a header-aware scout that predicts legal task capacity
before freezing hundreds of pages, plus native Finance/CodeForge/simulation
adapters under this **same** planning, reader-validation and reject contract.
The proposed 100-world/1,000-task quality batch and balanced multi-operation
distribution remain unmet; another lookup quota increase would inflate the
dominant L1 operation without solving those gaps.
