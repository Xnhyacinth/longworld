# P113 agentic feasibility preflight (2026-09-26)

**Strict L5 action-feedback qualified decision points: 0.** The scanned units
are **4 repository bundles containing 27 usable historical PR episodes** and
**24 controlled base worlds containing 192 QA tasks**. These units must not be
summed into “51 L5 tasks.” The frozen report is
`data/candidates/p113_agentic_feasibility_v1/report.json` (SHA-256
`c347c9f718f9c5152deb856bd87d8fdeed835d789635134913d4547ead41ddd6`). It is candidate-only and `train_ready=false`.

The preflight requires an explicit pre-action observation, an action menu with
at least two valid alternatives, a chosen action, action-conditioned next-state
observations, feedback for the available actions, and an alternative outcome
under the same decision point. It does not infer policy labels from the mere
presence of commits, CI results, state events, or answers to questions about a
state. A future source with an executable transition contract can be evaluated
against the same criteria; no such decision point is present in these artifacts.

| Source | Observed material | Missing for strict L5 |
|---|---|---|
| P108 signed GitHub PR/CI bundles | 27 usable PRs in Bitcoin 9, Deno 8, Godot 6, Requests 4; 22 show CI runs, 6 have failed CI, 5 have a later commit after failed CI | Action menu, chosen-action label, executable action-conditioned transition, per-action feedback, counterfactual alternative |
| P112 controlled shared-state campaign | 24 base worlds and 192 QA tasks with state events and bounded text interventions | Agent-selectable actions, action-conditioned transition, feedback/reward and alternative action outcomes |

The five failed-CI-then-later-commit histories are interesting **source
proposals**, not five successful repair policies: the later commit can coincide
with a fix but these frozen records do not provide an executable intervention
or an alternative outcome under the same initial state. All 51 inspected source
units lack an explicit decision-point contract. The rejection counts in the
report are per source unit, not per QA task. No pilot L5 task was produced.

Reproduce and replay the source-inventory conclusion:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p113_agentic_feasibility.py \
  --code-manifest data/capability_records/p108_code_sources_v4/manifest.json \
  --controlled-base data/candidates/p112_world_factor_campaign_v2/base \
  --output data/candidates/p113_agentic_feasibility_v1/report.json --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p113_agentic_feasibility.py
cat data/candidates/p113_agentic_feasibility_v1/report.json
```

The scanner checks the P108 bundle hashes against its manifest, the individual
PR export hashes against each bundle, and each P112 world against its job
receipt, then recomputes the observed counts. It does **not** independently
rerun cryptographic source-attestation validation or reader mask verification;
those belong to the upstream frozen producers. This preflight establishes a
bounded zero-result for these two cohorts, not for every possible code or
simulated source in the repository.
