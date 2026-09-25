# P105 multi-category arXiv source intake and paper-reference QA

P105 is a bounded, resumable source-shape-first test of the existing P96/P104
raw-TeX reference recipe. It discovers works from ten arXiv category queries;
no paper ID, question or answer is manually written into the batch. Each
selected work is deduplicated against the pinned P86/P104 source and candidate
universe, assigned a hash-stable work-level train/eval split, and requests its
last two source revisions. Metadata requests and source requests use one
connection with a global minimum 3.2-second start interval. Source archives
are published atomically by `fetch_paper_workflow.py`; P105 subsequently
rechecks inventory and archive SHA-256, revision IDs, split and license status.
The output-directory `flock` rejects concurrent fetch/probe writers, and
`fetch --verify-only` rechecks the completed source/rejection ledger without
network or rewriting the manifest. The same source bytes and four-process
probe can be replayed with `probe --verify-only`.

The pinned metadata cohort at
`data/capability_records/p105_paper_catalog_v1/manifest.json` has SHA-256
`75c1ece3f0fe1a766aaf4433d0931880c3dea2fd3328c3d0af6cf131cfa73a30`.
Ten Atom pages returned 1000 entries and 879 distinct works. The work-level
prior universe contained ten already frozen IDs; the selection traversal
recorded 44 single-revision rejections before selecting 20 new works, two
per query (14 train, 6 eval). The selected Atom records reported no license
URI. `content_use=local_research_only_no_redistribution` is therefore an
explicit operational boundary, not a claim that the sources can be released
or used for an authorized training run.

| Stage | Gross to net | Evidence |
| --- | ---: | --- |
| Selected works | 20 | ten category queries, two each |
| Frozen works / revisions | 16 / 32 | source manifest SHA-256 `8be598697c29b11f0425f33ea8cee069d44120de153aa5de6392eb8e1d1ff58c`; 162,654,278 archive bytes |
| Source-shape works | 4 | four-worker probe manifest SHA-256 `ff7c85db0baa86cb7c74fcf95200494e38362410e8d147d80b35e12bb0693f7f` |
| Raw cross-file links | 30 | latest-revision TeX parser candidates |
| P96 native / unified tasks | 12 / 12 | train 9, eval 3; 12/12 native and unified all-mask |
| Curated tasks | **8** | train 7, eval 1; final manifest SHA-256 `d0bbb28b070f7ae11029a7f79f4d5a0d1588f4fdadbccaf193b1023fcfc0c481` |
| Curated final all-mask | **8/8** | receipt SHA-256 `3c70f46b59ce6ddd4a9b3f45656029cd386d3c8294120ed7f65dcbf3302f952c` |

Four selected works did not yield a frozen two-revision source packet. One
source response had an invalid content type; three were reported by the
existing fetcher as empty **or** over its 64 MB per-response limit. That
combined error does not reveal which branch applied. Two further frozen
sources failed the source-path parser, and ten lacked an eligible cross-file
reference. These are separate acquisition and source-shape outcomes, not
failed QA labels. The P96 native compiler rejected ten of the 30 links:
eight final-reader resolver disagreements, one answer-encoded reference
label, and one under the minimum evidence extent. The P105 final-reader
quality gate rejected another four of the 12 raw QA: two alternative visible
title occurrences, one unexpanded TeX command in the gold answer, and one
gold answer whose whitespace-normalized text did not exactly match the
visible target span. The last case is explicitly logged rather than changing
the reader or silently accepting a nonliteral answer.

| arXiv query | Selected | Frozen | Cross-file source works | Curated tasks |
| --- | ---: | ---: | ---: | ---: |
| cs.CL | 2 | 2 | 1 | 2 |
| cs.LG | 2 | 2 | 1 | 1 |
| stat.ML | 2 | 2 | 0 | 0 |
| cs.CV | 2 | 2 | 1 | 3 |
| cs.AI | 2 | 2 | 1 | 2 |
| cs.RO | 2 | 2 | 0 | 0 |
| physics.med-ph | 2 | 1 | 0 | 0 |
| q-bio.MN | 2 | 2 | 0 | 0 |
| econ.EM | 2 | 1 | 0 | 0 |
| astro-ph.CO | 2 | 0 | 0 | 0 |

The final eight tasks use four distinct paper worlds and two operations:
six cross-file section references and two cross-file caption references.
Four readers are below 32K, one is in 32–64K, and three are in 64–128K;
their exact full-chat lengths range from 22,225 to 70,641 tokens. The final
Qwen3.5-4B chat template has 360,213 full-chat tokens and 172
assistant-supervised tokens: train 314,753/158 and eval 45,460/14. In the
quality ledger, the two selected evidence spans cover 3,432–22,165 tokens,
and the last selected evidence ends 1,953–13,650 tokens before the question.
Those measurements are from the final prompt, not raw character positions.
The ten-query source breadth did **not** produce ten-query task breadth:
all eight admitted tasks are from four CS queries, all use the unified
`researchlab` domain and one raw-LaTeX reference operation family. No 128K+
reader was admitted in this wave.

The P96 reader compiler re-resolves a cross-file reference in the final
visible TeX and checks that masking either the chosen reference or target
breaks that resolver. P105 additionally requires a unique case-folded exact
answer string and no unresolved TeX command. This is bounded evidence for
two-span source navigation; it does not establish the global shortest
semantic proof, rendered-PDF correctness, all paraphrastic alternative
answers, or model-training gains. The final shard and masks remain
`train_ready=false`; no GPU training or release was performed.

Replay and inspect from the project root:

```bash
cat data/capability_records/p105_paper_catalog_v1/manifest.json
cat data/capability_records/p105_paper_sources_v1/manifest.json
less -R data/capability_records/p105_paper_sources_v1/capacity_index.jsonl
less -R data/candidates/p105_paper_reference_curated_v1/quality_ledger.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/p105_paper_acquire.py \
  --config configs/p105_paper_acquire_v1.json \
  --output-dir data/capability_records/p105_paper_sources_v1 \
  --phase fetch --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p105_paper_acquire.py \
  --config configs/p105_paper_acquire_v1.json \
  --output-dir data/capability_records/p105_paper_sources_v1 \
  --phase probe --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p96_paper_to_unified.py \
  --config data/capability_records/p105_paper_sources_v1/qa_config.json \
  --native-dir data/candidates/p105_paper_reference_native_v1 \
  --output data/candidates/p105_paper_reference_unified_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p105_paper_quality_gate.py \
  --config configs/p105_paper_quality_gate_v1.json \
  --output-dir data/candidates/p105_paper_reference_curated_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py \
  data/candidates/p105_paper_reference_curated_v1 --all \
  --output data/candidates/p105_paper_reference_curated_mask_v1 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p105_paper_*.py
```
