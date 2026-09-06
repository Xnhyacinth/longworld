# P56 six authentic relation-set path

Date: 2026-09-06
Status: in progress; no inventory delta

## Why mixed-01 is not train-ready

Nine mixed-01 rows completed shared audit and row-level promotion, but the
immutable `p52-govinfo-bill-disposition-probe-1-v1` gate counts **distinct
`authentic_source_relation_id` values**, not edges or requested clauses. That
hash is `sha256(authentic_source_relation_edges)[:20]`. Mixed-01 is three
bands × three views of one world, so it contributes **3** identities:

| Band | Edges | `authentic_source_relation_id` |
| --- | ---: | --- |
| 32K | 4 | `b14fea02d64136aeffe7` |
| 64K | 8 | `536db491a970017819a9` |
| 128K | 16 | `0498a59e3c98369f9a80` |

The gate requires 6. The profile also requires `expected_promoted_worlds=1`
and one `government_legislation` world. Relation-set identifiers were not
rewritten. The threshold was not lowered.

## Honest 6-ID construction

Mixed-03 is the same H.R.4366 EAS→EAH oracle with a different nested request
prefix (6→12→24). Its nine projected rows already have three **different**
identities:

| Band | Edges | `authentic_source_relation_id` |
| --- | ---: | --- |
| 32K | 6 | `eaa89def1d83a4224e74` |
| 64K | 12 | `d41f43272c79b3e4c784` |
| 128K | 24 | `a6a638f417903a739899` |

Together mixed-01+03 are exactly six authentic relation-set identities. They
are correlated tasks from one bill and must occupy **one source-connected
world**, not two independently selected worlds. Relabeling `world_id` in place
after signing would break attestations. The permitted follow-through is:

1. Dense-rank and shared-audit mixed-03 without changing edges or IDs.
2. If those nine rows pass, emit a **new uniquely named** candidate/promotion
   directory whose signed rows keep the six relation-set hashes but share one
   `world_id`.
3. Select, promote, and quality-gate that single world. Fail closed on any
   shortcut window, missing proof growth, or world-count mismatch.

Mixed-02 remains rejected (16K raw window answers the task) and is not a
filler source of IDs.

## Current execution

CPU dense ranking of mixed-03
(`sentence-transformers/all-MiniLM-L6-v2@1110a243`) is running against
`data/candidates/p56_govinfo_mixed_dispositions_20260906/mixed-03/shared_views/candidates.jsonl`.
Shared audit, union re-attestation, and B5 are not started until ranking
finishes.
