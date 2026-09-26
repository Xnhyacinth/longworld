# P149 shared-world reader candidate checkpoint

P149 extends the P145 sharded reader bank with P146's nine independently new
raw-TeX paper tasks. The bank reuses one immutable candidate interface across
real Wiki, books, filings, paper sources, code workflows and controlled state
worlds; P142/P147 two-step policy remains a separate training contract on
some of the same controlled source worlds. This is a candidate checkpoint,
`train_ready=false`, with no GPU model-gain claim.

| Unit | Full candidate bank | Strict selected reader set |
| --- | ---: | ---: |
| Views / independent semantic tasks | 15,835 / 14,655 | 2,742 / 2,742 |
| Typed source groups | 2,094 | 789 |
| Known multi-capability groups | 807 | 402 |
| Input / assistant-supervised tokens | 1,227,201,559 / 3,903,248 | 164,229,893 / 286,596 |
| Null dependency status | 7,722 views | 0 views |

The selected split is 1,996 train / 746 eval. Source mix: Wiki 853, books
669, finance 192, code 69, paper 29, controlled simulation 930. Actual
final-chat bins: 680 below 32K, 978 at 32–64K, 905 at 64–128K and 179 at
128–256K. The full bank has 34 domain and 183 topic labels; the selected set
has 31/168. These labels are not counts of independent world mechanisms.
The selected assistant supervision is 286,596 / 164,516,489 final-chat
tokens (0.1742%).

P144 scheduled two P133 campaigns over disjoint seed windows: 256 generated
worlds, 240 admitted complete worlds, 960 QA and 960 separate one-step
policy candidates. P149 selected 642 of those QA tasks, covering all 240
newly admitted worlds. P144's full byte replay checks cross-batch source
identity and keeps all 16 quarantined worlds in the rejection ledger.
The [P147 policy cross-audit](../data/candidates/p147_policy_cross_audit_v1/report.json)
separately replays 284 two-step action trajectories and 568 masked stage
rows from 84 distinct source worlds, with no source/split overlap; the second
policy stage receives an explicit observation.

P146 adds nine source-backed real-paper tasks from six frozen paper works,
eight train and one eval. Its largest reader is 169,899 final-chat tokens;
the two required text supports are 143,965 tokens apart, and the final
support ends 14,292 tokens before the query. All nine pass exact source
archive/reader bytes, unique gold, cue/answer leakage checks, bounded dual
reader-text deletion, final token-span and assistant-mask checks. They are
reference/target alignment tasks with explicit natural-prose cues, not a
broad claim about open-ended paper reasoning. Source redistribution status
remains local research only. The frozen P146 compiler also passed an
independent root-run `--verify-only` replay after integration.

The [P149 capability matrix](../data/candidates/p149_capability_coverage_v1/report.json)
shows the unresolved real long-context holes: of 325 selected complete-set
scan tasks, 239 are <32K and only 10 are at 128–256K; relational join has
two at 128–256K; paper reference trace now has two at 128–256K. The new
state worlds add no 128–256K tasks. The strict selection requires a nonempty
dependency status, yet 137 selected tasks explicitly retain formal program
lineage with visible alternatives unsearched. Physical length and status
presence are not unrestricted reader necessity proofs.

An automatic official-MediaWiki category route was also run independently as
[P148](p148_official_wiki_category_catalog.md). It froze 22 new pages, but
only two short structured-table tasks survived P122/P126. Those two are not
inserted into P149 because they do not repair the measured long-capability
gap; the source and rejection receipts remain available for a typed numeric
or linked-document compiler.

The [global source-component audit](../data/candidates/p149_source_component_audit_probe_v1/report.json)
maps 2,742/2,742 selected views to hash-pinned source identities with zero
unknowns and zero train/eval conflicts. The generic paper adapter accepted
P146 without a source-specific audit branch. The first final-reader
materialization checked all 2,742 rows under pinned Qwen chat-template bytes:
every row has positive assistant supervision,
`loss_mask_start == input_tokens`, and
`input_tokens + supervised_tokens == full_chat_tokens` (four to 858
supervised tokens per row). Final train/eval JSONL and sample index are
hash-pinned in
`data/candidates/p149_candidate_materialized_probe_v1/`; a full second
`--verify-only` byte replay also passed (exit 0).

Inspect and replay from the repository root:

```bash
cat data/candidates/p149_candidate_refs_probe_v1/manifest.json
cat data/candidates/p149_candidate_selection_probe_v1/manifest.json
cat data/candidates/p149_capability_coverage_v1/report.json
cat data/candidates/p149_source_component_audit_probe_v1/report.json
cat data/candidates/p149_candidate_materialized_probe_v1/mask_audit.json
less -R data/candidates/p149_candidate_materialized_probe_v1/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p149_candidate_refs_probe_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p149_candidate_selection_shared_v1.json --output data/candidates/p149_candidate_selection_probe_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python -m scripts.p138_capability_coverage --config configs/p149_capability_taxonomy_v1.json --output data/candidates/p149_capability_coverage_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p149_candidate_refs_probe_v1 --selection data/candidates/p149_candidate_selection_probe_v1 --selection-config configs/p149_candidate_selection_shared_v1.json --output data/candidates/p149_candidate_materialized_probe_v1 --verify-only
```
