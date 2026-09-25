# P99 real-code content task pilot

The P94 DuckDB/Wasmtime 43-reader batch passed native replay and exact mask,
but its P65 filename-copy/content proof was **0/43**. The proof checker only
covers three filename aggregation operations: 26 tasks were outside its
grammar, seven DuckDB tasks passed the raw 16K alias test but failed when SHA
metadata was normalized while filenames remained, five had a literal-copy
opportunity, three had no effective approval condition, and two had redundant
source support. These are proof classifications, not 43 incorrect labels.

P99 samples a different operation from nine frozen CodeForge source banks. A
question supplies two code identifier tokens and two PR numbers; the answer is
the complete sorted set of `(pull_request, path)` pairs whose **merged-head
added lines** contain either identifier. The compiler follows visible merge
links, finds one identifier in each of two real source patches, checks the
full reader-visible JSON, removes all `+` added lines while preserving diff
headers, filenames, all other records and metadata, then requires both
identifiers to disappear from that controlled context. It rejects a scope if
either PR does not uniquely contribute, the two selected code witnesses fit
within a 16,384-token raw-context span, or the final chat overflows 262,144
tokens. Pinned tokenizer measurements use the actual assistant-only mask.

The final native batch is
`data/candidates/p99_code_content_v4/manifest.json`; its unified candidate
shard is `data/candidates/p99_code_unified_v2/manifest.json`. The first two
development snapshots (`v1` and `v2`) and the first unified snapshot (`v1`)
are superseded; do not add them to candidate totals.

| Measure | Final result |
| --- | ---: |
| Pinned source banks / productive groups | 9 / 4 |
| Independent semantic tasks / reader views | 21 / 21 |
| Train / eval | 14 / 7 |
| Physical 32K / 64K / 128K bins | 11 / 7 / 3 |
| Candidate scopes rejected | 197 |
| Final assistant mask audited | 21 / 21 |
| Full chat / supervised tokens | 1,709,815 / 1,052 |
| Accepted two-witness span | 21,285–161,687 tokens |

The productive repositories are Ruff (2), Oxc (12), uv (3) and
OpenSearch PHP (4). Dprint, Pulumi, Transformers, DuckDB and Wasmtime yielded
no candidates under this content control and 16K criterion. Rejections are
preserved in `p99_code_content_v4/rejects.jsonl`: 182 lacked two distinct
content-only identifiers, nine had no readable merged-head diff, and six had
shorter witness spans. This counts attempted source pairs, not independent
qualified tasks. No source or split is copied across train and eval.

A concrete Ruff row has PRs #27835 and #28000, identifiers
`is_rule_enabled` and `as_deref`, and answers with their source paths
`crates/ruff_linter/src/checkers/ast/mod.rs` and
`crates/ruff_python_ast/src/helpers.rs`. The final chat is 42,718 tokens,
with 50 supervised tokens; the two selected patch witnesses are 21,578
tokens apart. Both identifiers disappear when only added lines are removed
from the reader context, while filenames and other records remain.

The unified reader contains only `sample_id` and `messages`. The separate
index records source group, operation, physical length, native audit pointer,
and `content_backed_two_source_scoped_certificate`. The certificate concerns
the finite added-identifier grammar and **one** contiguous 16K raw window;
it does not rule out arbitrary paraphrases, multiple-window retrieval,
pretrained knowledge, or other equivalent reasoning. It is not a proof that
the model will learn long-code reasoning. `train_ready=false`; no GPU job was
started.

Reproduce and inspect:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p99_code_content_tasks.py \
  --config configs/p99_code_content_v1.json \
  --output data/candidates/p99_code_content_v4 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p99_code_to_unified.py \
  --config configs/p99_code_content_v1.json \
  --native-dir data/candidates/p99_code_content_v4 \
  --output data/candidates/p99_code_unified_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py \
  data/candidates/p99_code_unified_v2 --all \
  --output data/candidates/p99_code_unified_mask_v2 --verify-only
cat data/candidates/p99_code_unified_v2/manifest.json
less -R data/candidates/p99_code_content_v4/rejects.jsonl
less -R data/candidates/p99_code_content_v4/audit.jsonl
```

Eight focused P99 tests and Ruff pass. The final native and unified builds
were executed; the unified `verify-only` and independent 21-row mask replay
passed against the output bytes.
