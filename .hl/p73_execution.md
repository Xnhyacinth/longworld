# P73 counterexample-driven dependency synthesis — pilot execution

User directive (2026-09-23 06:02, the session's last real instruction): shift
from "fixing one recipe" to proposing and validating a data-generation method
whose core question is synthesizing data where the model must distinguish the
correct information-use procedure from plausible near-miss shortcuts. Charter
frozen in `.hl/design/p73_counterexample_synthesis.md`; GPU training stays
user-gated. This note records what the pilot wave actually landed and measured.

Note on lineage: the prior session (84d741b9, job fix-blockers-batch-data) wrote
the three modules on 2026-09-23 morning but degraded into corrupted tool calls
from ~09:20 (long uncompacted turn; harness rejected the malformed calls, which
surfaced as "No such tool available: Write/Bash"). This wave re-verified every
file that session produced before extending it: all modules pass py_compile and
their test files, and no half-written code from the corrupted period entered
the tree. The bank the degraded session generated at 15:56 was kept only after
full re-validation below.

## What landed

1. `longworld/synthesis/capability_mutations.py` — semantic mutants per family
   (drop_one_condition / and_to_or / ignore_exclusion / last_value /
   nearest_lexical / last_declaration / latest_text / ignore_revocation /
   wrong_rule_family / ignore_demos / relax_one_condition /
   tighten_one_condition) and `witness_report()` per-row computation. Pure
   audit-side functions; no bank mutation.
2. `longworld/synthesis/capability_shared_world.py` — one shared context hosting
   multiple families (records-side tasks solve over the full merged row set —
   real cross-family interference; F-side tasks solve over their sub-world rows,
   documented in the module docstring). Tasks now carry `solve_context` +
   `solve_context_sha256` so audits re-solve exactly what the answer was solved
   with.
3. `scripts/measure_witness_coverage.py` — shared-world bank audit (shards'
   world.json full-object layout + legacy header+rows layout both supported),
   writes `verification.witness.json` with per-row `example_details` and
   per-family summaries.
4. `scripts/generate_p73_pilot.py` — 42 worlds, 7 families x 84 rows, 588 rows
   total, bank `data/capability_records/p73_shared_v1`. Split is now by WORLD
   (seed % 5 == 0 -> eval): 448 train / 140 eval rows, 32 train / 10 eval
   worlds, zero straddlers. The original row-level 80% cut leaked one world
   across train/eval (found by the exposure gate, fixed, bank regenerated).
5. `scripts/export_p73_arms.py` — C (normal) vs D (witness-rich,
   distinguished_fraction >= 0.5) arm split with matched quotas/length/empty/
   answer-size and four-caliber budget stats; arms.json under the bank.
6. `scripts/export_contract_arms.py` + `longworld/synthesis/capability_contracts.py`
   — the three answer contracts (answer-only / answer+minimal-evidence /
   full-provenance) over the frozen arm_a rows
   (sha 9468dac48ecb1bba1acde70c4cd9e4acb4514a0320bf8dcf9ff7688ee50c2d90),
   3,972 rows each, landed under `data/capability_records/p73_contract_arms/`
   with corrected arms.json (real out_dirs) and manifest.

## Measured results

Witness audit (588/588 rows, deterministic re-run after the split fix):

| family | witness-rich | mean distinguished | per-mutant highlights |
|---|---|---|---|
| filter_aggregate | 100% | 0.967 | and_to_or 84/84, ignore_exclusion 84/84, last_value 84/84, drop_one_condition 73/84 |
| rule_holdout | 100% | 0.952 | wrong_rule_family 84/84, ignore_demos 76/84 |
| group_compare | 100% | 0.591 | and_to_or 84/84, drop_one_condition 65/84 |
| join_lookup | 100% | 0.584 | and_to_or 84/84, drop_one_condition 63/84 |
| asof_state | 100% | 0.500 | ignore_revocation 84/84, latest_text 0/84 |
| alias_locate | 96.4% | 0.482 | nearest_lexical 81/84; 16/84 empty-answer rows |
| set_complete | 81.0% | 0.405 | relax_one_condition 67/84, tighten_one_condition 1/84 |

Honest readings — dead or near-dead mutants on this bank (root causes verified
by the witness-audit agent, 2026-09-23):
- `latest_text` (asof) 0/84: NOT a bank coincidence — the implementation calls
  the same solver path as the correct answer (capability_mutations.py:232-234
  vs answer_of), so it can never distinguish on ANY bank. Inert by code.
- `last_declaration` (alias) 0/84: the spine enforces exactly ONE declaration
  row per alias handle (capability_families.py:873-876), so declarations[-1] ==
  declarations[0]. Inert by spine contract.
- `ignore_exclusion` (group_compare/join_lookup) 0/84 structurally: the
  generator draws the excluded category outside the two named groups
  (capability_records.py:668-671), so dropping it never changes the answer.
- `tighten_one_condition` (set_complete) 1/84: 69/84 scopes pin category==
  first (no numeric bound to sharpen); on-bound members are rare. Honest, but
  weak.
- `relax_one_condition` (set_complete) errors on 26/84 two-condition scopes
  (falls to 1 condition, below the 2-to-4 gate) — counted distinguished by the
  error-is-distinguished rule; real effect, noisy signal.
Design §3.1 has been amended to match what ships. asof's witness-fraction is
structurally capped at 0.5 while latest_text is inert — material for the C/D
>= 0.5 threshold. Distinguished fractions here are computed with erroring
mutants counted as distinguished (a shortcut that cannot produce a legal
answer still fails the task) — see capability_mutations.witness_report.

Gates (details in `data/capability_records/p73_shared_v1/verification.gates.json`):
- Solver recheck 588/588: zero answer mismatches, zero solver errors, zero
  provenance errors; group_id==world_id everywhere; contexts/answers/sha
  consistent across train.jsonl, eval.jsonl and shards.
- Collapse gate passes both calibers on both splits (exposure 13.7-14.0 /
  20.6-21.0, shape_uniqueness 0.62/0.57, top_instruction_share 0.004).
- Length honesty: worlds cluster at ~64K full-chat tokens (min 66.7K, median
  75.9K, max 92.3K); 36/42 within ±25% of 65536, ZERO at 8K or 32K. The design's
  8K/32K/64K three-band coverage is NOT met — the pilot is a single-band bank.
  Fixing this needs per-group length_records reduction in
  generate_p73_pilot.py CONFIG (later wave, not silently patched here).

C/D arms (`data/capability_records/p73_shared_v1/arms.json`): 400 train rows
each, 32 worlds each; supervised 143,640 (C) vs 145,502 (D) tokens; full-chat
30.6M vs 30.7M; steps@1ep 25/25; shape exposure 1.72/1.72. Witness-poor pools
were too small for several families (asof/filter/rule: zero poor rows below
0.5), so C for those families is a matched subset of D — overlap honestly
recorded per family in the honesty notes. The pilot's witness-rich skew
(mean ~0.7) itself makes a hard C-vs-D contrast impossible at 42 worlds; a
larger or mutant-varied wave is needed for a clean contrast.

Contract arms: answer-only cuts supervised tokens 2.80M -> 0.396M (14.1%) and
minimal-evidence -> 0.306M (10.9%) over the same 3,972 rows — the ID-enumeration
supervision hypothesis is now directly measurable by training B' vs A'.

Two late findings from the verification passes, landed after the first commit:
- The original answer-only export had DESTROYED the records-family world
  context (the old instruction swap replaced the whole ~228K-char user message
  with a 79-char one-liner; ~188M full-chat tokens were missing — visible only
  as the 187.7M-vs-375.8M full_chat_tokens gap in the stale arms.json). Fixed
  in capability_contracts.py (instruction_for_contract swaps only the Task and
  Return sentences; user_message_for_contract preserves the world context under
  every contract), answer-only regenerated and verified row-by-row (60 seeded
  rows/family/contract, 120 rows/contract re-tokenized, 0 mismatches). B' must
  train on the regenerated export.
- verification.gates.json as first written was invalid JSON (one missing
  brace; gate3/honesty nested inside gate2). Repaired and re-serialized; all
  gate data intact.

ID-enumeration "~73% of supervision tokens" (findings.md:1549) remains
UNVERIFIABLE as a number — no measurement artifact backs it. The honest
on-disk replacement: full-provenance 2,802,609 vs answer-only 395,793
supervised tokens on the same 3,972 rows = 85.9% of supervision sits in
answer-side id enumeration (p73_contract_arms/arms.json, tokenized with the
export's pinned Qwen recipe). Cite that number, not 73%, until a dedicated
measurement lands.

## What is NOT claimed

- No training has been run; all five arms (R/A'/B'/C/D) remain user-gated.
- Single length band; 8K/32K arms do not exist yet.
- The C/D contrast is weak at this scale (documented above), so the pilot bank
  is for pipeline validation and D-threshold calibration, not for a
  publishable D>C comparison.
- Related-work notes fact-checked by a read-only pass (task #6): 12 findings,
  key corrections applied — arm_b MMLU-Pro "−2.8pp" corrected to −24.3pp
  (design §2 + findings.md annotation; the −2.8 table matches nothing on disk),
  DocQA stock corrected to 3,597 rows (13,485 included never-downloaded
  LongAlign-10k), wiki "八行试点" marked unverified, set_complete
  ignore-emptiness mutant noted as unimplemented, witness audit aggregation is
  family-only (not family×length). External-work table: no factual errors
  found. ID-enumeration "73%" and the wiki pilot remain unbacked claims.

## Next steps (user-gated)

1. Decide the mutant refresh: replace latest_text / tighten_one_condition with
   live mutants or vary worlds so they can distinguish.
2. Multi-band regeneration (8K/32K/64K) once the length knob is turned.
3. Scale decision for the C/D wave (worlds >= several hundred for a real
   contrast).
4. Training arms R/A'/B'/C/D remain gated on the user's GPU call.
