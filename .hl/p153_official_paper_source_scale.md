# P153 official paper-source scale probe

P153 separates source discovery, source acquisition, and reader-task admission.
The cohort planner replays 24 previously frozen official arXiv Atom pages. It
does not list domains, category IDs, topics, or paper IDs by hand. It proposed
16 distinct, previously unfrozen papers from 16 official category queries
(11 train, five eval); the broader metadata ledger contains 923 eligible rows,
1,359 single-revision rows, 18 already-frozen rows, and six unavailable query
pages. These are **metadata counts, not acquired source groups or tasks**.

The actual P105/P110 provenance-preserving source fetch, including one delayed
resume, returned HTTP 429 on the first work's first revision both times. Its
verified result is `transport_blocked`, zero archives, zero source bytes, and
zero new source groups. No P153 task was compiled from that cohort. Both calls
used the existing globally paced single-connection arXiv transport; no further
retry or alternate unverified source endpoint was used.

As an offline fallback, the P146 reference compiler was replayed with four
processes over all 29 parsable P127 paper works. With P139 as the prior bank,
the wrapper reproduced the existing P146 nine tasks from six source groups,
including one 128K case, and passed the unified reader/mask verification. The
sample IDs overlap P146 **9/9**, so this is a positive compiler diagnostic,
not new data. With the newer P149 prior bank and a raised eight-task cap, it
produced **zero net-new tasks and zero new groups**. The rejection ledger
records 19 prior exact-answer overlaps; remaining candidates fail the same
cue/label leakage, unique target, alternate visible answer, or final token
distance gates. Increasing per-work task count alone does not scale this
paper recipe.

| Frozen stage | Source works | Independent new tasks | Final reader tokens | Status |
| --- | ---: | ---: | ---: | --- |
| Official Atom cohort plan | 16 proposed | 0 | 0 | Discovery only |
| P105 source acquisition | 0 acquired | 0 | 0 | HTTP 429 transport stop |
| P127 reuse vs P149 | 29 parsed prior works | 0 | 0 | Quality/prior overlap |
| P139 diagnostic | 29 parsed prior works | 0 beyond P146 | 480,293 duplicate tokens | 9/9 P146 sample overlap |

The P146 compiler still enforces unique visible references and targets, cue
and answer leakage checks, two reader-text deletions, pinned final-chat token
offsets, assistant-only masks, source split, and bounded alternative support.
These guarantees apply to the reproduced nine diagnostic rows; no new P153
row reached these gates. `train_ready=false` throughout. Source licenses and
model-gain claims are unchanged.

Inspect the authoritative local receipts:

```bash
cat data/candidates/p153_paper_source_cohort_v1/manifest.json
less -R data/candidates/p153_paper_source_cohort_v1/metadata_decisions.jsonl
cat data/candidates/p153_paper_source_fetch_v1/manifest.json
cat data/candidates/p153_paper_reference_reuse_v2/manifest.json
less -R data/candidates/p153_paper_reference_reuse_v2/decision_ledger.jsonl
cat data/candidates/p153_paper_reference_diagnostic_v2/manifest.json
```

Reproduce the completed checks from the repository root:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p153_paper_source_cohort.py --config configs/p153_paper_source_cohort_v1.json --output data/candidates/p153_paper_source_cohort_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p110_paper_acquire.py --config data/candidates/p153_paper_source_cohort_v1/acquire_config.json --output-dir data/candidates/p153_paper_source_fetch_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p153_paper_reference_batch.py --config configs/p153_paper_reference_reuse_v1.json --output data/candidates/p153_paper_reference_reuse_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p153_paper_reference_batch.py --config configs/p153_paper_reference_diagnostic_v1.json --output data/candidates/p153_paper_reference_diagnostic_v2 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p153_paper_source_cohort.py
UV_LINK_MODE=copy uv run --offline ruff check scripts/p153_paper_source_cohort.py scripts/p153_paper_reference_batch.py tests/test_p153_paper_source_cohort.py
```

The source-planner receipt SHA-256 is
`155bbcca76384ac4409a1ba527ea285b59e8caff4c7aa91d0e97f3569f552a68`;
the verified transport-stop SHA-256 is
`641cef3806a05a36b470b07dd9491c5ad4d81086d3a6abee46634b9d4f3cab34`.
The P149-relative reuse rejection receipt SHA-256 is
`f2ee28606a8c920df9eea4fd2e4ecbcda309895ee6da3cffc5aa1f2018cd2bd3`;
the positive P146-duplicate diagnostic receipt SHA-256 is
`04f3529be68dced1750b8521c449725854fb97ff60c433fb38bd1433c544a557`.
