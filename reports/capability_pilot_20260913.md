# Capability-first executable-world pilot

Status: implemented, locally materialized, and independently audited.
This closes the first three-recipe milestone of
`.hl/longworld_capability_reassessment.md`, not the full research programme.

## Implementation

- `longworld/synthesis/capability_world.py`: one fully simulated ledger grammar,
  shared events for recall/binding, approved-record aggregation, and conditional
  state transitions. Existing `WorldSimulator` supplies oracle state; the visible
  JSONL interpreter reconstructs answers without reading oracle state or gold.
- A bounded counterfactual changes an independently selected recall memo and a
  separate amount/approval record. Amounts retain the original 10–50 range, memo
  formats match, and affected spends plus their dependent event are rerun.
  The compiler rejects worlds where a suitable intervention is not found.
- `scripts/run_capability_world_pipeline.py`: process workers grow complete event
  histories to the declared interval [95%,100%] of each capacity. Every factual/CF
  chat undergoes pinned-tokenizer counting and the shared assistant-only
  preprocessing helper. Evidence is never truncated to fit.
- World seed groups stay in one split across lengths. Exclusive output
  directories, code/tokenizer hashes, shard hashes, explicit rejects, and atomic
  completion receipts prevent a partial run being represented as complete.
  Resume is not implemented: reruns require a new output directory.

## Actual execution

```bash
uv run --no-sync python scripts/run_capability_world_pipeline.py \
  --output data/capability_pilots/20260913_v1 \
  --seeds 0 1 2 3 4 5 \
  --capacities 65536 131072 262144 --workers 3
```

Worker PIDs: 523628, 523631, 523632. All 18 scheduled shards completed;
`rejects.json` is empty. No GPU/model training or publication was performed.

| Capacity | Rows | Actual full-chat tokens | Record workload parameter |
| --- | ---: | ---: | ---: |
| 65,536 | 36 | 63,630–63,989 | 529–535 |
| 131,072 | 36 | 127,657–127,860 | 1,062–1,076 |
| 262,144 | 36 | 255,428–255,720 | 2,126–2,156 |

Accounting: 108 rows = 72 train + 36 eval; 36 rows per named capability;
6 seed groups, **1 semantic topology/rule family**, 18 length/workload variants,
36 factual/CF contexts. Three questions reuse each context in separate rows;
these are not 108 independent worlds or 108 independent contexts.

The 256K aggregation/state tasks cover 709–719 target-entity records. Observed
aggregation evidence spans 255,068–255,390 tokens; recall's alias-to-ordered-record
coverage spans 128,179–254,768 tokens. These are coverage measurements, not proofs
of a minimal necessary evidence set or resistance to all retrieval shortcuts.

Total serialized full-chat tokens: 16,096,348. Supervised tokens: only 1,354
(0.00841% of chat tokens, including assistant terminators). This is a correctness
pilot, **not an efficient or sufficient SFT corpus**. Merely increasing identical
single-question rows would waste budget; the next compiler milestone should add
independent tail queries and distinct rule/task families before large scale-out.

Manifest and local message-format exports:
`data/capability_pilots/20260913_v1/{manifest.json,train.jsonl,eval.jsonl}`.
Detailed statistics: `reports/capability_pilot_20260913_stats.json`.

## Review and verification

Independent review initially rejected several implementations despite green
answer-consistency tests. None of those intermediate implementations was exported.

| Finding | Resolution |
| --- | --- |
| Late threshold encoded the aggregate answer | Removed total-derived threshold; fixed-cost interleaved conditional spends |
| Original state duplicated the aggregate | Real applied/skipped spends now influence state |
| CF memo prefix and huge amount revealed the recall target | Same memo format and native amount ranges; distinct recall and state interventions |
| Dependency pointed directly to recalled record | Dependent event references a spend, independent of the recall record |
| Huge CF amount did not always enable descendants | Bounded search verifies actual changed descendant and state, otherwise rejects |
| Non-spend dependencies interpreted inconsistently | Unsupported dependency grammar rejected explicitly |
| Completion receipt could be partially visible | Same-directory temporary file, fsync, exclusive atomic hard-link publication |

Focused command:

```bash
uv run --no-sync python -m pytest \
  tests/test_capability_world.py tests/test_capability_world_pipeline.py \
  tests/test_p0_engine.py tests/test_train_sft_masking.py -q
```

Result: **39 passed**. Ruff checks passed on both new implementation files and
their tests. The compiler worker additionally checked 20 seeds at 24/400/4000
records: 60 generated worlds passed regeneration and visible-answer validation.
The independent reviewer checked 20 seeds at 6/12/24/60/400 records: 92 generated
worlds passed, 8 small worlds explicitly rejected generation, zero generated
worlds failed validation. The rejection rate is not a model accuracy measurement.

The actual pinned tokenizer masking probe supervised only the answer and its
assistant terminator. Unit tests also cover incomplete/duplicate observations,
tampered gold, unsupported dependencies, no-overwrite behavior, and injected
shard failure yielding rejects without a completed manifest or training export.

Post-export independent audit passed: five current code hashes, two export hashes,
36 shard-content hashes, and all 18 receipts match. All 108 example IDs are unique;
train seeds {1,2,3,4} and eval seeds {0,5} and their world IDs are disjoint. Parsing
the actual QUESTION JSON from exported user messages and invoking the visible-only
solver reproduces **108/108 assistant answers**, without reading the world gold.
Pinned-tokenizer spot checks at all three capacities reproduce full-chat lengths
63,664 / 127,688 / 255,607 and the assistant-only supervision masks. These are
symbolic pipeline checks, not model accuracy results.

## Remaining boundaries and next milestone

The observations are structured synthetic ledgers, not natural prose. Domain
labels are presentation only. There is one rule family, seed-only train/eval
isolation, no held-out rule transfer, no legal-unknown examples, no L4/L5 harness,
and no model question-only/retrieval/short-window or training-gain measurements.
Observed span must not be renamed strict dependency. Historical strict/release
flags remain unchanged; these message files are not signed framework-ready
release packages.

Next: build independent multi-query supervision and additional transition/rule
families; add matched oracle-compact, retrieval, and model controls; then conduct
the equal-budget natural-long / MRCR-like / world-composed mixture experiment.
The pilot validates a generation-and-verification mechanism, not that research
hypothesis or the sufficiency of the current dataset.
