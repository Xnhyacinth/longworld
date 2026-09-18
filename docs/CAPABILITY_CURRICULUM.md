# Capability curriculum production

The v3 pipeline produces SFT messages under two answer contracts, chosen per
family. Joint families (ledger, reservation, rule_learning) keep one visible
context, 16 tail questions, and one assistant JSON object containing all 16
answers. The workflow family is split: one supervised question per row, each
row carrying its own context prefix and an assistant message with exactly that
one answer. Gold and private control contexts are excluded from the user
message. This is not a CPT-only export.

The split exists because the workflow answers are one reversible value chain:
`value = ((prior + payload) * multiplier) % 100003` with `depends_on` the
preceding record, so in a joint row a decoder can read every later answer off
its predecessor plus that partition's records without performing the long
read. Reordering the JSON keys does not change that. The leak was measured
before the fix: 1080 of 1080 workflow answers were derivable that way, and a
probe over the exported bank reproduces it on demand (see
`tests/test_capability_curriculum_leak.py`). Splitting all four families was
measured and rejected: 16 scalar rows per world give 3.34% shape uniqueness,
below the 0.10 collapse floor the repo gates on.

Every row records `questions_per_row`, `answer_dependency_mode`
(`independent` or `dependency_given`) and, for split rows,
`supervised_question_id`. `dependency_given` is a label, never a hidden
property: the joint workflow question is still available and is still
generated, it is simply not what the production export uses.

The change bumps the row schema to `longworld.multiqa-curriculum.v3` and moves
the world identity (the packaging is part of it), so shards written by the v2
schema fail loudly on fingerprint and resume. **A fresh output directory is
required.**

## Sampling contract

