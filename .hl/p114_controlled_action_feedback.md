# P114 isolated simulated action-feedback pilot (2026-09-26)

The pilot generated **48 distinct one-step policy tasks from 24 P86/P112
controlled base worlds**. Each world provides two opposite-target decisions
over the same as-of record state. This is new action-conditioned simulation,
not a relabeling of the old as-of QA: the compiler executes both actions,
computes next state and feedback for each, chooses the higher-feedback action,
and deletes a visible source fact to verify that the chosen action flips.

The frozen output is `data/candidates/p114_controlled_action_feedback_v1`
(`manifest.json` SHA-256
`ce7e219fceb34eafa71b324fc7ac96f1c015bbc95cc125085314ab616f7f5a01`).
`policy_train.jsonl` and `policy_eval.jsonl` form a **separate simulated agent
policy candidate**. They are not inserted into the reader SFT candidate index.
The assistant target is only `{"action":"ADD_500"}` or
`{"action":"REMOVE_500"}`. Observed balance, both counterfactual action
outcomes, next balances, rewards and source interventions live in
`proofs.jsonl`, outside the reader.

| Exact compiled property | Result |
|---|---:|
| Base semantic worlds / distinct policy tasks / views | 24 / 48 / 48 |
| Train / eval views, source-world atomic | 38 / 10; zero group overlap |
| ADD_500 / REMOVE_500 labels | 24 / 24 |
| ADD_500 / REMOVE_500 listed first | 24 / 24 |
| Choose-first-without-history correct | 24 / 48 |
| Final chat length 32–64K / 64–128K / 128–256K | 20 / 18 / 10 |
| Final chat / supervised tokens | 4,180,974 / 528 |
| Action-flipping visible fact deletions | 48 / 48 |
| Decisive fact → question start, exact final-chat tokens | 28,555–153,645 |
| Exact final-chat assistant mask | 48 / 48 |

The environment applies the P86 visible filter and effective/disclosure rules
to obtain current balance (S). The two available actions change it to
(S+500) or (S-500); feedback is (-|S_{next}-target|). One target lies
above (S), one below, so the same world gives opposite action labels. In 47
tasks the target offset magnitude is 250; one source world needed 150 to make
a text deletion change the action. The selected proof fact is a disclosed
revocation for the ADD action or an active record for the REMOVE action;
deleting it from reader text and rerunning the state solver reverses the best
action. The rule/header and every alternative support were **not** exhaustively
removed. The distance is to a certified action-flipping fact in this bounded
intervention, not a global shortest natural-language proof.

A bounded no-history diagnostic fitted the best one-dimensional target-value
threshold on train tasks: 22/38 correct there and 6/10 on source-disjoint eval
worlds. This is close to the 50% balanced prior but cannot rule out every
question-only heuristic. The same-world opposite-target pairing and balanced
menu order prevent a constant-action or first-choice answer from exceeding
50%. No model training or general long-context benefit has been measured.

The four-process compiler is pinned to the P112 base manifest and every P86
world receipt. It independently replays state from reader-visible records,
executes both actions, checks action-flipping text deletion, maps exact fact
and question positions through the final chat tokenizer, and rechecks the
assistant-only mask. Full byte-for-byte replay passed:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p114_controlled_action_feedback.py \
  --config configs/p114_controlled_action_feedback_v1.json \
  --output data/candidates/p114_controlled_action_feedback_v1 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p114_controlled_action_feedback.py
cat data/candidates/p114_controlled_action_feedback_v1/manifest.json
less -R data/candidates/p114_controlled_action_feedback_v1/proofs.jsonl
less -R data/candidates/p114_controlled_action_feedback_v1/mask_audit.jsonl
```

This is a controlled one-step environment, with executable outcomes for both
actions under a fixed observation. It is not an interactive multi-turn agent
episode, a real PR repair policy, an unrestricted counterfactual proof, or a
training-ready mix (`train_ready=false`).
