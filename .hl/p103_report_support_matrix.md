# P103 frozen annual-report cross-section support matrix

P103 scanned the same eight signed P64 issuer worlds and 32 annual filings
used by the finance taskbank. It did not fetch sources or generate a question.
The source catalog, every source manifest, each source text and the P98
comparison ledger are SHA-pinned. The authoritative P103 result is
`data/capability_records/p103_report_support_matrix_v2/ledger.json` (SHA-256
`5e824a33274c6b3b1a70fb99c698c3ae9f9f16294868fb317c0eca503afeef63`).
V1 is a historical narrower Item-reference scan; use V2.

The probe renders each filing with the same reader-visible HTML renderer as
the finance adapter, then tests three source-native relation profiles:

| Profile | Gross visible candidates | Supported relation | Main rejection |
| --- | ---: | ---: | --- |
| Prose Note reference → visible Note heading/body | 108 bounded references | 0 | 59 reference lines already state the title; 45 target headings absent; four references occur in tabular/layout lines without a row binding |
| Complete numeric table row → visible Note heading/body | 178 tab lines mentioning a Note | 0 | 175 lack a complete row with a separate numeric cell; three have no visible target heading |
| Item reference → executable rule | six bounded references | 0 | Four are only navigation links without typed rule predicates; one target body and one heading are absent |

Across the corpus, 147 visible Note numbers were observed and 123 have a
unique title with nearby visible body text. These are **not** automatically
bound to the 108 reference lines. For example, Amazon 2021 says “See Note 10
— Segment Information” on the reference line itself. Alphabet 2021 has a
bare “See Note 3” line, but no matching visible numbered target heading under
this parser. Amazon's “Commitments and contingencies (Note 7)” tab line lacks
the row and numeric-cell structure needed to compile a table-to-note program.
The full per-filing status and short source excerpts are in the ledger; full
source bodies are referenced by hash rather than copied into it.

The support status is therefore `tasks_admitted=0`, `train_ready=false`.
There is no P103 native reader, unified shard or mask receipt. This does not
invalidate the existing numeric P64/P96 finance tasks: it only states that
their frozen visible documents and current typed world do not support this
specific cross-section prose/table-note compiler. A larger annual-report
source pool would need native section/Note anchors, complete table row and
column identities, typed prose/rule facts, and a final-reader answer-changing
intervention before a Note or rule task can be admitted. Changing the issuer
name or domain label cannot supply those missing structures.

```bash
cat data/capability_records/p103_report_support_matrix_v2/ledger.json
UV_LINK_MODE=copy uv run --offline python scripts/p103_report_support_matrix.py \
  --config configs/p103_report_support_matrix_v1.json \
  --output data/capability_records/p103_report_support_matrix_v2/ledger.json \
  --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p103_report_support_matrix.py
```
