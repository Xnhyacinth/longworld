# P98 real-report prose and Note-reference feasibility

The eight P64 pinned annual-report source groups contain 32 complete filings,
but their existing source-bound worlds type financial statement cells, not
prose claims, Note targets, or a visible numbered-Note relation. P98 therefore
does **not** admit a report-prose reader task. The pinned, replayable rejection
ledger is `data/capability_records/p98_report_note_probe_v1/ledger.json`.

The probe renders all 32 source texts to the same visible-text representation
used by P64/P96. It finds 108 bounded `See/Refer to Note N` prose lines, 89
distinct lines after deduplication. Fifty-five reference occurrences already give
the Note title next to its number, so asking for the title has a local answer.
Forty-seven distinct lines use a bare Note number; across the issuer/year rows, 40
distinct bare Note-number groups lack a visible numbered heading that can be
linked reliably. A narrow heuristic finds nine filing×Note groups with exactly
two distinct reference lines. Manual inspection shows that some are duplicate
renderings or near-identical boilerplate across filing years: Intel's 2021
pair repeats a Dalian factory event, and SiriusXM's Note 16 pair recurs in
2021–2024 as the same Legal Proceedings/Contractual Cash Commitments template.
These nine groups are **candidates for review**, not nine independent training
tasks or evidence of a long dependency.

The generic Note-number prompt would mostly reward single-line lookup. The
current source parser also cannot prove the meaning of a bare Note target
from visible headings. A credible next attempt needs a source-native section
index with exact prose spans and numbered target headings, followed by a
visible-reader check that removes same-line answers and repeated alternate
support. Until that exists, this lane's support status is `unsupported` and
`tasks_admitted=0`; no train/eval reader or mask claim is made.

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p98_report_note_probe.py \
  --config configs/p98_report_note_probe_v1.json \
  --output data/capability_records/p98_report_note_probe_v1/ledger.json \
  --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p98_report_note_probe.py
cat data/capability_records/p98_report_note_probe_v1/ledger.json
```
