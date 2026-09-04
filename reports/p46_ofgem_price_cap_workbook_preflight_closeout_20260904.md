# P46 Ofgem price-cap workbook preflight closeout

## Outcome

**Capacity: PASS. Conversion: FAIL-CLOSED. Training inventory contribution: 0.**

Three official pre-levelised Ofgem price-cap workbooks span 2024Q4, 2025Q4,
and 2026Q3. After eliminating copied formula instances by replacing A1-style
coordinates and dropping cached values from formula-template units, the
cross-version union still contains **18,224 units / 469,328 exact Qwen tokens**.
That is enough raw unique capacity to pack 32K, 64K, and 128K contexts, but it is
not evidence that an exact-band, answer-dependent candidate exists.

Candidate generation remains disabled for two independent reasons:

1. All three publicly linked workbooks carry `Internal Only` print-header or
   custom-property markers. Ofgem's website-wide Crown copyright/OGL statement
   supports reuse unless otherwise indicated, but this conflicting marker needs
   an explicit rights decision before derived training-text redistribution.
2. The packages carry stale external-link/broken-name state, and this environment
   has neither `openpyxl` nor LibreOffice. A network-disabled, cross-engine oracle
   has therefore not proved a self-contained formula closure.

No candidate was generated, promoted, or counted.

## Frozen sources and OOXML safety

| Period | Bytes | SHA-256 | Sheets | Names | Formula cells |
|---|---:|---|---:|---:|---:|
| 2024Q4 | 4,236,086 | `b4aa5e7b1504d706e5297ad525857dde2a586db5a981b79fa63f85b575c8f267` | 38 | 1,558 | 155,347 |
| 2025Q4 | 5,040,045 | `d0cf3749e43081bec2f6503b3440a884044208703cd2f0ec53801f4da2d73957` | 41 | 1,560 | 161,910 |
| 2026Q3 | 5,240,596 | `f5b5c04a48b130ccf654ee9e7bfe81620e2fe85e0837004007fa36476c6b1a9e` | 41 | 1,560 | 164,079 |

The archives contain 176/187/238 members and expand to 25,496,389 /
30,068,265 / 30,689,661 bytes. Maximum observed member compression ratios are
20.14x, below the frozen 1000x ceiling. All have zero unsafe paths, duplicate or
encrypted members, macro-enabled content types, VBA, ActiveX, executable, DLL,
JavaScript, or embedded OLE payloads. Printer-setting binaries are counted but
excluded from semantic content.

The `Internal Only` marker occurs in 39/40/40 package parts. Document properties,
comments/people metadata, images, print headers, and external relationship
targets are excluded from capacity and any future candidate boundary.

## Capacity after removing copied formula templates

Tokenizer: `Qwen/Qwen3.5-4B` at revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`, local files only.

| Period | Non-empty cells | Exact-dedup tokens | Template-dedup units | Template-dedup tokens | New tokens vs previous |
|---|---:|---:|---:|---:|---:|
| 2024Q4 | 219,863 | 5,922,504 | 13,098 | 335,864 | 335,864 |
| 2025Q4 | 270,859 | 6,642,213 | 14,452 | 374,017 | 42,478 |
| 2026Q3 | 273,500 | 6,544,189 | 15,433 | 390,367 | 92,141 |
| Cross-version union | — | 14,714,242 | 18,224 | 469,328 | — |

Template-dedup removes 94.04%–94.66% of non-empty cell instances. Formula units
retain sheet, operators, functions, and literal constants but omit coordinates
and cached results; non-formula literals remain exact per sheet. This is a
conservative capacity preflight, not a packing receipt. Exact-band, near-dup,
truncation, source-lineage, remove-one, and derived-view gates remain unchanged.

## Formula DAG branches

The static worksheet graph exposes at least four materially different branches;
all terminate through the calculator sheet and the `1a Default tariff cap`
output's dynamic `INDIRECT` selector:

| Branch | 2026Q3 source → calculator evidence | Output hop |
|---|---|---|
| Electricity direct fuel | `3a DF` → `ElecSingle_Other_Benchmark` (700 formulas) | 6 `INDIRECT` selectors → `1a Default tariff cap` |
| Electricity network | `3e NC-Elec` → `ElecSingle_Other_Benchmark` (700 formulas) | same output hop |
| Gas network | `3f NC-Gas` → `Gas_Other_Benchmark` (1,372 formulas) | same output hop |
| Inflation and debt | `3g CPIH` (3,192), `3k EBIT` (1,372), `3n DRC` (924) → `ElecSingle_Other_Benchmark` | same output hop |

The model evolution is substantive rather than a relabel: the 2024 workbook
lacks `3m CO`, `3n DRC`, and `3o IC`; the 2025 and 2026 editions add these input
branches. Static worksheet formulas contain zero bracketed external-workbook
references and reference none of the 99 unique external defined names. However,
each package still carries 1,027 external-name definitions, 1,112 broken names,
and 11/11/12 external relationship targets, so stale-link exclusion must be
machine-enforced rather than assumed.

## Deterministic oracle contract for a future conversion

1. Freeze workbook bytes by the hashes above. Reject drift before parsing.
2. Strip document properties, comments/people metadata, images, headers/footers,
   external-link parts, and all broken/external defined names. Reject if a retained
   formula references any stripped name or external workbook.
3. Treat retained non-formula inputs as signed boundary values keyed by workbook
   hash, sheet, and cell. Never dereference external relationship targets.
4. With `openpyxl`, load formula and `data_only` views using `keep_links=False`;
   build a dependency closure for frozen output cells and reject unresolved or
   error-valued dependencies.
5. With LibreOffice, run headless in an isolated temporary profile, with network
   access and link updates disabled. Recalculate only the same closure, export
   twice, and require byte-stable canonical cell receipts.
6. Require Python/LibreOffice agreement under an explicitly declared decimal
   tolerance. A counterfactual must mutate one signed input branch and change the
   target output; remove-one must fail the answer proof.

This task did not add either engine as a dependency. Until the rights marker and
cross-engine checks pass, `train_ready=false` and `candidate_generation_allowed=false`.

## Reproduction and artifacts

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=/workspace/wynckeliao/.hf \
  uv run python reports/p46_ofgem_price_cap_workbook_preflight.py
uv run ruff format --check reports/p46_ofgem_price_cap_workbook_preflight.py
uv run ruff check reports/p46_ofgem_price_cap_workbook_preflight.py
jq empty configs/p46_ofgem_price_cap_workbook_preflight_v1.json \
  reports/p46_ofgem_price_cap_workbook_preflight_v1.json
```

- Request config: `configs/p46_ofgem_price_cap_workbook_preflight_v1.json`
- Aggregate report: `reports/p46_ofgem_price_cap_workbook_preflight_v1.json`
- Reproducer: `reports/p46_ofgem_price_cap_workbook_preflight.py`
- Source register: `sources/research_p46_ofgem_primary_sources_20260904.md`

Frozen artifact hashes before commit:

- config: `fa6d7a9e4b5221da9328cb86f611eb8c9bab452fd7a3a0e9f280832b50a6c3c0`
- reproducer: `b6df400e12b77cb83e2ba239caaff0c28bd71ce0b8dffa326fc66a232a377e78`
- aggregate report: `903b84d139cb30f698a4ad3c33af745f679530bb8be0ef1ce0d249defbd948f3`
