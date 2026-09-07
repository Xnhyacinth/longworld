# P54 exact-source replay and PMS diagnostic candidate closeout

This work replaces the former five-role presence experiment with an executable
`public_law` reference-resolution task. It does **not** complete the original
proposal/first-reading/adoption/corrigendum oracle, and it adds no train-ready
inventory or production-qualified rows.

## Executed outcomes

| Trial | Actual outcome |
| --- | --- |
| Proposal/adopted-act manufacturer qualification change | Exact raw source spans derive diploma-route years 2→1 and experience-only years 5→4. The corrected serialized representation is rejected by a sufficient 16K raw-prefix witness. |
| First-reading qualification text | Frozen HTML literally contains `five three years`; the parser rejects this ambiguity instead of guessing amendment semantics. |
| Responsible-person investigational statement | Article 15 dynamically resolves Annex XV / Chapter II / Section 4.1. Natural-order geometry fits 32K, but the repository's standard full `_dossier_spread` layout puts complete source-bound support inside **10,485 tokens**. This layout is rejected. |
| PMS-to-risk-control chain | One source-attested v2 parent and **3 standard candidate-stage projections** have been materialized. Shared audit results remain separate from this source/projection closeout. |

The PMS task follows this actual source graph:

`Article 15 PMS duty → Article 10(10) → Article 83 → Article 84 plan → Annex III 1.1 → Annex I 3 → Annex I 4`.

Article 84 is resolved through its explicit reference **back** to the Article 83
system; its legal reference direction is preserved in the replayed graph.
Article 83's risk-management Chapter/Annex value constrains the plan's later
target. The parser therefore consumes Article 83 body content, rather than
requiring only its title. Chapter boundaries prevent a section in Chapter II
from inheriting an earlier Chapter I marker. The terminal priority parser ends
at the complete third priority; it does not require the neighboring duty to
inform users about residual risks.

## Source and rights boundary

All six earlier official representations were retrieved again and matched their
frozen byte lengths and hashes. Their raw bodies are now persisted under the
new authorized research scope. The broader
[official EUR-Lex legal notice](https://eur-lex.europa.eu/content/legal-notice/legal-notice.html)
was also retrieved and pinned: 134,752 bytes,
SHA-256 `650467a4a593796e872a9984ed2da27f6cba1659b90d03b6a38c642c4e19acaf`.
It provides the legal-document reuse scope and the metadata CC0 basis, subject
to its exclusions. No claim relies on the Commission-only decision as coverage
for every institutional document.

The registered PMS parent uses only the
[original MDR act representation](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0745)
and that notice. The source sidecar contains the exact source bodies and every
selected byte-span binding. Legal text normalization and JSON source-coordinate
framing are disclosed. Non-text assets are rejected. Research rights summaries
list the actual selected source IDs, byte ranges and span digests; unselected
background is not silently covered by a task-specific review table. This is a
technical source/reuse check, not a legal opinion or production fetch attestation.

The source-family inventory remains exactly
`["eur-lex.europa.eu/legal-content"]`. The legal notice is not counted as an
additional answer-evidence family.

## Counterfactual semantics

The v2 question explicitly asks for the proved reference path and terminal
source availability. Its CF changes **dataset visibility**, not legislation:
the terminal source record retains its real source coordinate, its body is
withheld, and the visible record declares `source_body_withheld=true`.

Full replay returns the resolved priorities and `AVAILABLE`. CF replay returns
the six-node proved prefix, the unresolved Section 4 target and `WITHHELD`.
Without the visible receipt, the availability is `MISSING`. The withholding
receipt and every prefix node remain necessary; an empty selection or a lone
origin cannot reproduce the CF answer. Recovery checks the authenticated parent
document digest before restoring any source body. Hidden task metadata cannot
substitute a different body during recovery.

## Materialized v2 artifacts

Directory:
`data/research/p54-eurlex-pms-registered-32k-20260906-v2/`.

| Product | State |
| --- | --- |
| `source_receipt.json` | Source-role signed; exact act/notice bytes bound |
| `parents.jsonl`, `parent_sidecar.json` | One source-attested candidate parent; 380 canonical source records |
| `projected/candidates.jsonl` | CF **32,047**, full **32,196**, ordered **32,196** exact tokens |
| `projected/TASK_REPLAY_SIDECAR_V3.json` | Standard projection ancestry and source commitments bound |
| `projected/REPLAY_PATH_REGISTRY.json` | Loader registry for the v3 sidecar |

Each projection has seven essential artifacts. The parent near-duplicate
sentence ratio is 0.0008 under the shared metric. These measurements do not
stand in for a release gate, independent dense audit, semantic shortcut proof,
or higher-band proof growth. No 32K-only release profile was introduced.
The earlier registered v1 parent is a superseded development diagnostic and
must not be projected, selected or counted as inventory.

## Reproduction and validation

Research reports can be regenerated with:

```bash
uv run python -m reports.p54_eurlex_source_span_oracle
uv run python -m reports.p54_eurlex_reference_statement
uv run python -m reports.p54_eurlex_pms_chain
```

The first command also supports `--fetch` to recheck the pinned official
representations. Registered generation uses
`reports.p54_eurlex_pms_registered_pipeline` and its v2 config under the existing
`scripts.run_with_local_probe_trust` source/candidate role wrapper. It refuses
to replace different frozen outputs. The standard projection command is
`scripts.project_task_candidate_views`; it produced the v3 sidecar and all
three rows above. Regeneration of the v2 parent reproduced its frozen bytes.

Focused P54 validation: **30 tests passed**, including the two independently
reproduced failures for hidden CF recovery text and cross-chapter scope. The
registry/GovInfo regression subset passed **27 tests**. Ruff, formatting and
`git diff --check` passed for the P54 implementation and its shared branches.
An earlier larger sweep exposed a pre-existing Finance test-fixture mismatch;
its resolution and final combined regression result belong to the root
closeout, not to these focused counts.

The shared files only add the EUR-Lex adapter branches. Existing exact-band,
source-attestation, view-derivation, raw-window, retrieval, complexity,
near-duplicate and release-profile requirements remain unchanged.

Independent read-only source/oracle/CF review finished **PASS**. It verified the
three projected signatures and source payloads, all 127 proper subsets per view,
visible-receipt necessity, normal recovery, and rejection of the two repaired
tampering/scope cases. This review does not replace the separately executed
shared dense/release gates.

Detailed machine-readable receipts are
`p54_eurlex_source_span_oracle_v1.json`,
`p54_eurlex_shared_layout_rejection_v1.json`,
`p54_eurlex_pms_chain_v1.json` and
`p54_eurlex_pms_registered_v2.json` in this report directory.
