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
The first raw extraction yielded 2,304 facts, but the source screen found only 279
table facts with both structural row/column support and no obvious template
debris. The usable units cluster in astronomy (133), national parks (101)
and universities (45); bridge values retain template junk, while mountain and
museum pages produce no admitted clean table units under the current parser.
Even the 279 figure is a source-routing diagnostic, **not** 279 tasks or a
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
as independent sources. The v4 screen reports 2,308 facts and 283 clean
structurally supported table facts; university contributes 38 clean units
(32 England years, four Wales years and two locations).

```bash
uv run python scripts/screen_p76_wiki_sources.py --snapshots-dir data/capability_records/p76_wiki_titles_v4 --output data/capability_records/p76_wiki_titles_v4/source_screen.json
less -R data/capability_records/p76_wiki_titles_v4/source_screen.json
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
comparison pattern. Books, papers, true dense complete-set scans, grounded
rule+event hybrids and source-diverse 128K certified dependency have no
qualified P76 output yet. They are explicit expansion work, not hidden behind
the 8,596-task inventory total.
