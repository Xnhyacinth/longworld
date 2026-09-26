# P152 unified reader candidate checkpoint

P152 appends P148's two strict HTML categorical scans and P150's seven
numeric interval row-count tasks to the P149 reader bank. Both task routes
reuse P148's frozen official-MediaWiki category intake and P122 complete HTML
grids; one page supports both operations with identical source/split metadata.
This is candidate-only research data (`train_ready=false`), with no GPU
training or model-gain claim.

| Unit | Full candidate bank | Strict selected reader set |
| --- | ---: | ---: |
| Views / independent semantic tasks | 15,844 / 14,664 | 2,750 / 2,750 |
| Typed source groups | 2,098 | 793 |
| Groups with >1 operation label | 850 | 445 |
| Known multi-capability groups | 807 | 402 |
| Input / assistant-supervised tokens | 1,227,283,487 / 3,903,451 | 164,274,333 / 286,791 |
| Null dependency status | 7,722 views | 0 views |

The selected split is 2,004 train / 746 eval. Source kinds are Wiki 861,
books 669, finance 192, code 69, paper 29 and controlled simulation 930.
Final-chat bins: 689 below 32K, 977 at 32–64K, 905 at 64–128K and 179 at
128–256K. The selected assistant-supervised fraction is 0.1743% of
164,561,124 final-chat tokens. Full-bank domain/topic labels are 35/186;
selected labels 32/171. P148's `wiki_list_index` is a routing label, not a
new semantic domain. Labels and operation names are not independent mechanism
counts.

The 179 samples in the 128–256K bin are 114 real-book, 34 finance, 17 Wiki,
12 code and two paper readers. The controlled-state worlds have no selected
sample in that bin. The selected capability audit counts 492 cross-chapter
traces, 334 complete-set scans, 307 aggregate/compare tasks and 28 paper
reference traces, among other families. These are task counts rather than
evidence that every family has equal long-range or semantic difficulty.
Of 402 selected source groups with more than one known capability, 301 are
controlled simulations; the real groups are 71 books, 19 Wiki, six finance
and five code. No selected paper source currently spans two known capability
families. This is the main shared-real-world gap despite the larger task bank.

P148 automatically screened official category branches: 243 discovered root
candidates, 175 eligible roots, 12 scheduled roots, 22/22 new pages frozen.
The strict funnel was 73 gross tables → 26 valid grids → two P126 tasks from
one source page. P150 then compiled seven tasks from four source pages with
plain-integer column values, row/header/unit and original HTML-cell spans;
all 556 source rows and 21 reader hit/removal/near-miss edits passed its
independent audit. The two routes add nine short tasks, and the selected
increase is eight because balancing replaces one prior Wiki view. **Every
P148/P150 task is below 32K**. The automation has removed manual category
enumeration but has not solved real long-document task yield.

P144 and P146 remain as in the [P149 checkpoint](p149_integrated_candidate_status.md):
240 newly admitted executed state worlds, 960 source-distinct reader QA and
separate policy candidates; nine new real-paper reference tasks, including
one 169,899-token sample with a bounded 143,965-token evidence gap. P147
two-step policy traces stay outside the reader bank. The capability matrix
still shows just ten selected 128–256K complete-set scans, two 128–256K
paper reference traces, and no 128–256K state aggregate/complete-set tasks.
The selected set also retains 137 formal-lineage tasks whose visible
alternatives have not been searched.

The [source-component audit](../data/candidates/p152_source_component_audit_probe_v1/report.json)
maps 2,750/2,750 selected views to pinned underlying identities with zero
unknowns or train/eval conflicts. It includes the new P148 source pool and
the generic P150/P126 native shard bindings. Source identity, bounded
task-specific interventions and exact masks are separate evidence scopes;
none proves training transfer or unrestricted reader necessity.

The first full materialization completed with 2,750/2,750 positive
assistant-only masks under the pinned Qwen/Qwen3.5-4B tokenizer revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`.
Its manifest SHA-256 is
`894537acaaf6e5b4b71c57dcdece1e7f417ee2731d2b420092ab57a06a2d3b50`.
The independent full-byte `--verify-only` replay also completed with exit
zero and reproduced the manifest and all four materialized files. This
checkpoint is still not marked train-ready.

Inspect and replay:

```bash
cat data/candidates/p152_candidate_refs_probe_v1/manifest.json
cat data/candidates/p152_candidate_selection_probe_v1/manifest.json
cat data/candidates/p152_capability_coverage_v1/report.json
cat data/candidates/p152_source_component_audit_probe_v1/report.json
cat data/candidates/p152_candidate_materialized_probe_v1/mask_audit.json
less -R data/candidates/p152_candidate_materialized_probe_v1/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p152_candidate_refs_probe_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p152_candidate_selection_shared_v1.json --output data/candidates/p152_candidate_selection_probe_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python -m scripts.p138_capability_coverage --config configs/p152_capability_taxonomy_v1.json --output data/candidates/p152_capability_coverage_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p114_source_component_audit.py --index data/candidates/p152_candidate_refs_probe_v1 --selection data/candidates/p152_candidate_selection_probe_v1 --book-source data/sources/p113_book_cohort_v6 --book-source data/sources/p114_book_cohort_v2 --book-source data/sources/p120_book_cohort_v1 --book-source data/sources/p124_book_cohort_v1 --book-source data/sources/p136_book_cohort_v1 --extended-source-kinds --p118-code-and-simulation --wiki-source-pool data/candidates/p117_wiki_shape_intake_v9/source_pool.json --wiki-source-pool data/candidates/p119_wiki_structural_intake_v2/source_pool.json --wiki-source-pool data/candidates/p148_wiki_category_catalog_v1/p119/source_pool.json --output data/candidates/p152_source_component_audit_probe_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p152_candidate_refs_probe_v1 --selection data/candidates/p152_candidate_selection_probe_v1 --selection-config configs/p152_candidate_selection_shared_v1.json --output data/candidates/p152_candidate_materialized_probe_v1 --verify-only
```
