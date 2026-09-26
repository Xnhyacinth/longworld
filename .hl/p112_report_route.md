# P112 parameterized real-report cross-period tasks

This campaign reuses eight already frozen issuer-report worlds. A declarative
matrix of five selector metrics, four distinct target metrics, max/min
selection and earliest/latest baseline endpoints yields 640 legal cells.
The executor first selects a fiscal year from visible report cells, then
reads the target metric for that year and a baseline year in another report
and computes a difference in normalized USD millions. The visible reader
contains real report excerpts and cells; program, gold and proof metadata
remain outside the reader. The compiler runs four source jobs in parallel
and does not contain issuer-specific question functions.

The first version admitted 86 tasks but 55 had same-row comparison-column
shortcuts. A later version admitted 41, with 39 selecting the latest year.
Both remain diagnostics. The final version uses symmetric baseline endpoints
and a bounded same-report value/answer scan. Of 640 cells, 266 were rejected
for same-report or answer shortcuts, 208 for unsupported source shape,
29 for failed intervention, 33 by the balance cap and 28 by the world budget.
The resulting **76 independent semantic tasks / 152 final reader views** span
all eight issuer worlds (65 train tasks, 11 eval tasks). The task program's
baseline endpoint is earliest for 39 tasks and latest for 37. The selector
year is distributed across early, late and a small internal group rather
than always using the last year (35 early, 37 late, four internal).

The independent source/reader replay checks four selector cells and two
target cells per reader, same-report numeric shortcut, selector edits,
target edits, bounded target-support deletion, final answer and exact
assistant-only mask. All 152 readers passed. Final chat totals 10,073,510
tokens with 6,848 supervised tokens; physical bins are 65 below 32K, 76
at 64–128K and 11 at 128–256K. The executed evidence extent is
8,461–78,919 tokens, and the last evidence-to-question gap is 210–76,883.
These are observed, bounded checks. Alternative semantic paraphrases or
equivalent values in unrestricted prose were not exhaustively searched.
`train_ready=false`; no model learning claim.

**Post-review dependency boundary:** the target-support deletion in the
native auditor removes a key from its `proof_cells` mapping while leaving
the model-visible `context` unchanged. It proves formal program-lineage
necessity only. Selector and target **edits** do change the visible numeric
text and answer, but do not exclude every substitute reading path. One
Amazon reader (`p112:71eb4672aafaac986c6142f8f1f7491c594dc994e414d63b38427578edef0630`)
repeats the 2021 target value `24,879` in its 2021, 2022 and 2023 report
chunks and the 2024 value `68,593` twice in its 2024 chunk. The same-report
shortcut scan rejects a report containing **both** values, so this survives
despite alternative support for each target. The answer may be correct, but
its executed evidence span is not a certified minimum reader-text distance.
Before training promotion, generate a `reader_context_minus` that removes
all equivalent visible supports and check whether the answer becomes
underdetermined. The P112 report shard remains a research candidate.

P113 ran a separate visible-text numeric-surface audit over all 152 reader
views. Of 304 target observations, 201 still had the same numeric surface
somewhere in the reader after the proof-span number was masked. Its 304
gold-free `reader_context_minus` texts are recorded in
`data/candidates/p113_report_text_audit_v2` and explained in
`.hl/p113_report_text_audit.md`. This detects possible duplicate support;
it does not decide whether those numbers carry equivalent row/period/metric
semantics or whether the answer remains recoverable. The report shard is
still `train_ready=false`.

This changes operation coverage inside existing finance source worlds. It
does not add eight new issuers, domains or document licenses. The global
balanced arm can admit only part of these tasks under its per-source-group
cap; the full 76-task shard remains separately inspectable.

```bash
cat data/candidates/p112_report_route_native_v5/batch_manifest.json
cat data/candidates/p112_report_route_native_v5/final_audit.json
cat data/candidates/p112_report_route_unified_v2/manifest.json
cat data/candidates/p112_report_route_all_mask_v2/manifest.json
less -R data/candidates/p112_report_route_unified_v2/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/p112_report_route.py --config configs/p112_report_route_v2.json --output data/candidates/p112_report_route_native_v5 --workers 4 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p112_report_audit.py --config configs/p112_report_route_v2.json --native data/candidates/p112_report_route_native_v5 --output data/candidates/p112_report_route_native_v5/final_audit.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p112_report_to_unified.py --config configs/p112_report_route_v2.json --native data/candidates/p112_report_route_native_v5 --final-audit data/candidates/p112_report_route_native_v5/final_audit.json --output data/candidates/p112_report_route_unified_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py data/candidates/p112_report_route_unified_v2 --all --output data/candidates/p112_report_route_all_mask_v2 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p112_report_route.py
```
