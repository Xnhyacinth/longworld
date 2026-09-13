# Capability curriculum production

The v2 pipeline produces SFT messages: one visible context, 16 tail questions,
and one assistant JSON object containing all 16 answers. Gold and private control
contexts are excluded from the user message. This is not a CPT-only export.

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

| Family | World transitions | Questions and gold |
| --- | --- | --- |
| ledger | approved credits, conditional debits and distant event prerequisites | per-entity as-of balances, aggregates, ordinal memo recall and applied-action sets |
| reservation | supply, conditional reservation, release and idempotent cancellation | free capacity, supplied quantity, ordinal records and active reservation sets |
| rule_learning | novel affine rule inferred from demonstrations; distributed feature increments | classify each entity after integrating its updates, using a separately generated coefficient oracle |
| workflow | chained job outputs; tool inspect/process failure, repair and commit | partition endpoint value, recovery count and action after already-observed feedback |

The ledger/reservation oracle uses the existing event simulator; the visible
interpreter reconstructs state independently. Rule-learning truth uses generator
coefficients while the reader infers the unique rule from demonstrations. Workflow
truth executes tool transitions; its reader uses a separate arithmetic/transition
implementation. Future recovery traces remain outside model input. The workflow
is bounded causal replay supervision, not measured model-driven closed-loop L5.

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

Each worker writes an isolated world/shard, messages and an atomic receipt. A
completed manifest requires all scheduled shards, matching code/tokenizer hashes,
distinct example IDs and split isolation. Input tokens must occupy at least 90%
of 65,536 / 131,072 / 262,144 capacity; complete input plus answer must fit the
capacity. No evidence truncation is allowed. Full-chat and supervised token counts
use the pinned Qwen tokenizer and shared assistant-only masking helper.

`--resume` with identical code/config reuses **complete** shards after hashes,
world/message semantics and token/mask counts are checked. Incomplete shard
directories are retained for diagnosis and require a fresh run directory. This
version does not silently delete or overwrite partial artifacts.

## Evidence and accounting

Report topic/field coverage, transition families, seed groups, length variants,
contexts, training rows and QA pairs separately. Each of 16 questions has an answer;
placing 16 questions in one row does not create 16 independent worlds. Preserve
both the number of QA instances and the input-token reuse that packaging enables.

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
