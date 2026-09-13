# Multi-QA curriculum v2 execution

The user prioritized synthesis, pipeline, world and domain expansion; model
endpoint evaluation is deferred and is not a data-generation prerequisite.

## Delivered mechanisms

- Actual OpenAlex snapshot: 4 domains / 26 fields / 252 subfields / 4,516 topics,
  with saved responses, complete-pagination and unique-parent validation.
- Deterministic domain/field-balanced selection: 16 topics in 16 fields and
  16 subfields, across all four domains. These are taxonomy labels, not validated
  domain-specific physics. Topic changes alone do not alter semantic world ID.
- Four distinct mechanisms: conditional ledger, reservation/release, contextual
  affine rule induction, and chained tool processing with failure recovery.
- Sixteen questions per context, native JSON answers, isolated factual/CF rows,
  one assistant target per group. Truth is generated separately from the
  visible-input solver. Full tool receipts are outside model messages.
- Three input-long capacity bands, complete-answer assistant-only preprocessing,
  bounded process pool, immutable code/config/tokenizer binding, atomic receipts,
  and verified reuse of complete shards.

Source and entry points are described in `docs/CAPABILITY_CURRICULUM.md`.

## Actual batch

```bash
uv run --no-sync python scripts/run_capability_curriculum.py \
  --config configs/capability_curriculum_v2.json \
  --output data/capability_curricula/20260913_v2
```

Four generation worker PIDs: 570344, 570347, 570348, 570349. All 192 shards
completed with zero rejects. The batch contains **384 SFT rows / 6,144 QA
instances**, with 288 train rows / 96 eval rows (4,608 / 1,536 QA instances).

| Capacity | Rows | QA instances | Input tokens | Full chat tokens |
| --- | ---: | ---: | ---: | ---: |
| 65,536 | 128 | 2,048 | 60,290–62,813 | 62,676–63,709 |
| 131,072 | 128 | 2,048 | 121,238–126,444 | 126,480–127,842 |
| 262,144 | 128 | 2,048 | 243,365–254,209 | 254,219–255,714 |

Each family contributes 96 rows / 1,536 QA instances. There are 64 world seed
groups, 192 length/workload world variants, and 384 factual/CF contexts. The
rendered 6,144 QA instances are not 6,144 independent worlds. The same underlying
world seed appears at three lengths, with factual and intervened histories.

Train/eval topic sets contain 12/4 topics and world seed sets contain 48/16 seeds;
both are disjoint. Affine coefficient-instance overlap is zero; both splits
still share the affine structural family. This is not held-out-family transfer.

Full chat tokens: **56,974,125**. Assistant-supervised tokens: **982,213**, or
**1.724%**. The previous pilot had 1,354 / 16,096,348 (0.00841%). Different tasks
and answer structures contribute to that change, so it is not solely an effect
of multi-query packaging and is not a training-gain measurement.

## Controlled packaging measurement

For one factual 128K world from each family, keep the same 16 questions/answers
and change only questions per row: 1 / 4 / 8 / 16. Exact pinned-tokenizer results
are in `capability_curriculum_v2_packaging_20260913.json`.

Across the four worlds, 16 separate rows consume 7,925,572 full-chat tokens;
four rows containing 16 questions each consume 508,927: a **93.58% reduction**
in serialized training sequence tokens (15.57x ratio). Answers are unchanged;
assistant terminators account for the small supervised-token count difference.
No GPU training throughput or quality improvement is inferred from this result.

## Review and checks

The focused suite has **77 passing tests**; Ruff checks pass. Regressions cover
native-range counterfactuals, prompt/gold separation, independent oracles, hidden
tool-result leakage, novel-rule identifiability, topic-independent world IDs,
seed-free visible question IDs, and rehashed forged assistant answers.

Independent review initially found topic-dependent IDs without changed physics,
out-of-range rule corrections, future fault disclosure, shared solver/oracle
code, and visible seed/view IDs. These were repaired before batch generation.

Independent stratified output review checked 12 shards (four families x three
bands): all hashes, validators, **384 actual exported QA answers**, re-rendered
input/full lengths and assistant masks passed. A full structural review confirmed
all row IDs, family/band counts, topic/seed isolation and zero coefficient overlap.
Manifest SHA-256: `802c94b672f63e270627276b88e75d85d506592cfc77c0e25009a46442473f18`.

The complete-shard resume command revalidated all 192 shards and returned exit 0
without changing the manifest hash:

```bash
uv run --no-sync python scripts/run_capability_curriculum.py \
  --config configs/capability_curriculum_v2.json \
  --output data/capability_curricula/20260913_v2 --resume
```

Log: `capability_curriculum_v2_resume_20260913.log`. Partial unreceipted shards
require a fresh directory; this implementation never discards them automatically.

Full visible-answer, oracle-compact and paired bounded-window audit **passed**:
`capability_curriculum_v2_audit_20260913.json`, SHA-256
`4502333c13787d2e2423c0e020fcfaba07ea1a35293620658f3bb3e8f33f52af`.
All **6,144/6,144 actual assistant answers** match the independent visible solver.
Ledger/reservation privileged compact controls pass 3,072/3,072 queries.

Of 3,072 factual/CF question pairs, 694 answers change and 2,378 remain unchanged.
There are **665 pairs with identical 16K suffix observations and different answers**,
and 334 with identical 16K prefix observations and different answers. Identical
questions are checked first. These certify insufficiency of those particular
window-only protocols; prefix/suffix counts overlap and must not be added as
unique tasks. The receipt also records 4K and 8K results. No arbitrary-window or
retrieval impossibility is claimed, and no historical strict flag is promoted.

## Remaining scope

L4 now has a unique context-inferred rule and independently verified targets.
Workflow supervision now has actual simulated tool transitions and cross-job
dependencies, but it is bounded causal replay rather than model-driven sustained
execution. Domain-specific natural prose, wider transition graphs, legal unknown
tasks, more rule families, arbitrary retrieval controls and measured SFT transfer
remain further work. No historical strict, framework-release or production gate
was relaxed. The evaluation CLI is ready, but no model calls were made because
the user explicitly prioritized data production.
