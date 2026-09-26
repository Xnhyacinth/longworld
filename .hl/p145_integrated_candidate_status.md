# P145 larger unified reader candidate checkpoint

P145 extends the frozen P139 sharded reader bank with two P144 candidate-only
reader shards. The two producer batches made 256 distinct generated source
worlds, admitted 240 complete worlds under P133's existing selected event
deletion and >=16K witness-gap gates, and exported 960 new reader QA tasks.
P144's 960 one-step policy candidates and P147's two-step policy candidates
remain separate training contracts. No GPU training or model gain is claimed.
The separate [P147 policy audit](../data/candidates/p147_policy_cross_audit_v1/report.json)
checks 250 new two-step trajectories plus the 34-trajectory P142 pilot, with
284/284 branch/feedback replays and 568/568 masked stage rows. The second
policy stage receives an explicit observation, so it is not a second remote
retrieval task.

| Unit | Full candidate bank | Strict balanced reader selection |
| --- | ---: | ---: |
| Views / independent semantic tasks | 15,826 / 14,646 | 2,733 / 2,733 |
| Typed source groups | 2,091 | 786 |
| Known multi-capability groups | 807 | 402 |
| Input / supervised tokens | 1,226,721,459 / 3,903,055 | 163,749,793 / 286,403 |
| Null dependency status | 7,722 views | 0 views |

The selection contains 1,988 train and 745 eval tasks. Source kinds:
controlled simulation 930, real Wiki 853, books 669, finance 192, code 69
and paper source 20. Actual final-chat length bins are 679 below 32K, 972 at
32–64K, 904 at 64–128K and 178 at 128–256K. The full bank has 34 domain
and 180 topic labels; the selection has 31/165. These are labels rather than
independent mechanisms. The supervised fraction is 286,403 / 164,036,196
final-chat tokens = 0.1746%.

P145 selected 642/960 P144 QA tasks, covering **all 240** newly admitted
worlds. Across the selected reader bank, 802 tasks use the two P133/P144
native transition mechanisms (partial reversal 426, authorization hold 376).
The global source-component audit maps 2,733/2,733 selected views to frozen
underlying identities with zero unknowns and zero train/eval conflicts.
P144 itself checks cross-batch seed, world ID, base-record context and task
ID uniqueness, and both campaigns/reader shards passed full byte replay.

The [capability × source kind × actual length matrix](../data/candidates/p145_capability_coverage_v1/report.json)
prevents physical length totals from masking ability gaps. P144 adds only
<128K synthetic state tasks; the selected 128–256K count stays at 178.
Real long complete-set and paper-reference coverage therefore remain scarce.
Furthermore, 137 selected tasks still explicitly have
`formal_program_lineage_only; visible_alternatives_unsearched` status.
The nonempty-status selection gate does not certify full reader necessity.

The first final-reader materialization checked all 2,733 rows under the pinned
Qwen chat template: every row has a positive assistant target,
`loss_mask_start == input_tokens`, and
`input_tokens + supervised_tokens == full_chat_tokens` (minimum four,
maximum 858 supervised tokens per row). The final train/eval JSONL and sample
index are hash-pinned; the second byte-for-byte verify-only replay is a
separate reproducibility check.

All reader rows remain local candidates with `train_ready=false`. The
[P145 source audit](../data/candidates/p145_source_component_audit_probe_v1/report.json)
proves source split accounting, not semantic support for every task. The
training-product decision remains a separate gate.

Inspect/replay:

```bash
cat data/candidates/p145_candidate_refs_probe_v1/manifest.json
cat data/candidates/p145_candidate_selection_probe_v1/manifest.json
cat data/candidates/p145_capability_coverage_v1/report.json
cat data/candidates/p145_source_component_audit_probe_v1/report.json
cat data/candidates/p145_candidate_materialized_probe_v1/mask_audit.json
less -R data/candidates/p145_candidate_materialized_probe_v1/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p145_candidate_refs_probe_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p145_candidate_selection_shared_v1.json --output data/candidates/p145_candidate_selection_probe_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python -m scripts.p138_capability_coverage --config configs/p145_capability_taxonomy_v1.json --output data/candidates/p145_capability_coverage_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p145_candidate_refs_probe_v1 --selection data/candidates/p145_candidate_selection_probe_v1 --selection-config configs/p145_candidate_selection_shared_v1.json --output data/candidates/p145_candidate_materialized_probe_v1 --verify-only
```
