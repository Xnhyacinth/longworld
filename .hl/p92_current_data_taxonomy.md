# P92 scaling baseline: what the bank actually contains

Source of counts: `data/candidates/p91_final_candidate_refs_v2/candidate_refs.jsonl`
and its pinned manifest. These are **candidate reader views**, not approved
training examples or independent source documents.

| Existing lane | Views | Source/world groups | Distinct task IDs within lane | Current material and primary operations |
| --- | ---: | ---: | ---: | --- |
| Controlled simulation | 4,516 | 1,029 | 4,516 | Structured state/events, alias, join, aggregate, as-of, rule holdout |
| Grounded simulation | 12 | 1 | 4 | One real RFC rule source plus simulated connection records |
| Real code workflow | 817 | 7 | 817 | Repository/workflow records, merge/file/CI set operations |
| Real finance | 1,682 | 8 | 1,682 | Reports and financial tables, lookup, delta, aggregate, ratio, filter |
| Real paper revision | 7 | 3 | 7 | Revision and cross-file alignment in real papers |
| Real Wiki | 1,054 | 29 | 909 | Table lookup, paired-table join, interval scan, numerical set scan |

The index totals **8,088 views, 7,935 global independent semantic tasks,
1,077 groups**, 18 domain labels, 40 topic labels and 37 operation labels.
The 1,029 simulated groups should not be described as 1,029 independent
real-world topics. Likewise, several length views of one task are not new
semantic tasks. The 18 domain and 40 topic labels are a taxonomy count, not
evidence of hundreds of independent fields.
Only **154 groups** contain more than one operation: 132 controlled
simulation, 8 finance, 7 code, 6 Wiki and 1 grounded simulation. Thus the
shared-world multi-capability claim is still strongest in structured sources,
not broad natural documents.

Current coverage by source type is narrow: Wiki pages and tables, finance
reports, paper revision text, repository/workflow records, structured state,
and one real-rule/simulated-state hybrid. There is **no admitted book or broad
natural-report corpus, general free-form source-grounded QA pipeline, or
agentic action/feedback trajectory product** in the current index. L1/L2/L3
mechanisms exist, but natural semantic expression, cross-document support,
L4 transfer beyond narrow simulated rule holdout and L5 feedback are not
demonstrated at scale. A task's operation label alone does not certify reader
text necessity or model learning.

| Requested material | Current status | Suitable next operation signature |
| --- | --- | --- |
| Natural documents | Wiki pages/tables, narrow paper revisions | Evidence span, connected fact, complete set |
| Reports | Finance filings only; broad reports absent | Period/unit/scope-aligned compare and aggregate |
| QA | Present as reader output contract, not an independent source type | Known-gold answer with citation and blind reader check |
| Code | Seven repository/workflow groups; no general call trace | Symbol/config/version links and bounded impact closure |
| Agentic | No action-feedback trajectories in this bank | Separate history→action→feedback contract; do not relabel reader QA |

The P91 source-aware selection contains 1,169 tasks from 506 groups and
preserves 176 observed split × kind × operation × actual length cells. It
still assigns 238,308 of 273,137 supervised tokens (87.25%) to controlled
simulation. Selection and actual training composition are separate. Every
current index and selection manifest is `train_ready=false`; no model-gain
claim follows from these counts.

Audit labels also need separation from verified necessity: 5,224 of 8,088
views have no `dependency_status`, including much of the simulated bank;
3,588 have topic `unknown`. The 1,682 finance views are marked
`native_candidate` rather than reader-text necessity. These are metadata and
validation gaps, not grounds to re-label them as proven dependent samples.

The scaling target is an **explicit support matrix**, not a blind Cartesian
product. Source content must establish the operations it supports; the same
source/world can then be reused across legal tasks, renderer styles and
length/position layouts. Count accepted independent tasks, source identities,
actual token distributions, support/rejection reasons and dependency checks
separately. Adding a domain word or random identifier without changing
source semantics does not add a new domain or world mechanism.
