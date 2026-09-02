# P14 Company JPMorgan official-PDF source preflight

Date: 2026-09-02

Outcome: **source-capacity pass, integration pending, 0 quota increment**.
This is the historical preflight snapshot; the completed signed inventory and
9/9 task audit are recorded in `p14_company_jpmorgan_risk_taxonomy_20260902.md`.
JPMorgan Chase & Co. provides at least three clean, issuer-owned annual reports
whose natural extracted text independently exceeds 64K exact Qwen tokens. This
preflight does not create a source inventory or a trainable world. No PDF or
extracted text is stored in the repository.

## Official index and byte receipts

The issuer's [Annual Reports index](https://www.jpmorganchase.com/ir/annual-report)
returned HTTP 200 with `text/html;charset=utf-8`. Its HTML directly links all
four PDFs below on the same official `www.jpmorganchase.com` host. Each exact
PDF URL returned HTTP 200 and `application/pdf` without resolving to a mirror,
SEC archive, or challenge page.

| Year | Exact official URL | Status | Content-Type | Bytes | PDF SHA-256 |
|---:|---|---:|---|---:|---|
| 2021 | `https://www.jpmorganchase.com/content/dam/jpmc/jpmorgan-chase-and-co/investor-relations/documents/annualreport-2021.pdf` | 200 | `application/pdf` | 8,488,946 | `7b5f2a777e92896ebf2386b0a9f51bab731f0a975826abd42ac5a0f415092273` |
| 2022 | `https://www.jpmorganchase.com/content/dam/jpmc/jpmorgan-chase-and-co/investor-relations/documents/annualreport-2022.pdf` | 200 | `application/pdf` | 16,797,166 | `e2a82330efb82c9ab38b30cd043fa59c0c4b7debb5108559ce3c788bbe7942c6` |
| 2023 | `https://www.jpmorganchase.com/content/dam/jpmc/jpmorgan-chase-and-co/investor-relations/documents/annualreport-2023.pdf` | 200 | `application/pdf` | 6,814,192 | `bebb7c4abb278396187cadcac8006951ffcee7dde7c7b458ef364befacbd8908` |
| 2024 | `https://www.jpmorganchase.com/content/dam/jpmc/jpmorgan-chase-and-co/investor-relations/documents/annualreport-2024.pdf` | 200 | `application/pdf` | 17,104,906 | `78443e60b3188a70673ca7042d0bf9e4dc596d04072af729242886d22bd0a4e3` |

These byte hashes are observations from the bounded temporary downloads. They
are not signed source-role receipts and must be re-fetched and attested by the
eventual exporter.

## Conservative natural-text capacity

The diagnostic used project-pinned `pypdf==6.0.0` and the local tokenizer
`Qwen/Qwen3.5-4B@a7b0d22b993d71000cf2eadfb37222a67cee521e`, with no special
tokens. The clean 2022--2024 reports provide the conservative capacity basis:

| Year | PDF pages | Extracted chars | Words | Exact Qwen tokens | Tokens/word |
|---:|---:|---:|---:|---:|---:|
| 2022 | 328 | 1,209,351 | 188,269 | 345,240 | 1.83 |
| 2023 | 364 | 1,378,786 | 208,024 | 381,476 | 1.83 |
| 2024 | 372 | 1,367,197 | 206,511 | 382,075 | 1.85 |
| **Total** | **1,064** | **3,955,334** | **602,804** | **1,108,791** | **1.84** |

Each clean year independently exceeds 64K. The annual-report contents and
section bodies expose stable, cross-year chapter families:

- Management's discussion and analysis;
- Firmwide, strategic, capital, liquidity, market, and operational risk
  management;
- consolidated financial statements;
- notes to consolidated financial statements; and
- disclosure controls and procedures and internal control.

The 2022 contents page identifies MD&A introduction at printed page 46,
firmwide risk at 81, consolidated statements at 159, and notes at 164. The
2023 report identifies introduction at 48, firmwide risk at 86, consolidated
statements at 166, and notes at 171. The 2024 report identifies introduction at
52, firmwide risk at 91, consolidated statements at 172, and notes at 177.
These are issuer-authored navigation anchors, not inferred filler sections.

### 2021 exclusion

The 2021 PDF is directly retrievable and has a valid PDF byte receipt, but the
same diagnostic extractor produced 1,227,074 characters, 105,270 words, and
899,297 Qwen tokens, or 8.54 tokens per word. Its missing expected section
matches and token inflation indicate a font/text-encoding extraction problem.
The 2021 text is therefore excluded from the conservative capacity total and
must not be used until a source exporter either reproduces a clean extraction
or fails the record closed. Excluding it still leaves three complete years and
more than 1.1M natural tokens.

## Canonical-binding independence

A read-only exact search over `data/source_inventory`, `configs`, `reports`,
and `data/releases` found no canonical source record matching any of:

