# P138 versioned capability coverage (2026-09-27)

P138 inventories the pinned P137 candidate bank and P137 v3 selected refs.
The report is `data/candidates/p138_capability_coverage_v1/report.json` and the
explicit taxonomy is `configs/p138_capability_taxonomy_v1.json`. Both scopes
count **semantic tasks separately from length views**. Capability names are
task-operation families; they are not independent cognitive mechanisms or a
claim of model improvement.

Report SHA-256: `f991134165e51482ab0398ed2e01c2cd36d627c8e06708039a4283d55a70ea5f`.
Taxonomy SHA-256: `263b7a10ebffd575bd3f3c75f5d67a0b1cc4f7d052b09d21370523c32325c4c3`.

| Scope | Views | Independent tasks | Typed groups | Known multi-capability groups | Input tokens | Supervised tokens | Null dependency status |
|---|---:|---:|---:|---:|---:|---:|---:|
| Candidate | 14,626 | 13,446 | 1,790 | 548 | 1,152,505,808 | 3,646,437 | 7,722 |
| Selected | 1,797 | 1,797 | 470 | 142 | 111,040,888 | 114,185 | 0 |

The selected source-kind views are real Wiki 719, books 511, finance reports
192, code workflow 69, controlled simulation 286, and paper source 20. P137
v3 selected no grounded simulation or paper revision rows. Selected final-chat
length bins are <32K 514, 32–64K 574, 64–128K 554, and 128–256K 155; all 1,797 metadata
length labels match bins recomputed from final-chat token counts. Candidate
and selected label vocabularies are 34/180 and 30/161 domain/topic strings;
these strings do not measure independent semantics.

The highest-volume selected declared capabilities are cross-chapter trace 380,
complete-set scan 306, locate 305, aggregate/compare 291, cross-chapter
complete set 126, state aggregation 93, and state complete set 81.
`p125_joint_multi_operation` remains a composite reuse route and contributes
**no new capability**. Its label cannot by itself turn a one-capability
world into a multi-capability world. Groups counted as multi-capability have
at least two different mapped non-composite capability families. This is a
typed source-group statistic, not a certified source-component count.

Only P133's two explicit state-transition identities are counted as native
mechanisms here: `partial_reversal` has 128 candidate reader QA / 94 selected;
`authorization_hold` has 116 candidate reader QA / 80 selected. Their routes
require the pinned source name, world-ID prefix, topic, evidence profile and
operation. P133 policy rows belong to a separate action contract and are not
in this reader bank. Other source kinds receive task capability labels but
**no fabricated native-mechanism count**. The matching logic leaves unknown
or ambiguous future rows explicit. This snapshot's pinned rows all match one
declared route; it is not proof that the taxonomy covers future data.

The report pins the taxonomy, candidate index manifest/refs and selection
manifest/refs. It runs the existing bank and selection verifier before counting,
then checks selected totals against the P113 coverage calculation. It does not
independently re-parse every original source or prove reader-text necessity.
The source-component audit and final-reader materialization remain separate
checks. P137 v3 required a recorded dependency status: the selected null
count is zero, but a present status is **not** proof of complete reader-text
necessity. Compared with the broader v2 selection (1,814 tasks, 564 groups,
292 null statuses), this gate changes the source mix and increases P133 QA
selection while excluding some other source kinds. `train_ready=false`.

Reproduce:

```bash
UV_LINK_MODE=copy uv run --offline python -m scripts.p138_capability_coverage --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p138_capability_coverage.py
UV_LINK_MODE=copy uv run --offline ruff check scripts/p138_capability_coverage.py tests/test_p138_capability_coverage.py
cat data/candidates/p138_capability_coverage_v1/report.json
```
