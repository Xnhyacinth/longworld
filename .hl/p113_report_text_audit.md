# P113 report reader-text deletion sidecar

The P112 report auditor's target-support deletion removes an entry from its
`proof_cells` map; it leaves the reader context unchanged. P113 adds an
independent, bounded operation on the **actual final reader bytes**. For each
of the two target observations in every P112 report view it:

1. Checks the source quote at its final context offset and its annual-filing
   heading.
2. Replaces the digits of the observed target numeral in the proof span and
   counts all matching numerals still visible in the context.
3. Separately replaces every exact grouped or ungrouped Arabic-numeral
   occurrence of that magnitude throughout the context, preserving offsets,
   and writes a gold-free `reader_context_minus` user message.
4. Pins the original issuer files, case IDs and output hashes, then replays
   the whole sidecar byte-for-byte with `--verify-only`.

The final P112 shard contains **76 semantic tasks / 152 reader views**. This
audit covers all 152 views and 304 target observations. Across them it found
957 matching numeral occurrences: 653 remain after deleting only the
executed target numeral. **201/304** target observations and **75/76**
semantic tasks have at least one additional same-magnitude numeric candidate
somewhere in their reader contexts. Of the 304 observations, 89 have a
candidate in the same annual-filing chunk and 142 in another chunk; these
groups overlap. This is evidence that the executed evidence span cannot be
assumed to be the minimum reader-text proof. It does **not** establish that
those repeats express the same metric, year, unit or scope. For example,
Amazon sample
`p112:71eb4672aafaac986c6142f8f1f7491c594dc994e414d63b38427578edef0630`
has 2 occurrences of its 2024 target numeral and 9 occurrences of its 2021
target numeral in the final reader.

The all-surface context-minus contains no exact Arabic-numeral rendering of
the selected target magnitude, but can also redact unrelated uses of the
same number. Written-out numbers, derived formulae, rounded figures,
semantically equivalent comparative rows and model prior knowledge are
outside this search. No gold-blind reader was run on context-minus, so this
sidecar **does not certify answer underdetermination or a shortest dependency
distance**. Keep `train_ready=false` for this shard until those checks and
source-quality review are resolved. The native P112 visible selector/target
edits still show that specified numeric changes alter the answer under the
declared operation; this sidecar narrows, rather than replaces, that result.

Reproduce and inspect:

```bash
cat data/candidates/p113_report_text_audit_v2/manifest.json
less -R data/candidates/p113_report_text_audit_v2/audit_index.jsonl
less -R data/candidates/p113_report_text_audit_v2/reader_context_minus.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/p113_report_text_audit.py --native data/candidates/p112_report_route_native_v5 --output data/candidates/p113_report_text_audit_v2 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p113_report_text_audit.py
```

The context-minus JSONL contains only the altered user message and case ID;
the answer and proof reside in the separate native/audit files. Numeric
candidate positions and context-minus SHA-256s are in `audit_index.jsonl`.
