# P112 factorized world and grounded-rule candidate campaigns

Status: candidate-only, `train_ready=false`; no GPU training or model-gain claim.
This checkpoint expands already validated P86 shared-state mechanisms and P109
official-rule/simulated-state instances. It does not turn a vocabulary profile
into a new domain ontology or real source.

## What is parameterized

`scripts/p112_world_factor_campaign.py` takes a declarative matrix of base
record count, filter depth, world seed, and a list of domain schema profiles.
The P86 generator builds each base world once with four operations over shared
record IDs: `group_compare`, `filter_aggregate`, `asof_sum`, and
`asof_complete_set`. The profile compiler then maps record/event types and
entity/category/amount/target keys into laboratory, logistics, finance, or
software structured ledgers. The map is reversible and the final reader is
decoded and solved against the base executor. The visible header and question
use the mapped type and field names; both are checked against the map.

Each profile exposes the same underlying semantic task and gold answer.
`semantic_task_id` therefore uses base-world ID plus task ID across profiles;
the source-scoped view count is separately reported. Splits are assigned by
base seed, so profile views of one base world cannot cross train/eval. This
changes structured expression and source-world presentation, not the number
of operations or source-grounded topics. Category codes and memo IDs remain
synthetic. The final reader is an explicit JSON ledger/program task, not
natural report or agent feedback.

The new `scripts/p112_world_grounded_rules.py` starts from the pinned P109
support ledger: 44 RFC documents were reviewed there, but only two official
numeric lower-bound clauses were admitted. It applies 12 simulated state seeds
to each of those two clauses. The same observation IDs appear with different
visible values and answers. A source group must have identical ID lists and
at least two different answer hashes. Every admitted candidate must become
unresolved after deleting its official rule-support paragraph group, and must
change answer when a violating simulated observation is deleted. This
expands state instances, not RFC source count, topic count, or rule mechanisms.

## P112 v1 controlled pilot, verified

`data/candidates/p112_world_factor_campaign_v1/manifest.json` is a
`longworld.unified-candidates.v1` shard and passed exact `--verify-only` replay.
The six base P86 worlds all passed; four schema profiles produced 24 visible
world groups and 192 views. There are **48 global independent semantic tasks**,
not 192. All four operations have 48 views each. Domains/topics each have 48
views. Split is 128 train / 64 eval. Actual final-chat bins: 68 at 32–64K,
60 at 64–128K, and 64 at 128–256K. Six base worlds had zero rejects.

The independent all-row audit in
`data/candidates/p112_world_factor_mask_v1/manifest.json` passed 192/192
final Qwen3.5 chat templates and assistant-only masks: 15,592,414 final-chat
tokens and 44,700 supervised tokens. Its proof ledger contains 1,152
reader-visible fact-deletion probes, all answer-changing, and 192/192 bounded
consumed-fact envelopes at least 16K tokens (minimum 28,840; maximum 129,258).
These are observed spans for selected consumed facts, not globally shortest
proof distances; the task may admit unsearched alternate reasoning. Evidence
and answer metadata remain outside the model-visible reader messages.

## P112 expanded controlled batch

`configs/p112_world_factor_campaign_v2.json` uses 400/800/1600 records ×
depth 2/3 × four base seeds per cell, four schema profiles, and a bounded
four-process base-world builder. The resumed campaign completed 24/24 base
worlds and **192 global independent semantic tasks** across 96 presented
source groups and 768 reader views (608 train, 160 eval). Each of the four
operations has 192 views. Physical bins are 320 at 32–64K, 256 at 64–128K
and 192 at 128–256K. All base jobs were admitted; the declarative campaign
and its exact `--verify-only` replay passed.

The independent mask receipt at
`data/candidates/p112_world_factor_mask_v2/manifest.json` passed 768/768
final readers, with 68,115,289 full-chat and 193,392 supervised tokens.
Every row's selected consumed-fact envelope is at least 16K tokens and the
maximum observed envelope is 181,716. Those are bounded program lineage
spans, not globally shortest proof distances or natural-text necessity.
Only 24 base semantic worlds were added; the 96 presented groups include
four reversible domain-expression profiles for each base world.

## P112 official-rule state expansion, verified

Use `data/candidates/p112_world_grounded_rules_v2/manifest.json` (v1 is
historical). The unified shard has **2 real RFC rule sources / 24 independent
simulated-state tasks**, all train, zero rejects: 12 actual `<32K` views and
12 actual 64–128K views. The manifest records 24 rule-support group deletions,
24 violating-observation deletions, and same-ID/different-answer checks on
both source groups. The compiler's exact mask check and independent
`audit_unified_reader_mask.py --all` both passed 24/24; the latter has
1,212,553 final-chat and 2,005 supervised tokens. Its receipt is
`data/candidates/p112_world_grounded_rules_all_mask_v2/manifest.json`.
These documents remain local-research source material; source-rights review
and training promotion remain separate.

## Selection and next gate

The P111 balanced selector's existing controlled-simulation supervised-token
cap is almost saturated, so appending the P112 controlled shard may select
zero rows. Keep that budget fixed for a mechanism comparison: replace older
P81 controlled rows with P112 rows in matched operation and physical length
cells, matching total tasks and supervised tokens as closely as possible.
Report actual replaced source groups and global semantic-task IDs, then test
unseen domains and real-source benchmarks. Do not raise the simulation cap
merely to make a new candidate shard appear in a training pack.

Separate P112 lanes now pilot real books and reports, but this structured
campaign still lacks agentic feedback and additional validated real rule
clauses at scale. The last mile is model
learning evidence; exact masks, bounded interventions, and long spans alone
do not prove transfer.

Reproduce and inspect:

```bash
cat data/candidates/p112_world_factor_campaign_v1/manifest.json
cat data/candidates/p112_world_factor_mask_v1/manifest.json
cat data/candidates/p112_world_factor_campaign_v2/manifest.json
cat data/candidates/p112_world_factor_mask_v2/manifest.json
cat data/candidates/p112_world_grounded_rules_v2/manifest.json
cat data/candidates/p112_world_grounded_rules_all_mask_v2/manifest.json
less -R data/candidates/p112_world_factor_campaign_v1/proofs.jsonl
less -R data/candidates/p112_world_grounded_rules_v2/rejected.jsonl
UV_LINK_MODE=copy uv run --offline python -m unittest scripts.p112_world_factor_test -v
UV_LINK_MODE=copy uv run --offline python scripts/p112_world_factor_campaign.py --config configs/p112_world_factor_campaign_v1.json --output data/candidates/p112_world_factor_campaign_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p112_world_factor_mask.py --candidates data/candidates/p112_world_factor_campaign_v1 --output data/candidates/p112_world_factor_mask_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p112_world_factor_campaign.py --config configs/p112_world_factor_campaign_v2.json --output data/candidates/p112_world_factor_campaign_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p112_world_factor_mask.py --candidates data/candidates/p112_world_factor_campaign_v2 --output data/candidates/p112_world_factor_mask_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p112_world_grounded_rules.py --config configs/p112_world_grounded_rules_v1.json --output data/candidates/p112_world_grounded_rules_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py data/candidates/p112_world_grounded_rules_v2 --all --output data/candidates/p112_world_grounded_rules_all_mask_v2 --verify-only
```
