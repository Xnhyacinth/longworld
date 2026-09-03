# P24 ResearchLab eLife 94586 exact-64K materialization closeout

## Result

The first materialization is retained as a rejected predecessor: its 369-unit
pack collapsed to 368 representatives in a final 0.90 near-duplicate audit.
It is recorded in
`reports/p24_researchlab_elife_94586_64k_v1_near_dup_reject.json` and is not
eligible for downstream use.

One successor parent candidate was materialized in the independent v2 output
from the verified P21/P22 eLife 94586 v1/v2 inventory and bound to the P23 v1
replay sidecar. The final
Qwen/Qwen3.5-4B prompt is **64,512 tokens**, inside the required
`[64,000, 65,536]` band. The document context contains 181,122 UTF-8 bytes;
64,404 prompt tokens are the measured real-source marginal
(`real_source_token_ratio=0.99832589`).

The accepted v2 pack contains 367 unique and near-distinct units:

- 34 direct author-response paragraphs;
- 30 referee-report paragraphs;
- 175 v1-to-v2 `delta_after` semantic blocks;
- 123 v1-to-v2 `delta_before` semantic blocks; and
- 5 exact task evidence spans.

Five exact evidence spans are `causal_gold`, and the remaining 362
review/response/change units are
explicitly `natural_background`. They are not relabelled as task evidence.
The near-duplicate v1/v2 DOI strings remain bound in the source sidecar but are
not packed as separate documents. Two support representatives conflicting
with fixed evidence were also removed before subset selection. The final pack
has 367 representatives from 367 artifacts and zero pairs at the 0.90 gate.
No unchanged paper body, bibliography, duplicated text, synthetic filler,
derived view, or 128K attempt was admitted.

The P20/P21 capacity method reproduced 109,213 exact-deduplicated tokens and
76,142 representatives after the unchanged 0.90 near-duplicate collapse.

## Essential evidence

| Evidence | Record | Source chars | Text SHA-256 |
| --- | --- | ---: | --- |
| `controlling_review` | `elife:94586:v1` | 891825–892446 | `2bd025181bb190f1f4414d097fed845a38ca2270386082c0e0d9516e52fc4a58` |
| `direct_author_response` | `elife:94586:v2` | 1180636–1181632 | `d90dcbad51abd146184049409f04c99bf56139eb0e7e1224dc1d7b921e87e3cd` |
| `body_figure_fig5` | `elife:94586:v2` | 111087–111101 | `e5318f16a97c3738ad3a88da31b9fa7d29ce8443f36de6de16f7656dcf359466` |
| `appendix_APP9` | `elife:94586:v2` | 841461–841475 | `456d375bd28a5ae1a526c1b0fb202766bfa7b384798676308a3752817f155248` |
| `appendix_table_tbl3` | `elife:94586:v2` | 960321–960342 | `399024bb9dbe0ae58a821766999e8bec90d622d2e866d9de4709cbdeecb25fa4` |

The complete ordered 367-unit ledger is embedded as `artifact_manifest` in
`parents.jsonl`; its canonical SHA-256 is
`b666a3962bac915d78dcf68368a37c4a848af3c8cfc19cc7658f92f2b7feedfc`.

## Bindings and deterministic rebuild

- Source inventory SHA-256:
  `c7a1959d34b757053640f5f122a249f13180d00bed24fcb6badf8378fdd43ff5`
- Task SHA-256:
  `5581930f025687ab0237fe3efc2d8d53f8eb97cbc1265c64684e4b0331ae9fe0`
- Candidate content commitment:
  `a2bd02e708ea99014d51bbe1217ebbcce0feeec15eb615659557902e0799d19b`
- Sidecar exact-byte SHA-256:
  `0a3afdbe9ada4febcf5bf711f92f03be605656bbe729b7f3e90a10ba8cecc8a9`
- Parent JSONL exact-byte SHA-256:
  `46bcabc4e58c19c94e6a3dc02663f03c712e13a185ba36590ffa9f446c6b7a1e`
- Generation receipt exact-byte SHA-256:
  `61ab5a25caa8f12e1d25e7ca1b5ad97b02df2b375393f4d6cc3b0e1fb6e2fbcd`

Two v2 executions with identical config and the same separate source/candidate
probe-role identities
produced the same three hashes. Independent loading then verified the source
sidecar signature, candidate signature, candidate commitment, 64,512-token
prompt, strict `VERIFIED_IMPLEMENTED` replay, and zero unredacted email
patterns. The embedded final-pack audit also verified zero 0.90 near-duplicate
pairs. The sidecar retains the exact six-redaction receipt and all four
authentic relation kinds.

## Boundary and next blocker

This is a probe-attested parent candidate, not a training row:
`train_ready=false`, `production_eligible=false`, `selected=false`, and
`promoted=false`. It is not counted toward release inventory or ResearchLab
quota.

The exact next blocker is an eLife-specific standard-view projection contract:
strict candidate replay, deterministic remove-delta counterfactual,
review-response-revision chronology, and canonical semantic identifiers must
be added to the closed projection adapter set before full/cf/ordered views can
be generated. Dense audit and promotion remain downstream of that projection
gate.
