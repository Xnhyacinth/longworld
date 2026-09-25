# P92 generic frozen-Wiki year-table scan

The new `p92_generic_table_scan.py` infers a closed table from visible headings,
column labels, rows and a following heading. It accepts a single plain
four-digit `Year`, `Established`, `Opened`, `Built` or `Completion` column and a
clean subject column. Every row must have the same width, a unique clean name
and a parseable year. The program rejects incomplete or ambiguous tables rather
than quietly making a complete-set label from a subset of rows. It does not
infer that a generic `Year` means an establishment date. Questions name the
actual visible column.

The runner reads the pinned 38-group Wiki pool, validates snapshot SHA-256 and
revision inventory, and scans groups in four processes. It then compares
semantic keys `(snapshot, document, column, low, high)` against pinned prior
task audits before tokenizing. For each novel admitted task it checks
the assistant-only mask, offsets for **all** candidate rows, exact same-line
alternate support outside the table, and a visible-text hit/near-miss replay.
The replay is a bounded information intervention; it does not claim a
historically valid change to the public source. The P94 parser update chooses
a below-range boundary row whose changed year and near miss both preserve
ascending or descending year order. If no such row exists, the table/task is
still rejected.

Run:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/run_p92_generic_table_scan.py \
  --config configs/p92_generic_table_scan_v1.json \
  --output-dir data/candidates/p92_generic_table_scan_v6 --workers 4
cat data/candidates/p92_generic_table_scan_v6/manifest.json
cat data/candidates/p92_generic_table_scan_v6/rejected.jsonl
```

The frozen pool had **38 source groups and 117 unique pages**. Twenty-four
plausible year-table headers failed the strict row/closure parser; only one
complete table was admitted: 35 rows of the Japanese national-parks page in
`snapshot_c363c5af8780a75d524b` (`train`). Four interval candidates were
considered, but **all four already exist** in the pinned P76 parks table-scan
bank with the same snapshot, document, column and year boundaries. The
final net-new yield is **zero**. Replaying the same 38-group pool after the
P94 sorted-table update again found one complete table and zero net-new
tasks; four candidate intervals remain duplicates. The earlier `v4` probe materialized two rows
before this cross-bank check; they also duplicate existing questions and must
not be appended to a final candidate bank. Those rows had 55,587 and 55,661
full-chat tokens, a 1,243-token candidate-row extent and valid masks, but
their quality checks do not make them independent new tasks. This is a
source-capacity and deduplication diagnostic, not broad domain scaling.

The final `v6` and `v6_replay` runs use four and two workers and must produce
byte-identical manifests and candidate files. Focused parser and dedup tests
pass. `train_ready=false`; no model training was run. The source pool needs
many more complete, unit- and scope-clear tables before this lane can scale by
simple source substitution.
