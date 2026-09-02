# P14 ResearchLab Sparks section-reconciliation closeout

## Outcome

The current-trust local-probe candidate is 9/9 accepted by the
`p7-paper-source-slice-1-v1` strict audit. It contributes one ResearchLab world
with three strict nested tasks (3/5/10 source sections) and full, counterfactual,
and ordered views at every 16K/32K/64K band. The accepted row set contains
341,709 exact Qwen tokens. No pack, near-duplicate, exact-band, truncation,
derived-view, source-lineage, or replay threshold was relaxed.

## Authentic source and executable dependency

The source is the official arXiv history for *Sparks of Artificial General
Intelligence* (`2303.12712`), revisions v1 through v5. The task requires the
signed v5 `revision_of` v4 edge, both bounded endpoints, one uniquely grounded
content claim from every selected v5 section, the deterministic compiled-source
control, and the decision. Removing any claim, endpoint, relation, control, or
decision yields `unknown`; changing the metrics claim changes the answer under
semantic counterfactual replay.

The visible-LaTeX compiler removes only comments and formatting markup, keeps
the grounded claim byte-for-byte, and records the original source hash and byte
range plus the compiled output hash. The control records the reachable
`main.tex` include graph and explicitly excludes acknowledgments, author block,
bibliography, build cache, macro-only, table-of-contents, and unreachable input.

Official source archive SHA-256 values are:

- v1: `83a0605c77c0fa53c08f87612449d4e8a77f8745427179d3d2bc7b8ce419ba21`
- v2: `b4298a6c254b6ea6e540c16469f52a6db0200400043b7d5443bdd4077ca1d9e2`
- v3: `154f014e0ab1fb5d2d5d48c0a983669420b48db16505ec5b217f0d11bf6e73ad`
- v4: `3651323cc9eddee607a9c4d2f7329bd1d5ba79bbe6dc22d4e2774a471822738a`
- v5: `2366e07aa84848e354c3f5489fa3b39e7da4af7a9eda4d1cc80b017d8a67f9b0`

The current-trust signed manifest SHA-256 is
`2385ccdee13bc2f87c4c6a322c7a8e60e447288e2b6ce71c485f7c6dbd9f7521`;
the signed `sourceworkflow@2` bundle SHA-256 is
`97b35381a2dbd429b9a26b46fdd9f59e75042f5d2dfe8ccc4c373c70fee795ec`.
Current-trust replay loads one workflow, five records, four continuous revision
relations, and workflow ID `source:paper_workflow:9734aeb87a51a942425b3dab`.

## Exact and strict results

| Band | Full / CF / ordered tokens | Full / CF span | Ordered span | Source relations |
| --- | ---: | ---: | ---: | ---: |
| 16K | 16,243 | 16,138 | 16,138 | 1 |
| 32K | 32,338 | 32,158 | 32,158 | 1 |
| 64K | 65,322 | 65,141 | 64,480 | 1 |

Mean sentence near-duplicate ratios are 0.0084, 0.0049, and 0.0034 by band.
Full/ordered source-token ratios are 0.9072, 0.9222, and 0.9075. Every dense
top-3 replay is `unknown`, while the full selected pool has semantic and strict
executable sufficiency, counterfactual sufficiency, and remove-one failure.

- candidate row-set SHA-256:
  `3b0abcfe647d9528e1ef1e9c9ca014c54cc9636b8b5e6c16dec5c197d89b3f33`
- candidate train file SHA-256:
  `27b71d7f129a4bd35e0cb1752481af2862584bd168a86a277ee383fb172ac7f6`
- pinned dense rankings SHA-256:
  `34c09273885053088e68d1a48c4ad05d3c5e9c514f56211725897a210092f542`
- strict audit receipts SHA-256:
  `fb13942b38e27b0793546002331a6dc705b4897d8bc5e913a134536894820014`
- accepted row file SHA-256:
  `d88ad9dade12f47682e906d074d3e2205e9c63a39e108ba7649a31df4248ebd6`
- preflight / audit accepted / rejected: `9 / 9 / 0`

## Verification

The formal checks used the project `.venv` and the active local-probe trust at
`/root/.longworld-p12-active/p12-probe-12-20260829-v1/local_probe_trust.json`.

```text
pytest -q tests/test_researchlab_sparks_section_reconciliation.py
3 passed

ruff check longworld/domains/researchlab/{simulate,events,queries,render}.py \
  tests/test_researchlab_sparks_section_reconciliation.py
All checks passed!

git diff --check -- <Sparks-owned code/config/test paths>
exit 0
```

Generated source inventory, candidate rows, preflight files, dense rankings,
and audit JSONL receipts are reproducibility evidence and are not recommended
for the source commit.
