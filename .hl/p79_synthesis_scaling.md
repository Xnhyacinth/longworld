# P77–P79 unified long-context synthesis

Date: 2026-09-24. Status: **candidate bank complete; train_ready=false**.
No GPU training or release promotion was run.

## Outcome and accounting

Canonical batch: `data/candidates/p79_unified_with_concat_v1/`.
Its `manifest.json`, `merged/manifest.json`, `coverage.json`, source-lane
receipts and plan are the accounting authority. P79 appended only the new Wiki
concat lane to the verified P78 bank; it copied P78 reader bytes and checked
their hashes rather than re-tokenizing the 1,682 Finance rows.

| Source lane | Reader views | Active source/world groups | Native semantics |
| --- | ---: | ---: | --- |
| Wiki real documents | 513 | 20 | 391 lookup, 70 closed-table scan, 52 two-row comparison |
| Finance real filings | 1,682 | 8 issuers | 11 native program families; period/unit/basis/source pins retained |
| CodeForge real workflows | 817 | 7 repositories | native repository/CI/file programs; one over-window candidate rejected |
| Controlled record simulation | 288 | 72 worlds | seven native families, fresh P77/P78 seeds |
| **Total** | **3,300** | **107** | **3,248 distinct `(source_kind, semantic_task_id)`** |

The bank spans 14 recorded domains, 34 topic labels and 27 operation labels.
Twenty source groups have more than one operation. Split is 2,534 train and
766 eval candidate views. The three P79 concat rows are longer views of tasks
already in P78, so P79 added **zero** independent semantic tasks. Counts of
rows, source-scoped tasks (3,276), global semantic tasks (3,248), and source
groups are deliberately separate. Topic labels include issuer/repository names
and `unknown` for simulation; label count is not a semantic novelty measure.

Final pinned-chat message lengths: `[0,32K):368`, `[32K,64K):424`,
`[64K,128K):1,524`, `[128K,256K):984`; minimum 2,844 and maximum 261,954
tokens. These are observed lengths, not target labels or certified evidence
distance. The `<32K` group includes 90 views below 16K; 63 views are at
least 256,000 tokens while remaining inside the 262,144-token final-chat cap.

## What scaled and what did not

The shared scheduler reads one source plan, invokes Wiki/Finance/CodeForge/
simulation native compilers, checks source hashes and native receipts, then
normalizes final reader messages, answers, split, token mask and physical
length. Finance, CodeForge and simulation keep their own executable oracle.
The unified merger rejects duplicate sample IDs and cross-split or
answer-changing semantic tasks. P79's append path copies a verified prior bank
and processes only newly added lanes. `adoption_mode` states whether the
control-plane run created the native output or reverified an existing output;
`declared_generation_cohort` identifies the data wave. Neither is a claim of
novel task semantics relative to old P64/P71 inventories.

Topic-seed Wiki intake froze 17 new groups/34 pages; four groups supported the
current compiler, giving 87 real lookup tasks. This is source acquisition
without manual page or per-topic question functions, but only 4/17 groups were
productive and no new scan/pair operation emerged. The safe concat pair of
seven original documents yielded three 87.5K-token lookup views; an earlier
experimental combination was excluded after a prior train/eval page overlap
was detected. Concat increased physical length, not cross-document
dependency.

One separately tested two-document bind→lookup recipe found one valid task
across 18 frozen Wiki snapshots. It remains a research probe outside P79:
the source pool lacks enough complementary documents to make that recipe a
scalable production family. The measured result supports routing/fetching
connected documents next, not labeling ordinary concatenation as multi-hop.

## Quality and next gate

The bank is **reader-integrity verified**, not a promoted training set.
Wiki has bounded table-parser evidence and intervention checks; simulated
worlds have native solver rechecks. Finance's 1,682 current tasks all carry
`strict_long_dependency_verified=false`; CodeForge has native program replay
but no general final-text necessity certificate. Physical 128K/256K context
does not imply a necessary long evidence path. Before training selection,
separate source/answer correctness, bounded text necessity, alternative
support search and position/distance tests in an explicit admission profile.
The next source router should pair pages with shared linked entities and
complementary attributes, while table-rich sources should be selected for
scan/aggregation instead of padding weak lookup pages.

Finance and CodeForge native wrappers are fail-closed but currently cannot
resume an interrupted partial issuer/repository batch in place; the control
plane reports that state and requires a new output path. Per-job restart is a
remaining throughput improvement before much larger catalogs are attempted.

No P71/P72/P73 frozen artifact was overwritten. All new source and candidate
outputs live under distinct P77–P79 paths.

## Inspect and replay

```bash
cat data/candidates/p79_unified_with_concat_v1/manifest.json
cat data/candidates/p79_unified_with_concat_v1/merged/manifest.json
cat data/candidates/p79_unified_with_concat_v1/coverage.json
less -R data/candidates/p79_unified_with_concat_v1/merged/sample_index.jsonl
cat data/candidates/p78_wiki_topic_discovery_broad_v1/result.json
cat data/candidates/p78_wiki_concat_pair_pool_v1/result.json
```

The canonical output is a **candidate** `merged/candidate_train.jsonl`,
`merged/candidate_eval.jsonl`, and `merged/sample_index.jsonl`; each source
lane preserves its original audit and receipt files. Resume verification:

```bash
uv run python scripts/run_unified_synthesis_batch.py \
  --config configs/p79_unified_with_concat_v1.json \
  --output data/candidates/p79_unified_with_concat_v1 \
  --base-batch data/candidates/p78_unified_expansion_v1 --workers 2 --resume
uv run python scripts/report_unified_synthesis_coverage.py \
  data/candidates/p79_unified_with_concat_v1/merged \
  --output data/candidates/p79_unified_with_concat_v1/coverage.json
```
