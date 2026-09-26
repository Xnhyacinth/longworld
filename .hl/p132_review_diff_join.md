# P132 real CodeForge review-to-final-diff candidate

P132 adds one operation to the existing signed CodeForge world: across two merged
pull requests, return the complete sorted `(PR, path)` set whose path appears in
a frozen review comment and in that PR's **final merged-head diff**. A review
comment on a path that disappeared by merge is an explicit excluded candidate.
The operation is different from the existing changed-file union/intersection,
approval, CI, and added-code-identifier tasks. It reuses the same source banks,
repository-atomic splits, renderer, Qwen tokenizer, and assistant-only SFT mask.

The source-shape scan covered 475 two-PR scopes across 13 frozen repository
banks. It found 62 structurally eligible scopes from Ruff, Oxc, dprint, uv,
and Bitcoin. The other banks had no scope with both positive PRs and a
commented path excluded by the final diff; 9 scopes in DuckDB/Wasmtime also
lacked a source-readable merged-head diff. The fixed pilot cap is four tasks
per repository and PR pairs are scheduled to prefer previously unused episodes.
This is a bounded operation pilot, not a claim that the repository pool has
been exhausted or that 12 examples meet the project's target data scale.

Final native shard: `data/candidates/p132_review_diff_join_v6/`.
Final unified candidate shard: `data/candidates/p132_review_diff_join_unified_v4/`.
Earlier v1-v5 native and v1-v3 unified directories are diagnostics, not the
promotion target. The final native shard has 12 independent tasks: Ruff 4,
Oxc 4, Bitcoin 2, dprint 1, uv 1. Split is 11 train and 1 eval by repository.
Final-chat length bins are `<32K` 2, `32K` 2, `64K` 5, and `128K` 3;
the observed final length range is 29,112-207,874 tokens. Assistant-supervised
tokens total 811 (42-117 per task), with no reader truncation. Source license
status remains **local research only**, and `train_ready=false`.

The native compiler verifies each CodeForge `BUILD_RECEIPT.json`, its world
and source bindings, the pinned P99/P108 bank-config SHAs, the printed
review-comment path against the native hidden path, and the final merged-head
link. The model receives the rendered records, not hidden path attributes or
the answer/proof. Exact case-sensitive path matching is used; ambiguous
rename, deletion, and new-file diff markers are rejected. Every positive
answer member has a reader-text comment-side deletion and a deletion of all
path occurrences within the selected final-head text. Each changes exactly
that answer member. An excluded commented path is
hypothetically added to the final diff and must enter the complete-set answer.
These interventions are stored as context SHA and resulting answer receipts;
they are **counterfactual audit views**, not extra training examples.

All evidence offsets are mapped into the actual final chat template and
tokenizer. The distance claim is intentionally narrow: **at least one answer
member per task** has a minimum enumerated comment-to-final-diff support gap
of at least 8,192 final-chat tokens. Across the 12 tasks, 23 of 34 answer
members satisfy that threshold; the shortest qualifying task maximum is
9,383 tokens. It does not follow that every member is long distance, that
unknown semantic shortcuts are impossible, or that model training improves.
A bounded alternate text scan records other visible records containing each
answer path: 5 tasks have at least one long member with no such extra path
occurrence. Earlier commit diffs explain many of the others; those occurrences
do not alone establish a second final-merged-head proof, so they are retained
as candidate diagnostics rather than silently relabeled as clean.

The unified adapter replays visible gold from final reader JSON, verifies the
native manifest, answer, mask counts and intervention receipts, then emits
`candidate_train.jsonl`, `candidate_eval.jsonl`, and `sample_index.jsonl`.
Its `native_row_ref` points to the pinned native reader row and its metadata
contains `bank_directory`, `world_instance_id`, split and row index; the
existing `_code_groups` source-component auditor resolves all five signed
bank/raw-source bindings without a common-code change. Full bank selection,
joint source-component audit, and training remain root-owned gates.

Reproduce:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p132_review_diff_join.py --config configs/p132_review_diff_join_v1.json --output data/candidates/p132_review_diff_join_v6 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p132_review_diff_to_unified.py --native-dir data/candidates/p132_review_diff_join_v6 --output data/candidates/p132_review_diff_join_unified_v4 --verify-only
UV_LINK_MODE=copy uv run --offline python -m pytest tests/test_p132_review_diff_join.py -q
UV_LINK_MODE=copy uv run --offline ruff check scripts/p132_review_diff_join.py scripts/p132_review_diff_to_unified.py tests/test_p132_review_diff_join.py
```

Inspect one native case without dumping the multi-megabyte reader to a terminal:

```bash
UV_LINK_MODE=copy uv run --offline python - <<'PY'
import json
from pathlib import Path
p = Path('data/candidates/p132_review_diff_join_v6')
audit = json.loads((p / 'audit.jsonl').read_text().splitlines()[0])
print(json.dumps({k: audit[k] for k in ('source_group', 'source_prs', 'answer', 'evidence')}, indent=2))
PY
```
