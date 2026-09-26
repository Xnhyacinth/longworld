# P113 frozen-paper source-shape screen and new-source pilot

P113 adds a deterministic, four-worker **screen before full-reader compilation**.
It reads frozen source inventories with the existing P86 archive verifier, then
reuses P96's TeX source-file renderer, cross-file section/caption reference
resolver, and cue/label shortcut checks. It also reuses P104/P105's exact
answer-uniqueness rule and checks that masking either support span breaks the
visible resolver. It emits a per-work matrix and per-target rejection ledger.
It does not loosen P96/P105 or itself admit training tasks.

## Historical discrimination and scope

Pinned P104, P105, and P110 capacity indexes supplied **28 frozen works**:
5 P104, 16 P105, and 7 P110. The screen recovered 38 raw cross-file links,
then retained 10 source-visible targets. Work-level comparison to the prior
curated P104/P105 ledgers and P110 rejection result was 5 predicted-positive
and previously admitted, 23 predicted-negative and previously unadmitted,
with zero discordant works. The reason ledger records 14 reader-resolution
failures, eight alternate exact answer occurrences, three answers encoded in
labels, two unexpanded TeX answers, and one answer in the question cue. This
is an **in-sample gate replay**, not an estimate of precision on unseen papers.

The historical manifest is
`data/capability_records/p113_paper_shape_v2/manifest.json`, SHA-256
`e5cd4c8d1bf4351c829cf1909e263321c68ada2ce794335603e6125d79d9531c`.
The earlier `_v1` output predates an explicit `new_unlabeled_works` field and
is diagnostic only.

## New official-metadata source campaign

The planner used P110's pinned official arXiv Atom cohort and prior fetch
ledger. Of 18 metadata-selected works, nine had already been attempted; it
selected the first six unattempted works automatically across six category
queries (`astro-ph.EP`, `cs.DB`, `eess.SP`, `q-bio.GN`, `q-fin.MF`, `stat.AP`).
There were five train works and one eval work by stable work split. The P110
transport-classifying fetcher completed **6/6 works, 12 revisions**, using
8,869,585 archive bytes and no transport failures. All six Atom records lack
a reported license URI, so their content remains
`local_research_only_no_redistribution`, `train_ready=false`.

The four-process P110 source probe found **0/6 cross-file-reference works**:
four latest source trees had no matching cross-file link and two had source
paths the existing renderer rejects. The P113 screen independently retained
zero targets, and the route emitted zero P96 source families. No empty or
unproven P96 reader shard was created. This is direct evidence that more
arXiv metadata categories alone do not make the current cross-file recipe
scale. The new-source probe manifest SHA-256 is
`3f89db3d9ced85774850426871a6338c9ca0392d664c2a403f4effeb1f24f321`;
the routed manifest SHA-256 is
`7fb0f229dde77ff1218c0d748bd093b9f5d5532dfeb820d38a940868b224fed2`.

The same six frozen pairs also went through the existing P86 **revision
alignment** compiler. Five had no quality prose hunk. One `stat.AP` work
(`2607.26297`, train split) produced one candidate that passed exact-line
uniqueness, visible reader replay, both version-line and source-record
deletions, native assistant mask, unified normalization, and independent
full-reader all-mask. The final chat has **80,455 tokens**, including **313
assistant-supervised tokens**. Its two versioned evidence spans have a
**47,303-token bounded extent**. A second native build produced identical
bytes for all seven native files. This is one task from one source world;
the span is a bounded certificate for the explicit version comparison,
not a global shortest semantic proof or training-gain result. The native,
unified, and all-mask manifest SHA-256 values are respectively
`e8070147414b59f24d0a3647506a7a7290d9f35c86032df32dd7fc4e5e3cac59`,
`337a538c1d29d641f99eb4c18b41bd660b4a52b57571f962b1f383e50b81a2c2`,
and `0c8e492f6209ecb34f9cd512eb607549dcf20a94044f5e3408774c737ba33de0`.
This local research candidate has not been promoted to a train-ready pack.

## Reproduce and inspect

```bash
cat data/capability_records/p113_paper_shape_v2/manifest.json
less -R data/capability_records/p113_paper_shape_v2/work_matrix.jsonl
less -R data/capability_records/p113_paper_shape_v2/target_ledger.jsonl
cat data/capability_records/p113_paper_sources_v1/manifest.json
less -R data/capability_records/p113_paper_probe_v1/capacity_index.jsonl
cat data/candidates/p113_paper_revision_native_v1/manifest.json
less -R data/candidates/p113_paper_revision_native_v1/rejected.jsonl
less -R data/candidates/p113_paper_revision_native_v1/audit.jsonl
cat data/candidates/p113_paper_revision_mask_v1/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/p113_paper_shape.py --config configs/p113_paper_shape_v1.json --output-dir data/capability_records/p113_paper_shape_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_paper_cohort.py --config configs/p113_paper_cohort_v1.json --output-dir data/capability_records/p113_paper_cohort_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p110_paper_acquire.py --config data/capability_records/p113_paper_cohort_v1/acquire_config.json --output-dir data/capability_records/p113_paper_sources_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p110_paper_probe.py --config configs/p113_paper_probe_v1.json --output-dir data/capability_records/p113_paper_probe_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_paper_shape.py --config configs/p113_paper_shape_new_v1.json --output-dir data/capability_records/p113_paper_shape_new_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p113_paper_route.py --config configs/p113_paper_route_v1.json --output-dir data/capability_records/p113_paper_route_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p110_paper_revision_plan.py --config configs/p113_paper_revision_plan_v1.json --output-dir data/capability_records/p113_paper_revision_plan_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/merge_p86_native_candidates.py --paper-dir data/candidates/p113_paper_revision_native_v1 --output data/candidates/p113_paper_revision_unified_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py data/candidates/p113_paper_revision_unified_v1 --all --output data/candidates/p113_paper_revision_mask_v1 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p113_paper_*.py
```

The source-shape screen deliberately labels new works as unlabeled during
calibration. Raw TeX is not equivalent to the rendered paper: macros,
comments, and compilation branches remain a separate validity limit. The
two source-path parser failures are recorded rather than silently rewriting
source archives. A future paper expansion should route more than this one
TeX shape (for example tables, definitions, and within-document evidence)
through independent final-reader checks; this pilot does not claim those
capabilities exist.
