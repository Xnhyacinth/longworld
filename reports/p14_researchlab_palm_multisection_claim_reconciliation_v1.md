# P14 ResearchLab PaLM multisection-claim closeout

## Outcome

PaLM v5 is a final `NO-GO` for `paper_multisection_claim_reconciliation`.
It does not count toward ResearchLab quota and was not promoted. The task-specific
adapter, query, render ordering, pack marker, config, and tests were removed after
the bounded rescue failed. No exact-token, near-duplicate, derived-view,
truncation, source-lineage, window, dense, or replay threshold was relaxed.

## Authentic source and attempted dependency

The diagnostic used the official arXiv PaLM history `2204.02311` through v5. The
compiled-input preflight recursively followed `\\input{...}` from `main.tex`,
excluded unreachable files, bibliography, cache, and archives, and selected one
unique content claim per reachable methods, training, compute, benchmark, or
limitations file. Answers reconciled exact content claims rather than paths or
hashes, and remove-one checks required every selected claim, control relation,
and decision.

The first task design had no authentic revision relation. Its initial natural
candidate emitted only 5/9 cells: 16K full/CF passed at 16,295 tokens, 16K
ordered collapsed to full, all 32K views overflowed at a 32,911-token lower
bound, and all 64K views passed at 65,292 tokens.

A bounded semantic-order/claim selection rescue emitted a diagnostic 9-row
candidate at 16,295 / 32,594 / 65,292 exact prompt tokens. Strict audit rejected
9/9 because its internal compiled-input control reused the
`arxiv_revision_relation` event type: the candidate serialized relation count 1
while both candidate and replay relation-edge lists were empty. The dense
ranking completed, but the fail-closed source-bundle audit accepted 0 rows.

The final authorized preflight separated the compiled-input control from real
revision relations and bound the signed official v5 `revision_of` v4 relation
(`arxiv:2204.02311v5:revision-of`, evidence
`"previous_revision_id": "v4"`). It reused the v5 `training-setup.tex` claim as
the source endpoint and added a bounded v4 endpoint. All three tiers then had
exactly one validated relation count and one replayed authentic edge. Their
complete essential lower bounds were nevertheless:

| Tier | Claim files | Essential artifacts | Exact prompt lower bound | Band upper bound | Overflow |
| --- | ---: | ---: | ---: | ---: | ---: |
| 16K | 6 | 10 | 17,085 | 16,384 | 701 |
| 32K | 12 | 16 | 33,591 | 32,768 | 823 |
| 64K | 27 | 31 | 66,756 | 65,536 | 1,220 |

Every tier therefore failed `strict_support_overflow` before candidate
generation. Per the bounded-rescue rule, no further file substitution or claim
tuning was attempted.

## Diagnostic artifacts

- signed manifest SHA-256:
  `92e3f41b98da96ad0e49851569da0fbcfe8694bd02fce08300e9c00b771c0724`
- signed source bundle SHA-256:
  `719b192a4dc48e600d763b42870124b31c9719f74baa30e86827ec5786151fd9`
- invalid intermediate candidate:
  `data/releases/p14-researchlab-palm-multisection-claims-v1-candidate-rescue/`
- candidate train SHA-256:
  `caf49275a052908d609809e2a806d25c1b27091fd69bd552fc2aed0ba29e4828`
- candidate quality report SHA-256:
  `e6fb79d0d4708f6ce8ca234146dc202b022aa1c72bdfafa8b09539618df0e490`
- dense ranking SHA-256:
  `e000dc46762b48b55f7e2b1148f0b06a61ab6d16aa40de2f2c57268e48a7256f`
- strict rejects SHA-256:
  `eb9a428def79c8174a5e6092dee689171f71636c56ce339f5ff0fb6a8cd5222c`
- strict audit rows/accepted/rejected: `0/0/9`

These are diagnostic local-probe artifacts only. The candidate is not a
train-ready release.

## Retained generic fixes

Two independent fail-closed fixes remain after task cleanup:

1. The paper exporter preserves exact internal whitespace and can select a
   plain sentence fragment following an inline-LaTeX sentence while continuing
   to reject macro-bearing fragments and enforce unique/absent-from-prior spans.
2. `context_source_relation_count` now uses
   `valid_arxiv_revision_relation_event`, the same endpoint predicate used by
   relation-edge replay, instead of counting every event named
   `arxiv_revision_relation` whose required inputs merely exist.

The second fix has an independent real-revision fixture: a valid v2-to-v1 edge
counts as one, while corrupting its source endpoint identity counts as zero.