- official host `jpmorganchase.com`;
- annual-report filenames `annualreport-2021.pdf` through
  `annualreport-2024.pdf`;
- JPMorgan Chase CIK `0000019617`; or
- exact issuer name `JPMorgan Chase & Co.`.

The proposed source family is therefore not an alias of the occupied Amazon,
Microsoft, Apple, NVIDIA, Berkshire, SEC 403, or challenge routes. Final source
inventory and release selection remain authoritative; this negative search is
only a preflight snapshot.

## Temporary reproduction commands

The scratch directory was outside the repository:
`/tmp/p14-company-jpmorgan-scout.dmBsRp`.

Official-index linkage:

```bash
curl -L --max-time 30 -sS \
  https://www.jpmorganchase.com/ir/annual-report \
  | rg -o '/content/dam/[^" ]+annualreport-(2021|2022|2023|2024)\.pdf' \
  | sort -u
```

Bounded download and response identity, repeated for 2021--2024:

```bash
curl -L --max-time 120 -sS \
  -D /tmp/p14-company-jpmorgan-scout.dmBsRp/2024.headers \
  -o /tmp/p14-company-jpmorgan-scout.dmBsRp/2024.pdf \
  -w 'status=%{http_code} type=%{content_type} bytes=%{size_download} final=%{url_effective}\n' \
  https://www.jpmorganchase.com/content/dam/jpmc/jpmorgan-chase-and-co/investor-relations/documents/annualreport-2024.pdf
sha256sum /tmp/p14-company-jpmorgan-scout.dmBsRp/2024.pdf
```

Temporary extraction and exact token counting used the installed project
environment and never wrote extracted text into the repository:

```bash
HF_HOME=/workspace/wynckeliao/.hf TRANSFORMERS_OFFLINE=1 uv run python - <<'PY'
from pathlib import Path
from pypdf import PdfReader
from transformers import AutoTokenizer

root = Path("/tmp/p14-company-jpmorgan-scout.dmBsRp")
tokenizer = AutoTokenizer.from_pretrained(
    "Qwen/Qwen3.5-4B",
    revision="a7b0d22b993d71000cf2eadfb37222a67cee521e",
    local_files_only=True,
)
for year in range(2021, 2025):
    reader = PdfReader(root / f"{year}.pdf")
    text = "\n\f\n".join(page.extract_text() or "" for page in reader.pages)
    tokens = tokenizer.encode(text, add_special_tokens=False)
    print(year, len(reader.pages), len(text), len(text.split()), len(tokens))
PY
```

Canonical-binding search:

```bash
for root in data/source_inventory configs reports data/releases; do
  test ! -e "$root" || rg -l -i \
    -g '*.json' -g '*.jsonl' -g '*.yaml' -g '*.yml' -g '*.md' \
    'jpmorganchase\.com|annualreport-(2021|2022|2023|2024)\.pdf|0000019617|JPMorgan Chase & Co\.' \
    "$root"
done
```

## `issuer_official_pdf` reuse boundary

The newly available Berkshire official-PDF path establishes reusable mechanics
but remains intentionally issuer-specific. A JPMorgan exporter may reuse only
the generic trust-boundary behavior:

- bounded HTTPS fetch from an explicit issuer-owned host and exact URL list;
- rejection of redirects, non-200 status, non-PDF content type, oversized
  payloads, invalid PDF magic, encrypted PDFs, and empty extraction;
- pinned parser identity, raw-PDF and UTF-8 text byte hashes, page/nonempty-page
  counts, extraction replay, source-role attestation, rate limits, authorization
  receipt, and atomic no-replace publication; and
- signed same-issuer chronological `prior_official_annual_report` relations.

It must not relabel JPMorgan inputs as Berkshire records or reuse Berkshire's
hard-coded issuer, CIK, host, URL template, years, source kind/family, section
identities, or Pilot/cyber derived facts. JPMorgan needs a distinct issuer
adapter or a reviewed generic `issuer_official_pdf` contract with:

1. exact JPMorgan issuer identity and CIK `0000019617`;
2. the four official static URLs above, with 2021 fail-closed until extraction
   quality is resolved;
3. issuer-specific MD&A, risk, statements, notes, and controls section receipts
   whose byte ranges and hashes are replayed from the pinned text; and
4. an answer program that depends on cross-year JPMorgan disclosures rather
   than Berkshire facts or financial-value arithmetic already represented by
   existing Company/Finance tasks.

Only after a source-role-signed inventory, replayed section/fact receipts, and
distinct source family exist should generation start. Quota still requires a
natural 16/32/64K x full/CF/ordered 9-cell candidate, remove-one and window
necessity, near-duplicate and truncation gates, pinned dense ranking, strict
replay, and final profile selection. This preflight does not relax or satisfy
any of those gates.
