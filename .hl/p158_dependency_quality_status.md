# P158 pinned dependency-quality inventory (2026-09-27)

P158 grades the **recorded bounded intervention** for the frozen P156 v3
reader selection. It does not certify unrestricted necessity, a globally
minimum evidence window, or model improvement. The machine-readable matrix is
`data/candidates/p158_dependency_quality_v1/report.json` (SHA-256
`90a571faab1fc5a3598c3aed98daee129a85f8e48e36dac6c279432ec0f493cd`).
It has one row for each nonempty **source kind × declared capability × actual
final-chat length × intervention class × exact dependency status** cell.
P158 pins P156 index/selection manifests and refs, the P156 taxonomy, P152
selection refs for comparison, and five native proof files. The existing P113
bank/selection verifier runs before counting.

| Measure | P156 v3 selected reader set |
| --- | ---: |
| Independent tasks/views | 2,696 / 2,696 |
| Real Wiki / books / finance / code / paper | 1,028 / 664 / 79 / 69 / 28 |
| Controlled state simulation | 828 |
| Bounded single or set edit / multiple support edits / scoped shortcut screen only | 1,806 / 889 / 1 |
| Physical 128–256K views | 157 |
| P152 retained / replaced out / newly selected | 2,428 / 322 / 268 |

Both grade names refer to intervention *shape* and its stated scope. Even the
multiple-support class can leave paraphrases, alternative comparisons or
other reader-visible proofs unsearched. The report preserves all 23 exact
status strings; a future unclassified string makes replay fail rather than
being assigned a reassuring grade. Task capability names come from the pinned
P156 taxonomy, not from a test of distinct cognitive mechanisms.

Final-token distances are deliberately sparse and separated by meaning:

| Native measure | Selected views with a value | At least 16K (where inspected) |
| --- | ---: | ---: |
| P133 decisive fact to question | 828 | 828 |
| Evidence extent | 156 | 38 |
| Last support to question | 125 | 1 |
| Gap between specified supports | 23 | 16 |

For P133, the distant **selected decisive** event or record is checked against
its pinned proof, world hash, task ID and final-token offsets. Its proof says
alternative supports and global minimum proof are unsearched. For P154, 114
Wiki typed-grid tasks have native cell token spans, but none has a 16K
evidence extent; these are bounded row-cell edit tasks, including rows that
did not match. The nine P146 paper proofs and direct metadata for two P127
paper views yield bounded reference/target support separations; exact answer
string and parser-recognized labels are the searched alternatives. Direct
code metadata supplies 31 evidence extents and 12 review-to-diff witness
gaps. These metric columns cannot be added as distinct task counts because
one view can carry multiple measures.

The 157 physical 128–256K views are 113 books, 17 Wiki, 13 finance, 12 code,
and two paper. P158 can substantiate a >=16K extent or specified support gap
in **nine** of these views (five code extents, three code support gaps,
one paper extent/gap), using its pinned metrics. The other 148 are *unmeasured by this inventory* on
that specific distance criterion. This does not negate their separate native
audits; it identifies the missing common final-token evidence interface.
No controlled state world in P156 v3 reaches 128K.

Shared-evidence overlap is computed only where native identities exist. Among
2,175 within-source-group, cross-capability task pairs, 745 have comparable
native witness sets; 388 share at least one recorded witness. Controlled
simulation contributes 602 measurable pairs and 295 shared selected decisive
fact IDs. P154 Wiki contributes 143 measurable pairs and 93 shared examined
source HTML cells. The remaining 1,430 pairs—including all book, code and
finance pairs in this inventory—are **unmeasurable**, not disjoint. Sharing a
selected decisive fact or an examined table cell does not establish that the
full minimal evidence sets are the same. Paper has no cross-capability pair
in the pinned selection.

This result gives the next compiler a precise target: propagate audited
source-span IDs and final-chat token positions into the common candidate
contract for real books, Wiki, finance, code and papers; then compare
alternative supports on the actual reader bytes. Simply increasing physical
length or adding domain/topic labels cannot repair this measurement gap.
`train_ready=false`; P158 did not start training or change the selection.

Reproduce:

```bash
UV_LINK_MODE=copy uv run --offline python -m scripts.p158_dependency_quality_inventory --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p158_dependency_quality_inventory.py
UV_LINK_MODE=copy uv run --offline ruff check scripts/p158_dependency_quality_inventory.py tests/test_p158_dependency_quality_inventory.py
cat data/candidates/p158_dependency_quality_v1/report.json
```
