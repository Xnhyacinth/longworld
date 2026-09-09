# P58 IETF question-only and relation-necessity diagnostic

The existing dense/window receipts are real, but they do not establish that
these tasks require causal long-context reasoning. A deterministic predictor
that sees only the question recovers **16/24 complete answers and 127/135
fields** across the seven P57 IETF tasks. No model, context, gold answer,
evaluator-only field, or source document is used to generate its predictions.

## What was measured

The predictor parses the question's explicit per-field codebook and returns
each field's sole `code`. The gold is read only afterward for scoring. These
are a deliberately simple baseline and correlated views, not 24 independent
experiments or a perfect solver.

| Views | Exact matches | Correct fields |
| --- | ---: | ---: |
| Full | 8/8 | 45/45 |
| Ordered artifact | 8/8 | 45/45 |
| Counterfactual | 0/8 | 37/45 |
| Total | 16/24 (66.7%) | 127/135 (94.1%) |

The seven tasks are ACME, SSH, DNSSEC, HTTP Semantics, HTTP/2, PKIX path, and
TLS 1.3. TLS contributes two lengths; all contribute three views. The same
question-only baseline was also run directly on the seven frozen local-product
train files, with each train-file SHA256 recorded in the JSON report. It returns
the same aggregate counts there. This is not merely an obsolete candidate result.

The current CF view changes one answer field to `UNKNOWN`. Its failure under
this baseline is meaningful: question-only prediction does not solve the
presence/absence discrimination. It nevertheless exposes severe factual-label
predictability and field imbalance. Improving packing cannot repair that.

## Relation ablation

Every parent was replayed with each declared essential relation removed, and
with all those relations removed together. For ACME and SSH, the answer is
unchanged. `_replay_gold_rfc_succession_task` validates `selected_relations`,
then computes output from `selected_evidence` and fixed answer codes only.
The other five tasks change their answers for each declared-relation ablation.

This does not mean that deleting every source document leaves ACME/SSH solvable,
nor that their quoted facts are fabricated. It means the declared publication
edge is not necessary to the implemented answer. Do not claim it as an
answer-changing causal dependency, or invent a publication requirement solely
to make an ablation green.

The full ACME evidence quotes total 454 characters; SSH totals 639. These are
gold-selected support sizes, **not** a blind retriever's successful context size.
Their separation by authentic leftover may create long-distance retrieval,
which must be distinguished from a deep dependent computation.

## Why the previous gates missed this

`compute_task_proof` currently derives `question_only_unsolved` from empty
adapter selection plus absence of the *entire serialized answer string* on
the question/body surface. A nested codebook can disclose every answer value
without containing that exact serialized string. Running the adapter on no
selected artifacts is not equivalent to letting a solver read the question.
The raw-window and MiniLM checks measure their stated selectors/replay rules;
they do not eliminate this separate shortcut.

The source-unit ACME 32K repair and fifth-gold DNSSEC 64K successor remain
valid descriptions of their packing/receipt history. The old DNSSEC
`event_count < 4` failure is superseded and was not rerun. Neither the 3/3
current receipt nor `already_promoted=true` overrides the new diagnostic.

## Treatment and next admissible version

- Retain the immutable old products and their provenance; describe their
  existing gate status and this new limitation together. No product was
  silently relabeled or rewritten by this diagnostic.
- Do not use these 24 rows as evidence that a model needs long-context causal
  reasoning, and do not count their length/view variants as task diversity.
- A successor task should produce source-computed values or resolve real
  version/condition alternatives, so its question does not disclose a single
  factual output per field. Removing the codebook alone would not establish
  necessity or repair memorized constants.
- Measure question-only, short retrieved support, source/relationship removal,
  and real alternative-version outcomes before spending on dense long-window
  audits. Keep alternative facts and labels source-supported; do not fabricate
  false statements to balance classes.
- Gate thresholds, legacy receipts, source bytes, CURRENT_RELEASE, and HF were
  not changed. This is a diagnostic, **not an implemented universal shortcut
  gate** or a certification that the other inventory is shortcut-free.

## Reproduction

```sh
uv run python scripts/audit_p58_ietf_shortcuts.py --catalog configs/p57_task_pipeline_v1.json --inventory reports/p58_inventory_audit_20260908.json --output reports/p58_ietf_shortcuts_20260908.json
```

The JSON binds catalog, script, replay implementation, projected candidates,
parents, inventory snapshot and affected frozen train-file hashes. An independent
read-only rerun reproduced all seven candidate-job results. Ruff passes for the
new diagnostic script. No neural-model accuracy or training result is claimed.
