# Current long-context synthesis references (2026-08-20 lookup)

Search backend: Codex web search fallback because `parallel-cli` was unavailable. These notes are evidence only.

## Primary sources

- SearchArt (2026), https://arxiv.org/abs/2607.24850 — verified task synthesis from real web documents and generated evidence graphs; validates QA consistency, trajectory quality, and evidence relevance; reports downstream SFT/RL search-agent results.
- ACC (2026), https://arxiv.org/abs/2605.21850 — compiles real agent tool trajectories and environment observations into long-context QA; evaluates MRCR/GraphWalks gains and preservation of general capabilities.
- LongTraceRL (2026), https://arxiv.org/abs/2605.31584 — multi-hop questions from KG walks; high-confusability distractors from read-but-uncited agent documents and low-confusability search-result documents; entity-level process rubrics; downstream benchmark evaluation.
- LongRLVR (ICLR 2026), https://arxiv.org/abs/2603.02146 — argues answer-only rewards are too sparse for contextual grounding; adds a dense verifiable context reward and reports RULER-QA and LongBench v2 improvements.
- NExtLong (2025), https://arxiv.org/abs/2501.12766 — interleaves retrieved hard-negative chunks rather than arbitrary filler; reports HELMET and RULER improvements.
- LongMIT (2024), https://arxiv.org/abs/2409.01893 — studies factors for effective multi-hop long-context instruction data and releases LongMIT.
- RULER (COLM 2024), https://arxiv.org/abs/2404.06654 — evaluates effective, rather than advertised, context length using retrieval, multi-hop tracing, aggregation, and QA across lengths.

## Audit criteria derived from the sources

1. Long length alone is not the outcome: report downstream effective-context gains by length and task complexity.
2. Necessary context should be explicitly grounded and preferably receive process/evidence supervision, not only final-answer loss.
3. Distractors should be confusable and task-related (same entity/schema/trajectory neighborhood), with difficulty tiers; arbitrary real text is insufficient.
4. Data validity needs joint checks of answer consistency, evidence relevance/necessity, and trajectory or proof quality.
5. Training claims need matched-token baselines, general-capability retention, and held-out evaluation beyond generator-native templates/domains.
6. Report distance and depth independently: far-apart two-fact retrieval is not deep multi-hop reasoning.
