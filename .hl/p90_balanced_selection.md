# P90 source-aware candidate selection

This wave adds a deterministic **candidate-only** selection over the frozen P89
three-shard reference index. The selector reads `candidate_refs.jsonl` metadata,
retains shard/row pointers, and never copies or changes the reader bodies. It
verifies the upstream index first and can replay the exact selected bytes.

## Selection contract

- Freeze the input manifest and reference SHA-256 values.
- Count a globally independent task as `(source_kind, semantic_task_id)`, matching
  the upstream 7,932 count. The source-scoped count is 7,986 before selection;
  it is also reported but is not used for deduplication.
- Reject a `(source_kind, source_group)` found in both train and eval. Retain
  one reader view per globally independent task.
- Round-robin over `(split, source_kind, operation, actual length_bin)` cells,
  with a deterministic SHA-256 seed tie-break and preference for less selected
  source groups within each cell.
- Apply caps of 24 views per source group, 48 per cell, and 400 train/100 eval
  per source kind. These are inspectable selection parameters, not quality
  thresholds. The source-kind caps were added because the baseline has 1,029
  controlled simulation groups but only 8 finance and 7 code groups.
- Report every before/after cell plus source kind, group, domain, topic,
  operation, split, length, input tokens, and supervised tokens. The actual
  `length_bin` comes from the candidate tokenizer accounting, not its requested
  source length label.

## Frozen result

| Measure | P89 input | P90 selection |
| --- | ---: | ---: |
| Reader views | 8,085 | 1,166 |
| Global independent tasks | 7,932 | 1,166 |
| Source-scoped tasks | 7,986 | 1,166 |
| Source/world groups | 1,077 | 505 |
| Occupied split × kind × operation × length cells | 175 | 175 |
| Domain/topic/operation labels | 18 / 40 / 36 | 18 / 40 / 36 |
| Train/eval views | 6,275 / 1,810 | 876 / 290 |
| Input tokens | 805,706,033 | 103,191,075 |
| Supervised tokens | 2,916,790 | 277,411 |

Selected views by kind: controlled simulation 500, grounded simulation 4,
real code workflow 168, real finance 192, real paper revision 7, real Wiki 295.
Selected views by physical length bin: `<32K` 276, `32K` 331, `64K` 295,
`128K` 264. The selected source groups are 457 controlled simulation, 1
grounded simulation, 7 code, 8 finance, 3 paper, and 29 Wiki. No selected
source group crosses train/eval; the maximum selected per group is 24.

The selected supervision remains highly uneven: controlled simulation
contributes 242,167 of 277,411 supervised tokens (87.3%) despite being 42.9%
of selected views. Finance and Wiki jointly contribute 15,287 supervised
tokens. The selection therefore is **not** a training mix or evidence of model
gain. It retains all observed cells while exposing this weighting problem for
the next data-generation and training-design stage. Reader evidence validity,
license/release status, and native mask correctness are governed by their
upstream records; this selector does not re-run them. `train_ready=false`.

## Inspect and replay

```bash
cat configs/p90_balanced_selection_v1.json
cat data/candidates/p90_balanced_selection_v1/manifest.json
less -R data/candidates/p90_balanced_selection_v1/selected_refs.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --output data/candidates/p90_balanced_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python -m pytest -q \
  tests/test_select_p90_balanced_candidates.py
```

The selected references and manifest have a deterministic hash. A changed
upstream manifest/reference, altered selected file, source-group split leak,
or changed parameter set fails verification. No GPU task was started.
