# P139 integrated reader candidate checkpoint

P139 extends one immutable sharded reader bank with the 23 new P131 Wiki
table tasks, 12 P132 review-to-final-diff code tasks, 244 P133 executed-state
reader QA tasks, and 240 P136 book tasks. The P133 policy candidates remain a
separate one-step action contract on the same source worlds. All artifacts are
local research candidates with `train_ready=false`; no model gain or release
rights are certified by these checks.

| Unit | Full candidate bank | Strict balanced selection |
| --- | ---: | ---: |
| Views / independent semantic tasks | 14,866 / 13,686 | 1,883 / 1,883 |
| Typed source groups | 1,851 | 531 |
| Known multi-capability groups | 567 | 161 |
| Input / assistant-supervised tokens | 1,171,338,487 / 3,648,539 | 118,781,242 / 114,811 |
| Null dependency status | 7,722 views | 0 views |

The selected split is 1,386 train / 497 eval. Source kinds are Wiki 719,
books 597, finance filings 192, real code workflows 69, controlled simulation
286, and paper source 20. There are 514 views below 32K, 594 at 32–64K, 597
at 64–128K, and 178 at 128–256K **final-chat tokens**. The selected set has
30 domain and 161 topic labels (the full bank has 34 and 180); those are
metadata coverage, not independent semantic mechanisms. Selected typed source
groups are 241 books, 173 Wiki snapshots, 93 controlled worlds, nine paper
sources, eight filing issuers and seven code repositories. The
assistant-supervised fraction is 114,811 /
118,896,053 = 0.0966% of final-chat tokens.

P136's second catalog window attempted 180 distinct works and froze 73 books
after source/size checks. Its two established compilers produced 240
independent candidate tasks from 61 productive books; 19 books support both
operations. P139 selected 146 P136 tasks from all 61 productive books. This
extends source breadth and same-world task reuse, but it remains one literature
domain with two question mechanisms. P133 froze 64 generated worlds, admitted
61 complete worlds under its event-dependency gate, and exported 244 reader QA
plus 244 policy candidates. P139 selected 174 of those reader QA tasks; the
native mechanism counts are 94 partial reversal and 80 authorization hold.

The versioned [capability matrix](../data/candidates/p139_capability_coverage_v1/report.json)
classifies source/operation routes without treating dynamic operation labels as
new mechanisms. It excludes P125 composite reuse from multi-capability
inflation and retains unknown routes explicitly. Selected known multi-capability
groups comprise 71 book, 60 controlled-simulation, 19 Wiki, six finance and
five code groups. The matrix exposes an important coverage gap: complete-set
scan has 306 tasks, but 220 are below 32K and only 10 are at 128–256K;
state aggregate/complete-set has no 128–256K tasks. Paper reference trace has
only 19 selected tasks. These cells need actual source/evidence expansion,
not extra topic names or padding.

The global [source-component audit](../data/candidates/p139_source_component_audit_probe_v1/report.json)
maps all 1,883 selected views to hash-pinned underlying identities with zero
unknowns and zero train/eval conflicts. It includes all five book cohorts,
exact Wiki snapshots, filings, raw paper sources, CodeForge banks, and P133
world/receipt/base-record identities. This proves source-boundary accounting,
not complete semantic support or shortest reader proof. The P132 code gate
checks a frozen positive review/comment-to-final-diff intervention certificate;
P133 native QA has bounded selected event deletion and >=16K witness-to-query
gaps. These bounded scopes must not be generalized to every candidate.

The strict selection requires nonempty `dependency_status`, but the field's
presence is only a filter. It removed 6,992 otherwise eligible candidate
views with missing status; it does not elevate weak statuses to reader
necessity. In the selected set, 137 tasks explicitly retain
`formal_program_lineage_only; visible_alternatives_unsearched`. A broader
P137 v2 review set has 1,814 materialized reader views
and 292 null statuses. This P139 selection is the primary candidate for
further reader evaluation and training-package review, subject to gold-blind
tests, rights/source policy, supervised-token balance and actual model gains.

The [materialized reader/mask audit](../data/candidates/p139_candidate_materialized_probe_v1/mask_audit.json)
has 1,883 checked final-chat rows under the pinned Qwen chat template. Every
row has a positive assistant target, `loss_mask_start == input_tokens`, and
`input_tokens + supervised_tokens == full_chat_tokens`; the final train/eval
JSONL and sample index are hash-pinned in its manifest. The full second
byte-for-byte `--verify-only` replay passed (exit 0).

Inspect and reproduce from the repository root:

```bash
cat data/candidates/p139_candidate_refs_probe_v1/manifest.json
cat data/candidates/p139_candidate_selection_probe_v1/manifest.json
cat data/candidates/p139_source_component_audit_probe_v1/report.json
cat data/candidates/p139_capability_coverage_v1/report.json
cat data/candidates/p139_candidate_materialized_probe_v1/mask_audit.json
less -R data/candidates/p139_candidate_materialized_probe_v1/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p139_candidate_refs_probe_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p139_candidate_selection_shared_v1.json --output data/candidates/p139_candidate_selection_probe_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p114_source_component_audit.py --index data/candidates/p139_candidate_refs_probe_v1 --selection data/candidates/p139_candidate_selection_probe_v1 --book-source data/sources/p113_book_cohort_v6 --book-source data/sources/p114_book_cohort_v2 --book-source data/sources/p120_book_cohort_v1 --book-source data/sources/p124_book_cohort_v1 --book-source data/sources/p136_book_cohort_v1 --extended-source-kinds --p118-code-and-simulation --wiki-source-pool data/candidates/p117_wiki_shape_intake_v9/source_pool.json --wiki-source-pool data/candidates/p119_wiki_structural_intake_v2/source_pool.json --output data/candidates/p139_source_component_audit_probe_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python -m scripts.p138_capability_coverage --config configs/p139_capability_taxonomy_v1.json --output data/candidates/p139_capability_coverage_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p139_candidate_refs_probe_v1 --selection data/candidates/p139_candidate_selection_probe_v1 --selection-config configs/p139_candidate_selection_shared_v1.json --output data/candidates/p139_candidate_materialized_probe_v1 --verify-only
```