`configs/capability_curriculum_v2.json` pins the full OpenAlex topic snapshot.
The actual snapshot has 4 domains, 26 fields, 252 subfields and 4,516 topics;
the hierarchy follows [OpenAlex's topic contract](https://help.openalex.org/data/topics/).
`scripts/fetch_capability_topics.py` records responses and retrieval provenance,
rejects duplicate topics, conflicting parents and incomplete pagination.

The sampler selects domains equally, rotates across shuffled fields within each
domain, and samples topics with a fixed seed. Topic IDs are held out between
train and evaluation; every length and counterfactual of a world seed stays in
one split. Seeds and world identities never appear in the visible question IDs.

Taxonomy coverage is metadata coverage. These generic symbolic mechanisms do not
yet establish domain-specific scientific realism or natural-language diversity.
Different topic labels alone never count as different semantic world families.

## Executable families and QA

| Family | World transitions | Questions and gold | Packaging |
| --- | --- | --- | --- |
| ledger | approved credits, conditional debits and distant event prerequisites | per-entity as-of balances, aggregates, ordinal memo recall and applied-action sets | joint, 16 per row |
| reservation | supply, conditional reservation, release and idempotent cancellation | free capacity, supplied quantity, ordinal records and active reservation sets | joint, 16 per row |
| rule_learning | novel affine rule inferred from demonstrations; distributed feature increments | classify each entity after integrating its updates, using a separately generated coefficient oracle | joint, 16 per row |
| workflow | chained job outputs; tool inspect/process failure, repair and commit | frozen-prefix endpoint value, recovery count and next action under a fixed policy | split, 1 per row |

The ledger/reservation oracle uses the existing event simulator; the visible
interpreter reconstructs state independently. Rule-learning truth uses generator
coefficients while the reader infers the unique rule from demonstrations. Workflow
truth executes tool transitions; its reader uses a separate arithmetic/transition
implementation. Future recovery traces remain outside model input.

The workflow family is **L3 state replay with a fixed policy plus offline
next-action prediction**, not closed-loop execution. `execute_job` takes its
next action from a module constant table keyed by state alone
(`new -> inspect -> process -> repair -> ...`), and three of the four answer
fields are local lookups on the last visible record; only `final_value` needs
the chain replayed. The lineage says so explicitly
(`execution_mode: offline_replay_with_fixed_policy`, `closed_loop: false`,
`fixed_policy` the table itself), and the capability tag is
`L3_state_replay_offline_action_prediction`. No step of the pipeline asks a
model to choose an action, and no measurement of model-driven control exists
here.

Counterfactuals change legal input values and recompute outcomes. Unaffected QA
remain valid controls; requiring all unrelated answers to change would introduce
artificial couplings. The simulator does not encode answers in thresholds, special
memo prefixes, visible seed IDs, or out-of-range counterfactual values.

## Run and verify

From the owning project environment:

```bash
uv run --no-sync python scripts/fetch_capability_topics.py \
  --output data/taxonomy/<new-snapshot> --workers 4

# Pin the resulting topics.json SHA-256 in a new versioned config.
uv run --no-sync python scripts/run_capability_curriculum.py \
  --config configs/capability_curriculum_v2.json \
  --output data/capability_curricula/<new-run>

uv run --no-sync python scripts/audit_capability_curriculum.py \
  --input data/capability_curricula/<new-run> \
  --output reports/<new-audit>.json
```

**Known gap, not yet fixed:** `scripts/audit_capability_curriculum.py` still
assumes the pre-split shard shape. Its `audit_export` rejects any shard whose
row count is not exactly 2 (`missing paired view`, line 129) and indexes
`views["factual"]` / `views["counterfactual"]` as one row each (line 198), so a
split shard — 32 rows, 16 per view — raises before it can check anything. The
auditor needs its per-shard view loop to accumulate rows rather than assume one
row per view. Until that lands, the workflow family cannot be audited by this
script; the ledger/reservation/rule_learning rows still audit unchanged.

Each worker writes an isolated world/shard, messages and an atomic receipt. A
completed manifest requires all scheduled shards, matching code/tokenizer hashes,
distinct example IDs and split isolation. Input tokens must occupy at least 90%
of 65,536 / 131,072 / 262,144 capacity; complete input plus answer must fit the
capacity. No evidence truncation is allowed. Full-chat and supervised token counts
use the pinned Qwen tokenizer and shared assistant-only masking helper.

Under split packaging the 90%-of-capacity invariant is per row, at that row's
own prefix length, so the shortest prefix is what the record count is fitted
to. The workflow partition schedule is therefore deliberately unbalanced: the
first question holds 93% of the records and the remaining fifteen share the
tail, which spreads the row lengths so each prefix is close to full and none
falls below 90%. An even 16-way split cannot satisfy the invariant — its first
prefix is 1/16 of a capacity-sized context.

`--resume` with identical code/config reuses **complete** shards after hashes,
world/message semantics and token/mask counts are checked. Incomplete shard
directories are retained for diagnosis and require a fresh run directory. This
version does not silently delete or overwrite partial artifacts.

## Evidence and accounting

Report topic/field coverage, transition families, seed groups, length variants,
contexts, training rows and QA pairs separately. Each of 16 questions has an answer;
placing 16 questions in one row does not create 16 independent worlds. Preserve
both the number of QA instances and the input-token reuse that packaging enables.
The manifest reports `packaging` and `rows_per_family`, so rows and QA pairs can
be read apart: a split family contributes 16x the rows of a joint one per world
and owns no input-token reuse.

Rows per world are also capped at 50 by the collapse gate's per-document
exposure check, which the split workflow family reaches at 32 (16 questions x 2
views). A split family with more than 25 questions per world would trip it.

The independent auditor parses actual exported user questions and assistant
answers, then recomputes every answer from visible input. It also checks privileged
oracle compact controls where available. Compact success is expected for suitable
retrieval tasks and is not automatically grounds for rejection.

For 4K/8K/16K prefix and suffix controls, identical observed token windows plus
identical questions but different correct answers form a bounded information
witness. This certifies insufficiency of that particular observation protocol for
the pair. It does not certify all sliding windows, arbitrary retrieval, every
token's necessity, or a universal strict long-dependency profile. Missing-header
parser errors are never counted as such witnesses.

Coefficient-instance overlap is recorded separately from structural family
overlap. Both splits share the affine family; disjoint coefficient tuples do not
establish generalization to a new mathematical family. Historical strict and
production flags remain unchanged.

Model evaluation is deferred by the user's current priority. A reusable evaluator
exists at `scripts/eval_capability_curriculum.py`; transport failures, truncated
responses and answered questions have separate accounting. It is not a generation
prerequisite, and no GPU or endpoint is required for the symbolic production loop.
