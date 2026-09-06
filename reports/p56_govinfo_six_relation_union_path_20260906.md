# P56 six authentic relation-set path

Date: 2026-09-06
Status: mixed-03 shared audit passed; single-world regeneration not yet gated

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

## Mixed-03 shared audit

CPU dense ranking of the existing mixed-03 projections finished 9/9
(`all-MiniLM-L6-v2@1110a243`, rankings SHA-256
`cd2118ad828d2533dcdddecdc15d45b9b08232aea46a1a09f6c6552fac062509`).
Shared `create_task_dense_audit` then passed **9/9** in 452s. AUDIT_MANIFEST
`dense_audit_complete=true`, audits SHA-256
`c3284db0bf4bb17f5a9d5b03fab26caebadb6b7973f5e99ef967f34727ff97c3`.

Every mixed-03 row has `no_shortcut=true`, contiguous/local/artifact windows
insufficient, BM25/TF-IDF top-k insufficient, full-pool strict replay
sufficient, and near-duplicate sentence ratios 0.0285–0.0380. Proof events
grow 6→12→24. Gate-facing authentic relation-set identities:

| Band | Essential events | `authentic_source_relation_id` |
| --- | ---: | --- |
| 32K | 6 | `eaa89def1d83a4224e74` |
| 64K | 12 | `d41f43272c79b3e4c784` |
| 128K | 24 | `9ef594600e59fba9f253` |

Together mixed-01+03 are exactly six authentic relation-set identities. They
are correlated tasks from one bill and must occupy **one source-connected
world**. Relabeling `world_id` in the existing signed rows would break
sidecar commitments, `query_id` canonicalization, rankings, and audits.

## Query-id collision

Parent `query_id` is `{world_id}:{bucket}:{view}:first`. If mixed-01 and
mixed-03 keep those three bands under one `world_id` without an extra
instance token, the 32K/64K/128K full parents collide. The permitted
follow-through is a **new uniquely named** generation that:

1. Reuses the frozen mixed-01 and mixed-03 seeds, prefixes, and CF anchors.
2. Sets `shared_world_id=govinfo-118-hr4366-eas-eah-mixed-union-20260906`.
3. Sets `task_instance_id` to `mixed-01` / `mixed-03` so query ids remain unique.
4. Leaves authentic relation-set hashes and `min_real_source_relations=6`
   unchanged.

Mixed-02 remains rejected and is not a filler source of IDs.

## Next command

```bash
uv run python reports/p56_govinfo_mixed_dispositions_20260906.py \
  --plan configs/p56_govinfo_mixed_union_20260906.json \
  --output-dir data/candidates/p56_govinfo_mixed_union_20260906
```

Then project, dense-rank, shared-audit, select, promote, and quality-gate that
single world. Fail closed on any shortcut window, missing proof growth, or
world-count mismatch.
