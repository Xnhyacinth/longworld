# P86 frozen paper source-capacity batch

P86 scanned five distinct hash-pinned arXiv works and 20 adjacent revision pairs with three local processes. The final v2 receipt admits **7 independent train-split revision-alignment tasks** from three works: AEVB 5, MLRC 1, Attention 1. Megatron 0 and GPT-3 0. Thirteen pairs were rejected (9 without a qualifying prose hunk, 3 with alternate exact support, 1 below the final 32K-token minimum). The earlier v1 diagnostic admitted 8 before the prose filter excluded a near-identical revision line; only v2 is the review candidate.

All seven final chats are 33,079–35,831 Qwen3.5 tokens. Their bounded necessary answer-line span in the **final chat token coordinate system** is 21,850–31,972 tokens. They contain 238,546 input tokens and 1,903 supervised assistant tokens in total. One task compares two changed files; six compare one changed file across revisions. The questions explicitly describe revision alignment, so this batch adds real long-distance version reading but does not establish natural-intent QA or broader domain coverage. All rows retain `train_ready=false`.

The compiler validates each frozen fetch-inventory hash and archive hash. For four works it also hash-checks a source bundle, its referenced workflow manifest, and that manifest's fetch-inventory hash. It does **not** freshly verify historical HMAC signatures. The GPT-3 preflight has no linked source bundle in this batch and is marked accordingly. The P85 AEVB v4→v5 audit is pinned for overlap checking; admitted v2 tasks have zero matching source pairs and zero exact-answer overlaps. Source character capacity in `capacity_index.jsonl` is only a cheap inventory measure, never claimed as token capacity.

Final reader admission checks unique exact answer lines, visible line-alignment replay, deletion of each required line and entire required source record, final token offsets, and the assistant-only mask. These are bounded checks for an explicit alignment program; they do not prove all semantic alternatives absent or demonstrate model improvement. More than 10 archive paths would have overstated source breadth because copied trees and revisions collapse to roughly ten distinct frozen paper works. A metadata-first, work-level expansion plan is recorded at `configs/p86_public_paper_acquisition_plan_v1.json`; it has **not fetched** new works. It requires rights review and single-connection, three-second API pacing per [arXiv's API terms](https://info.arxiv.org/help/api/tou.html).

Inspect:

```bash
cat data/candidates/p86_frozen_paper_batch_v2/manifest.json
less -R data/candidates/p86_frozen_paper_batch_v2/capacity_index.jsonl
less -R data/candidates/p86_frozen_paper_batch_v2/rejected.jsonl
less -R data/candidates/p86_frozen_paper_batch_v2/audit.jsonl
less -R data/candidates/p86_frozen_paper_batch_v2/train.jsonl
```

Rebuild and verify:

```bash
PYTHONPATH=. .venv/bin/python scripts/run_p86_frozen_paper_batch.py --config configs/p86_frozen_paper_batch_v2.json --output-dir data/candidates/p86_frozen_paper_batch_v2_replay
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_p86_frozen_paper_batch.py
.venv/bin/ruff check scripts/run_p86_frozen_paper_batch.py tests/test_p86_frozen_paper_batch.py
.venv/bin/ruff format --check scripts/run_p86_frozen_paper_batch.py tests/test_p86_frozen_paper_batch.py
```

The completed replay produced byte-identical `capacity_index.jsonl`, `rejected.jsonl`, `sample_index.jsonl`, `audit.jsonl`, `train.jsonl`, `eval.jsonl`, and `manifest.json` (SHA-256 equality). Four focused tests passed; Ruff checks passed.
