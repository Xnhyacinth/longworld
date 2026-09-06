# P56 six authentic relation-set path

Date: 2026-09-06
Status: fail-closed at selection; no inventory delta

## Why mixed-01 is not train-ready

Nine mixed-01 rows completed shared audit and row-level promotion, but the
immutable `p52-govinfo-bill-disposition-probe-1-v1` gate counts **distinct
`authentic_source_relation_id` values**. Mixed-01 is three bands × three views
of one world, so it contributes **3** identities. The gate requires 6.

## Mixed-03 and the shared-world regeneration

The original mixed-03 projections passed shared dense audit 9/9
(`no_shortcut=true`, proof events 6→12→24). A new uniquely named generation
`p56_govinfo_mixed_union_20260906` reused the frozen mixed-01/mixed-03 seeds
and prefixes, set
`shared_world_id=govinfo-118-hr4366-eas-eah-mixed-union-20260906`, and used
`task_instance_id` so parent query ids did not collide.

That product has one world id, 6 unique parent query ids, 18 projected views,
18 dense rankings, and 18 shared audits, all `no_shortcut=true`. The six
gate-facing identities are unchanged from the original schedules:

| Schedule | 32K | 64K | 128K |
| --- | --- | --- | --- |
| mixed-01 | `b14fea02d64136aeffe7` | `536db491a970017819a9` | `0498a59e3c98369f9a80` |
| mixed-03 | `eaa89def1d83a4224e74` | `d41f43272c79b3e4c784` | `9ef594600e59fba9f253` |

Preflight accepted 18/18. Selection then failed:

`insufficient worlds with immutable world lineage: ... duplicate_cells=...+mixed_source_binding`

## Why 3+3 cannot enter this profile

A world may occupy each `(length_bucket, view, first)` cell only once. The
profile requires 32K/64K/128K × full/CF/ordered, so one world has **9 cells**
and at most **3** authentic relation-set identities (views of one parent share
edges). Two 3-band schedules need 18 cells. Putting them in one `world_id`
duplicates every cell; putting them in two `world_id`s violates
`expected_promoted_worlds=1` and the `government_legislation` quota of 1.

Relation-set hashes were not rewritten. `min_real_source_relations=6` was not
lowered. Mixed-02 remains rejected and is not a filler source of IDs.

## Permitted next work

A later GovInfo world can reach 6 identities only by adding **new bands or a
new source-connected parent family that does not reuse these 9 cells**, or by
an **explicit new profile** that admits two worlds while keeping the 6-identity
threshold. Neither change is in this closeout. Parallel entity expansion should
move to a different bill/transition or another domain rather than retuning this
layout.
