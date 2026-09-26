# P128 controlled action-state expansion (2026-09-27)

P128 compiles **one-step simulated policy decisions** from additional executed
states of the already pinned P112/P86 ledger worlds. This is a separate policy
candidate, not a reader-QA addition, new world mechanism, or multi-turn agent
trajectory. The authoritative artifact is
`data/candidates/p128_action_state_scale_v3/manifest.json` (SHA-256
`a56b1588641b5f764ab4f47193f3b8e41bcf310a61af9ce70bef0e5867febff8`).
The checked compiler file SHA-256 is
`aaa07400a539a12a62e89f6b4ac61126a843482a5a3f548b0ef99c0869277ea6`,
also pinned inside the v3 manifest and checked before verify-only replay.
The v1/v2 artifacts are earlier diagnostics with identical reader/proof/mask
bytes; v2 added explicit source-world reuse, and v3 pins the compiler code.
`train_ready=false`.

The source is the same 24 world IDs used by P114's 48-task pilot and the P112
controlled QA campaign. The v3 manifest lists those IDs and pins the P112 base
manifest and P114 policy manifest. **There are zero new source worlds and zero
new environment mechanisms.** P112's four domain profiles are vocabulary
renderings of the same executable ledger state machine; they are not four
independent agentic domains.

For each world, the compiler tries both existing filtered scopes (`q0`, `q1`)
at their original cutoff, one day after the effective cutoff, and one day after
the disclosure cutoff. It excludes `q0` at its original cutoff, already used
by P114. A state is distinct only when its complete active-record-ID set is
new within the world. Date labels, target offsets, or equal/different sums
alone do not establish state novelty. Each admitted state yields a pair of
opposite-target decisions with the same visible observation and menu. The
reader sees the entire ledger, state rules, filters, cutoffs, two actions and
feedback rule; it must choose the higher-feedback action. The assistant target
is one JSON action object. Full observed balance, both action outcomes,
rewards and evidence interventions stay in sidecars.

| Check | Result |
|---|---:|
| Gross state cells | 144 |
| Existing P114 state excluded | 24 |
| New distinct active states / paired policy tasks | 120 / 240 |
| Train / eval, source-world atomic | 190 / 50; 0 world overlap |
| ADD_500 / REMOVE_500 | 120 / 120 |
| Choose first / state-only constant prior | 120/240 / 120/240 |
| Target-only single-threshold diagnostic | 106/190 train, 28/50 eval |
| Exact assistant masks | 240/240 |
| Selected visible event / record-support-group deletion flips action | 120 / 120 |
| Final-chat length 32–64K / 64–128K / 128–256K | 100 / 90 / 50 |
| Full-chat / supervised tokens | 20,904,876 / 2,640 |
| Selected fact to question start | 25,723–181,134 tokens |

The threshold probe fits one target-value split on train worlds and evaluates
on the five held-out worlds. It is only a bounded no-history diagnostic. Same
observation opposite-target pairing guarantees 50% for a state-only or
constant-action rule, but neither this nor the threshold probe excludes every
question-only shortcut. The selected reader-visible fact deletion flips the
optimal action after the state solver and two transitions rerun. For revoked
record support, deletion removes the record and any referencing event; for an
event witness it removes only the event line. The distance is to this selected
witness, not a minimum proof across all equivalent supports.

A separate local semantic pass over all 240 final reader rows re-extracted
the target from the question, recomputed the balance from reader-visible text,
executed both action rewards, and repeated the selected text intervention;
all 240 matched the answer and sidecar. The 240 final reader/sample-index/proof/
mask IDs align, 240 masks report `exact_assistant_mask_checked`, and no sample
ID overlaps P114. Full compiler byte replay and four focused tests passed.
All 24 source-world split labels match the P112 base QA rows and P114 policy
rows; there are no cross-contract source-world split conflicts.
Independent subagent review confirmed 240/240 question/gold/outcome/deletion
rows and exact masks plus witness/query offsets on six length-bin extrema.
It found no task blocker. The final compiler fix reserves the P114 active-state
signature during deduplication; the v3 artifact has the same six non-manifest
file hashes as reviewed v2 and adds an enforced compiler SHA. There is no GPU
training or measured model gain.

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p128_action_state_scale.py \
  --config configs/p128_action_state_scale_v1.json \
  --output data/candidates/p128_action_state_scale_v3 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p128_action_state_scale.py
cat data/candidates/p128_action_state_scale_v3/manifest.json
less -R data/candidates/p128_action_state_scale_v3/state_ledger.jsonl
less -R data/candidates/p128_action_state_scale_v3/proofs.jsonl
less -R data/candidates/p128_action_state_scale_v3/mask_audit.jsonl
```

Further agentic scaling requires new state-world seeds/processes or genuinely
different action/feedback mechanics, not more target margins on these 24
ledgers. This candidate belongs in policy-specific assessment, not the
reader-SFT selection pack.
