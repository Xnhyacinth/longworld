# P133 executed state mechanisms and 64-world campaign (2026-09-27)

P133 adds **two new controlled state-transition mechanisms** over the existing
synthetic record substrate. A partial reversal subtracts specified units while
leaving the record present. An authorization hold suppresses a record until a
matching visible controller grant is effective, disclosed and unexpired; a
viewer grant does not release it. This is two mechanisms, not two natural
domains. The 400/800-record settings vary physical length and scan load, not
mechanism identity.

The authoritative campaign is
`data/candidates/p133_state_mechanism_batch_v4/manifest.json` (SHA-256
`36c60c383f9d2297c47c9f866bff047d51d6e310f319fa5d772664beda871e79`;
compiler SHA `65196293d5e06b40cf9337c1d911414b82af56109f8de7d715509bdfec0f3169`).
It freezes every gross world and receipt, including rejected worlds, and pins
the prior P112/P133 pilot manifests. Seed, source-world ID and underlying
base-record context SHA overlap with those prior sets is zero. Each admitted
world shares one reader context across two filtered scopes, with `net_sum`,
`residual_entries` and two opposite-target one-step policy decisions per scope.
Both possible actions execute to a next balance and numerical feedback in the
proof sidecar. Reader QA and policy rows have separate files and separate
training contracts; neither is inserted into P130 selection.

| Exact property | Result |
|---|---:|
| Gross / admitted / quarantined source worlds | 64 / 61 / 3 |
| Admitted partial-reversal / authorization-hold worlds | 32 / 29 |
| Gross task attempts / admitted task views | 512 / 488 |
| Reader QA / one-step policy views | 244 / 244 |
| Train / eval views, source-world atomic | 384 / 104; no overlap |
| QA selected event deletion changes gold | 244 / 244 |
| Policy mechanism-event edit/deletion flips action | 244 / 244 |
| ADD_500 / REMOVE_500 policy labels | 122 / 122 |
| Exact final assistant masks | 488 / 488 |
| Final-chat length <32K / 32–64K / 64–128K | 47 / 203 / 238 |
| Full-chat / supervised tokens | 29,135,969 / 70,047 |
| Selected witness to question start | 16,530–90,488 tokens |

The three rejected worlds are authorization-hold/400-record instances in which
a required REMOVE-action mechanism-event witness is closer than 16K final-chat
tokens to the question. Rejecting each entire world removes 18 otherwise-valid
rows in addition to the three failed REMOVE rows and their three paired ADD
rows. All 512 attempted cells remain in `attempt_ledger.jsonl`; all 64 world
and receipt files remain SHA-pinned. `rejected_source_worlds` lists the three
seeds, IDs and causal reasons. This preserves the same-world multi-operation
contract without relaxing the event-distance gate.

The early v1 pilot (8 worlds/64 views), v2 64-world diagnostic (512 views),
failed strict-gate probe, and v3 506-view diagnostic are separate historical
artifacts. V2 selected ordinary record deletion as the primary QA witness in
200/256 cases, so it does not certify mechanism-dependent QA. The failed-gate
probe (`data/candidates/p133_state_mechanism_v3_failed_gate_probe_v1`) records
the first 10 incomplete worlds and 28 policy-row rejections. Adding valid
future-hold effective/reveal edits recovered seven worlds; three remain
quarantined in v4. Neither prior diagnostic is a training set.

For every v4 reader QA, a **visible transition event** deletion changes the
executed answer and its selected event is at least 16K final-chat tokens from
the question. Every policy task has a separate event edit or deletion that
changes the balance and flips the optimal action, also at least 16K away. The
REMOVE action may additionally have a record/support-group deletion witness;
that generic record witness is not counted as the mechanism certificate. In
partial-reversal worlds, increasing an existing visible reversal's units can
flip REMOVE. In authorization worlds, changing a visible controller grant or
activating a future hold at its effective/disclosure boundary can do so. Each
edit reruns the visible solver and both action feedback values. These are
bounded interventions, not an exhaustive proof search or a model-complexity
lower bound.

The reader-only QA export is
`data/candidates/p133_state_reader_unified_v1/manifest.json` (SHA-256
`ac109d397d1b4f55de3c6cd6c8af32847f169366f14187cac42165dc6362695e`;
adapter SHA `a1f1704f4d7148e693aa4f8beb09cc89aff6ba74f939a6fe53f736ed458ae66c`).
It has 244 standard unified reader candidates (192 train/52 eval), with exact
reader bytes copied from the campaign. Each index row includes an absolute
`world.json:task_id` native reference, task/sample IDs, source world and receipt
paths with hashes, and the campaign-manifest SHA. It excludes all 244 policy
rows. The P133-aware source auditor maps pinned worlds/receipts, base-record
identity and source-world split; a standalone audit resolved all 244 rows to
61 source groups and 124 pinned campaign/world/receipt files. The frozen export
manifest still records source certification as pending until the selected-bank
audit finishes. It remains `train_ready=false` and is not included in P130.

Full v4 generation and byte replay passed. A separate local pass recomputed
all 488 reader answers/action rewards, selected text deletions and policy
event edits from final reader bytes; all matched. Independent review repeated
488/488 semantic checks and the full replay, retokenized eight length-bin
extrema, and found no task blocker under the bounded claims. The best fitted
single target-only threshold reached 102/192 train and 26/52 eval policy rows;
this is a bounded shortcut diagnostic, not proof against every question-only
heuristic. No GPU training or model gain has been measured.

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p133_state_mechanism_batch.py \
  --config configs/p133_state_mechanism_batch_v4.json \
  --output data/candidates/p133_state_mechanism_batch_v4 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p133_reader_unified_adapter.py \
  --config configs/p133_reader_unified_adapter_v1.json \
  --output data/candidates/p133_state_reader_unified_v1 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q \
  tests/test_p133_state_mechanism_batch.py \
  tests/test_p133_reader_unified_adapter.py
cat data/candidates/p133_state_mechanism_batch_v4/manifest.json
less -R data/candidates/p133_state_mechanism_batch_v4/attempt_ledger.jsonl
less -R data/candidates/p133_state_mechanism_batch_v4/proofs.jsonl
cat data/candidates/p133_state_reader_unified_v1/manifest.json
```
