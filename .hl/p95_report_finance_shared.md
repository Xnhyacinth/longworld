# P95 shared-world real annual-report compiler

The P95 candidate compiler applies four different executable selectors to the
same frozen issuer world. It reads the existing P64 catalog and its signed,
source-verified annual filings; no issuer-specific question function or topic
replacement is involved. It generates two reader views per semantic task:
complete audited statements, and an analyst packet containing earlier complete
statements plus the latest full visible filing. The numeric fact graph and
execution trace remain in the audit output, never in `messages[0]`.

The four operations select the lower middle operating margin then read operating
cash flow; select the largest absolute adjacent-year operating-cash-flow change
then read later-year revenue; select the greatest adjacent-year revenue growth
then read later-year operating cash flow; and select the largest adjacent-year
operating-margin swing then read later-year operating cash flow. Each program
uses four source-year records to choose a year or transition, rejects ties or missing
scope-compatible source metrics, and reads the target amount from the selected
year's original filing. The compiler binds every consumed numeric cell to an
original-source span, a visible-renderer span and a final reader span.

Final P95 v2 batch:

- `data/candidates/p95_report_finance_shared_batch_v2/batch_manifest.json`
- `data/candidates/p95_report_finance_shared_batch_v2/final_audit.json`
- `data/candidates/p95_report_finance_shared_batch_v2/selector_interventions_v3.json`
  and `selector_interventions_v3.receipt.json` (pins the sidecar, batch,
  final audit and auditor code hashes).
- `data/candidates/p95_report_finance_unified_v1/manifest.json` (standard
  unified candidate shard: 64 views, 32 independent task IDs, 48 train and 16
  eval views; bound to each issuer manifest and the final audit SHA-256).
- Per issuer: `reader.jsonl`, `sample_index.jsonl`, `audit.jsonl`, `rejects.json`,
  `manifest.json` under the issuer directory.

Eight existing frozen issuer source groups generated 32 independent tasks and
64 final reader views. Six groups retain the P64 train split and two the P64
eval split. Each operation has eight semantic tasks and 16 reader views; there
were zero source/structure/capacity rejects in this batch. The final physical
bins are 28 `<32K`, 32 `64k` (64K–128K), and four `128k` (128K–256K).
The mask audit independently retokenized every final reader: 64/64 assistant
masks, 4,186,608 input tokens and 1,928 supervised tokens. It also re-executed
64/64 answers using only numeric quotes and report-year headers present in the
final reader text, checked source hashes, quote offsets and evidence token
spans. The evidence extent is 8,186–78,919 tokens; last-evidence-to-query
distance is 555–79,333 tokens. These are actual final-input positions, not
claims of globally shortest proof.

The supervised share is only about 0.046% of full-chat tokens. This lane is a
reader-SFT candidate and should be mixed by input and supervised-token budget,
not counted as 64 equally cheap updates. Its two views reuse the same semantic
answer; the 64 views are 32 independent questions.

The pilot v1 had two operations and 16 tasks/32 views; it remains historical.
The v2 batch was rerun independently at
`data/candidates/p95_report_finance_shared_replay_v2`; all 57 generated files
outside the separately computed `final_audit.json` match byte for byte. The
final audit's `--verify-only` mode recomputes masks and visible-cell answers.
The separate bounded selector audit edits one final-visible selector cell to
another equal-width numeric display and re-executes from visible cells. It
changes both the selected year/transition and numeric target for 64/64 views.
One Intel case uses a parenthesized operating loss as a synthetic edit. The
original 64 reader files are unchanged; this audit does not exhaust alternate
support paths in the unmodified context.

```bash
UV_LINK_MODE=copy uv run --offline python scripts/run_p95_report_finance_shared.py \
  --catalog configs/p64_finance_taskbank_catalog_v1.json \
  --output data/candidates/p95_report_finance_shared_batch_v2 --workers 2

UV_LINK_MODE=copy uv run --offline python scripts/audit_p95_report_finance_shared.py \
  --catalog configs/p64_finance_taskbank_catalog_v1.json \
  --batch-dir data/candidates/p95_report_finance_shared_batch_v2 \
  --output data/candidates/p95_report_finance_shared_batch_v2/final_audit.json \
  --verify-only

UV_LINK_MODE=copy uv run --offline python scripts/p95_report_finance_to_unified.py \
  --catalog configs/p64_finance_taskbank_catalog_v1.json \
  --batch-dir data/candidates/p95_report_finance_shared_batch_v2 \
  --final-audit data/candidates/p95_report_finance_shared_batch_v2/final_audit.json \
  --output data/candidates/p95_report_finance_unified_v1 \
  --verify-only

UV_LINK_MODE=copy uv run --offline python scripts/audit_p95_report_selector_interventions.py \
  --catalog configs/p64_finance_taskbank_catalog_v1.json \
  --batch-dir data/candidates/p95_report_finance_shared_batch_v2 \
  --final-audit data/candidates/p95_report_finance_shared_batch_v2/final_audit.json \
  --output data/candidates/p95_report_finance_shared_batch_v2/selector_interventions_v3.json \
  --verify-only

cat data/candidates/p95_report_finance_shared_batch_v2/amazon/sample_index.jsonl
cat data/candidates/p95_report_finance_shared_batch_v2/amazon/audit.jsonl
less -R data/candidates/p95_report_finance_shared_batch_v2/amazon/reader.jsonl
```

The program references amounts attributed to four original annual filings,
but later filings can contain comparative columns with duplicate amounts.
The audit establishes bounded source-program and visible-cell replay;
it does not establish that all alternative visible proofs have been found, nor
that a model must read every original filing. The eight source groups already
existed in P64, so this is task and context diversity, not eight new source
worlds or domains. All outputs remain research candidates with
`train_ready=false`; no GPU training or model-improvement claim is made.
