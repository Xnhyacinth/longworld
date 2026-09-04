# P47 USAspending certified-period reconciliation preflight

## Outcome

**Source-layer capacity: PASS. Certified relation-chain admission: FAIL. Training
inventory contribution: 0.**

Official no-key endpoints establish real NASA FY2024 P6 and P9 certification
events, current prime-award transaction archives, and a separately sourced
prime-to-subaward relation. The identifier-free cross-period prime/subaward
projection contains **236 award-level artifacts / 332,872 exact tokens** and
**238,434 amount/date-coarsened tokens**, enough raw material to attempt 32K,
64K, and 128K packing.

None of those tokens is admitted to the requested certified chain. Both File C
download jobs failed and their file objects returned HTTP 403, so the essential
`certified P6/P9 File C -> D1/D2 prime award` bridge has zero observed rows.
The annual award archives were regenerated in August 2026 and are not
as-certified FY2024 snapshots; File F subawards are reported by prime recipients,
not certified by NASA. The result is `train_ready=false`,
`do_not_generate=true`, and zero candidates.

## Rights, privacy, and credentials

- **Credentials: pass.** Agency reference, submission history, archive list,
  count, and spending-by-award endpoints all responded without an API key or
  `Authorization` header.
- **Rights: blocked for training redistribution.** The official website and API
  repositories are US Government works released under CC0, but their licence
  explicitly leaves privacy/publicity rights unaffected. It is not a clear
  field-level licence for all third-party recipient/subrecipient records. This is
  a technical finding, not legal advice.
- **Privacy: pass only for ephemeral projection.** The analysis excluded names,
  UEI/DUNS/CAGE and recipient hashes, addresses/contact details, officers and
  compensation, descriptions, other free text, and all raw identifiers from
  reports/capacity. Join identifiers existed only in memory; neither values nor
  hashes were persisted.

The official PII guidance explains aggregate and redacted assistance records.
Public availability therefore does not justify copying every field into training
text.

## Certification and schema evidence

The submission-history endpoint returned:

| Period | Published versions | Certified versions | Latest certification | Uncertified versions |
| --- | ---: | ---: | --- | ---: |
| P6 | 2 | 1 | `2024-04-25T19:10:17.071047+00:00` | 1 |
| P9 | 1 | 1 | `2024-08-02T17:00:42.45991+00:00` | 0 |

The current archives passed byte, ZIP-member, CSV-byte, CSV-hash, header, and
required-field checks:

| Layer | Columns | Annual rows | P1-P9 scoped rows | Unique transaction keys | Awards | Cross-P6/P9 awards |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Contracts A-D | 297 | 23,162 | 12,816 | 12,816 | 6,182 | 1,453 |
| Assistance 02-05 | 112 | 10,232 | 6,124 | 6,124 | 4,928 | 589 |

There are zero empty award/transaction keys and zero duplicate scoped
transaction keys. However, the live count API reports 12,817 contract and 6,125
assistance transactions—one more than each frozen August archive. This small but
real drift proves that the live API and archive are not a single immutable
snapshot; the prime count reconciliation gate fails.

## Prime-to-subaward relation evidence

For the same P1-P9 window:

| Layer | Count API | Grouped search rows | Exact repeated result tuples | Joined to frozen prime | Joined to cross-P6/P9 prime |
| --- | ---: | ---: | ---: | ---: | ---: |
| Contract subawards | 2,682 | 2,484 | 64 | 2,369 | 2,131 |
| Assistance subawards | 1,326 | 1,326 | 44 | 1,027 | 316 |

The contract count/search delta is 198 rows. The two grouped searches also
contain 478 and 151 repeated internal IDs, respectively. These observations do
not prove which backend representation is correct; they do prove that raw File F
deduplication and control-total semantics must be resolved before using subaward
counts as an oracle. This agrees with USAspending's official warning that legacy
subaward duplication is common and that other reporting-quality defects exist.

Across both groups, 3,396 returned rows join to a prime archive, and 2,447 join
to a prime with activity on both sides of the P6/P9 boundary. Those rows cover
236 distinct cross-period prime awards with subawards. They establish a real
relationship graph, not a certified graph.

## Capacity and exact bands

Tokenizer: pinned local `Qwen/Qwen3.5-4B` revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`.

| Projection | Exact tokens | Amount/date-coarsened tokens |
| --- | ---: | ---: |
| Prime transactions, identifier-free exact-dedup | 3,024,764 | 2,048,407 |
| Subawards, identifier-free exact-dedup | 152,451 | 4,685 |
| 236 cross-period prime-plus-subaward artifacts | 332,872 | 238,434 |

The formal bands remain unchanged: 32K `[32000,32768]`, 64K `[64000,65536]`,
and 128K `[128000,131072]`. Both exact and coarsened source-layer projections
exceed every lower edge, so materialization is plausible without padding or
identifier entropy. This is only a capacity upper bound: no exact-band packing,
formal near-duplicate audit, or candidate was run.

Admissible certified-chain tokens are **0**, with gaps of -32,000, -64,000, and
-128,000 to the respective lower edges. Uncertified prime/subaward rows cannot be
used to fill those gaps.

## Deterministic task design and current blocker

The source-defined program is:

1. freeze the selected certified P6 and P9 submission versions and hashes;
2. validate File C and join PIID or FAIN/URI to D1/D2 using broker rules C8/C11;
3. reconcile File C/D1 obligations with C23.1 and P6-to-P9 outlay continuation
   with C27.1;
4. attach File F as an explicitly non-certified, prime-reported layer and remove
   exact duplicate source records;
5. emit period delta, orphan, mismatch, duplicate, raw, and reconciled controls.

An answer-changing remove-one is meaningful only after a selected P9 File C row
is observed: removing it must change a TAS-award delta or create an orphan/control
mismatch. At present that counterfactual cannot execute.

Shortcut risk is high and untested. Award keys make lexical retrieval easy, and
precomputed aggregate totals would collapse the task. Any future conversion must
pass unchanged 4K/8K/16K raw-window, lexical/dense retrieval, remove-one,
near-duplicate, exact-band, derived-view, and truncation gates. Totals must be
oracle outputs, not context hints.

## Actionable next step

Do not generate from this snapshot. Retry the two official File C exports only
after the download service is healthy, freeze the certified submission version
and all source hashes in one acquisition window, and require exact C8/C11/C23.1/
C27.1 replay before attaching any File F rows. If the API cannot expose the
as-certified historical snapshot—or if field-level training reuse remains
unclear—retire the certified-period formulation and evaluate a separate
non-certified public-spending reconciliation task rather than relabeling it.

Reproduction:

```bash
uv run python reports/p47_usaspending_certified_period_preflight.py
uv run ruff format --check reports/p47_usaspending_certified_period_preflight.py
uv run ruff check reports/p47_usaspending_certified_period_preflight.py
jq empty configs/p47_usaspending_certified_period_reconciliation_preflight_v1.json \
  reports/p47_usaspending_certified_period_preflight_v1.json
```

Frozen artifact hashes before commit:

- config: `6f24c0dc3f06919e80cb9c3dfe03f23518295a49edb20de12824c5c903adf89c`
- reproducer: `f3325a7fd19dc78a2b891540695227196b366d00c74ff753bee2894ada862554`
- aggregate report: `1a31b698f6a1f09350ca2843953fdaef8f1c4e586b6a7a604c204fb52d890b34`
- source register: `01fd18e6f8428c3190124c331269dad9689366067087ac74d136f032689e48ac`
