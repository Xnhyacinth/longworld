# P96 declarative finance task scaling

P96 varies a typed selector program and a different target metric over the
same eight signed annual-report worlds already used by P64/P95. One interpreter
executes every combination; adding an issuer with a supported signed source
manifest does not require an issuer-specific task function. The source catalog
and prior P95 candidate index are SHA-pinned in
`configs/p96_finance_factorial_wide_v1.json`.

The legal vocabulary has 25 selector specifications spanning eight families:
maximum/minimum value, largest absolute adjacent-year change, maximum
percentage growth, maximum/minimum/lower-middle ratio, and largest absolute
adjacent-year ratio change. Five duration metrics can be targets. A target may
not be one of its selector's inputs. This gives 92 declared combinations per
world, or 736 support-matrix cells across eight worlds. Every cell is recorded
as selected, blocked for known prior-program equivalence, rejected for answer
concentration, unsupported/degenerate, or outside the task budget. Source
facts must have one annual duration, USD-million unit, source reporting basis,
and a visible original-filing span; unique maxima/minima and unambiguous
denominators are required.

The narrow config requested at most 16 tasks per world: 128 independent tasks
and 256 reader views. The wide config requested at most 48, but the answer
cap of two tasks per issuer × target metric × selected year creates a tighter
theoretical upper bound of 5 × 4 × 2 = 40 per world. Actual wide yield was
33–40 tasks per world, totaling 296 tasks/592 views. All narrow task IDs occur
in the wide batch; widening added 168 task IDs. In the wide matrix, 40 cells
were blocked as five explicit P64/P95 equivalent programs per issuer, 400
were rejected by the answer bucket cap, and 296 were selected. Those 296
occupy 156 answer buckets: 140 with two tasks and 16 with one. The per-source,
selector-family, target-metric and split counts are in
`data/candidates/p96_finance_factorial_wide_batch_v1/scaling_report.json`.

The wide final-byte audit independently retokenized and re-executed 592/592
reader views from visible statement cells: 38,843,270 input tokens and 19,444
assistant-supervised tokens. It checked source pins, answer hashes, exact
assistant-only masks, quote and token spans, and the selected record. Physical
length bins before selector filtering were 258 `<32K`, 296 `64k`, and 38
`128k`. Evidence extent was 8,186–79,354 tokens. The record is
`data/candidates/p96_finance_factorial_wide_batch_v1/final_audit.json`.

For selector sensitivity, the auditor joins visible source-cell evidence from
all tasks sharing an issuer and reader variant. It changes one selector cell
in a temporary copy of the final context using an equal-character-length
numeric display and independently re-executes the declared program. It
requires both the chosen source record and the numeric target to change.
568/592 views passed this bounded search. The 24 failures occur in pairs, so
the unified shard retains only 284 complete semantic tasks/568 views and
records 12 excluded tasks. Both views of every retained task passed the final
mask/answer audit and selector intervention. The sidecar and its immutable
code/data receipt are
`data/candidates/p96_finance_factorial_wide_batch_v1/selector_interventions.json`
and `selector_interventions.receipt.json`.

The final candidate shard is
`data/candidates/p96_finance_factorial_wide_unified_v1/manifest.json`: 422 train
and 146 eval views from six/eval-two source groups, 77 distinct declared
selector→target signatures, 37,532,712 input tokens and 18,708 supervised
tokens. Its manifest binds the native batch, final audit and selector sidecar;
the shard remains `train_ready=false`. This lane reuses eight existing finance
worlds and adds no domain label. The exact semantic IDs have zero overlap with
the pinned P95 index, while five known equivalent P64/P95 programs per issuer
were explicitly blocked. These checks do not exhaust semantic equivalence
with every older free-form task. Later filings contain comparative columns, so
neither source-version lineage nor the bounded selector edit proves that a
reader must open all four original filings. No model training or gain is claimed.

A second two-worker run with the same pinned wide config was written to
`data/candidates/p96_finance_factorial_wide_replay_v1`. Its 49 generated
non-log files, including all eight issuer reader, index, audit and matrix
files plus the batch manifest, were byte-identical to the frozen wide batch.
The diagnostic stdout/stderr logs are excluded from the content comparison.

```bash
# A new replay must use a fresh, nonexistent output directory.
UV_LINK_MODE=copy uv run --offline python scripts/run_p96_finance_factorial.py \
  --config configs/p96_finance_factorial_wide_v1.json \
  --output data/candidates/p96_finance_factorial_wide_replay_v2 --workers 2

UV_LINK_MODE=copy uv run --offline python scripts/report_p96_finance_scaling.py \
  --narrow data/candidates/p96_finance_factorial_batch_v1 \
  --wide data/candidates/p96_finance_factorial_wide_batch_v1 \
  --wide-config configs/p96_finance_factorial_wide_v1.json \
  --output data/candidates/p96_finance_factorial_wide_batch_v1/scaling_report.json \
  --verify-only

UV_LINK_MODE=copy uv run --offline python scripts/audit_p96_finance_factorial.py \
  --config configs/p96_finance_factorial_wide_v1.json \
  --batch-dir data/candidates/p96_finance_factorial_wide_batch_v1 \
  --output data/candidates/p96_finance_factorial_wide_batch_v1/final_audit.json \
  --verify-only

UV_LINK_MODE=copy uv run --offline python scripts/audit_p96_finance_interventions.py \
  --config configs/p96_finance_factorial_wide_v1.json \
  --batch-dir data/candidates/p96_finance_factorial_wide_batch_v1 \
  --final-audit data/candidates/p96_finance_factorial_wide_batch_v1/final_audit.json \
  --output data/candidates/p96_finance_factorial_wide_batch_v1/selector_interventions.json \
  --verify-only

UV_LINK_MODE=copy uv run --offline python scripts/p96_finance_to_unified.py \
  --config configs/p96_finance_factorial_wide_v1.json \
  --batch-dir data/candidates/p96_finance_factorial_wide_batch_v1 \
  --final-audit data/candidates/p96_finance_factorial_wide_batch_v1/final_audit.json \
  --selector-sidecar data/candidates/p96_finance_factorial_wide_batch_v1/selector_interventions.json \
  --output data/candidates/p96_finance_factorial_wide_unified_v1 \
  --verify-only

cat data/candidates/p96_finance_factorial_wide_batch_v1/amazon/support_matrix.jsonl
cat data/candidates/p96_finance_factorial_wide_batch_v1/amazon/audit.jsonl
less -R data/candidates/p96_finance_factorial_wide_batch_v1/amazon/reader.jsonl
```
