# P14 Company Berkshire source-acquisition closeout

Date: 2026-09-02

Outcome: **source foundation GREEN; no Company quota increment**. Four
issuer-owned Berkshire Hathaway annual-report PDFs were fetched from the
issuer's official HTTPS host, replay-extracted with the project-locked
`pypdf==6.0.0`, sectioned, signed by the current source role, and loaded
through the generic fail-closed `issuer_official_pdf` source contract. The
subsequent Company task experiment is separately closed at 0/9; that result
does not invalidate this reusable source inventory.

## Official source receipts and natural capacity

The official index is
`https://www.berkshirehathaway.com/reports.html`. The exporter permits only
the four exact same-host paths below and rejects redirects, non-PDF content,
unexpected PDF magic, and byte/parser replay drift.

| Year | Official URL | Bytes | PDF SHA-256 | Pages | Qwen tokens |
|---:|---|---:|---|---:|---:|
| 2021 | `https://www.berkshirehathaway.com/2021ar/2021ar.pdf` | 934,618 | `f32194d1ff39b613d91d741e4f5d1c2d13dcf8ca65730b8dfe7de31e4fe2e359` | 143 | 136,730 |
| 2022 | `https://www.berkshirehathaway.com/2022ar/2022ar.pdf` | 1,638,808 | `52138f7464c477b10bf5068b8a211f059f8b90ba6b9c4d16c8ee434193a70be4` | 144 | 135,857 |
| 2023 | `https://www.berkshirehathaway.com/2023ar/2023ar.pdf` | 3,003,110 | `2132b85f9c472a6f0b141551adb08d93f88db28e6a3794e7e2b13ca3f8a57b5b` | 152 | 144,513 |
| 2024 | `https://www.berkshirehathaway.com/2024ar/2024ar.pdf` | 1,852,116 | `6aa0a501680c50a57e0a1a2e841b46800e1fed828a8a7889d83d8eae7953e4bb` | 150 | 138,429 |
| **Total** | — | **7,428,646** | — | **589** | **555,529** |

The extracted text SHA-256 values are respectively
`e0b2f966c49c05dd136c5d9d350a6278f5189f1d95bf21d1b7c8ff3e226bad09`,
`8b249fe60885130c57919c3ccfc621e2c860e80e1543548db7249878cb5549af`,
`8365f5e38aa6ad64dc0f1f68c09b2d81af85e72b71a83c1d89c493eb36424096`,
and
`115f6a438028ea7dbeda10d85c257c03004a54d5a68a8b7dd91422862b38f1d0`.
The Item 1, Item 1A, and Item 1C narrative pool totals 115,874 exact Qwen
tokens, so source capacity is naturally above 64K without padding or duplicate
records.

## Dependency and trust contract

`pypdf==6.0.0` is an intentional `synthesis` optional dependency. The
standard library, project environment, and host tools had no PDF text
extractor; locking one parser was necessary to make PDF bytes-to-text replay
deterministic and auditable. No additional dependency was added.

The generic source contract records and replays:

- raw PDF SHA-256 and bytes;
- extracted UTF-8 text SHA-256 and bytes;
- parser name/version, page count, and non-empty page count;
- bounded section ranges and hashes;
- fact spans against the extracted text;
- adjacent official-report relations;
- source-role producer attestation and bundle binding.

The Berkshire exporter remains entity-specific through its exact official
host/URL allowlist. The generic loader does not impersonate SEC or issuer-IR
HTML workflows and is available for a second independently allowlisted issuer.

## Signed inventory

- Directory:
  `data/source_inventory/p14_company_berkshire_official_annuals_v1`
- Inventory SHA-256:
  `b35a62a27ac47341b8ba33079127ce508fbca921937260170dc874b132fdda15`
- Bundle SHA-256:
  `3259051381e5da25a843be0d041ebec378927d928b7b9f290c44c4f9dbeca65b`
- Binding digest:
  `22b7cf9febee608f7333a5aa5c62b61fab4b80d9e103e9aa71270758384a9c02`
- Workflow:
  `source:issuer_official_pdf:dec845667b2e971e8123fb22`
- Replay result: 4 records, 3 adjacent relations, `real_public`

A canonical inventory search found no prior Berkshire issuer binding, CIK
`0001067983`, or any of these four official PDF records. Profile selection
remains authoritative; the failed task experiment contributes 0 quota.

## Focused verification

```bash
uv run --extra synthesis python -m pytest -q \
  tests/test_berkshire_annual_report_export.py
uv run --extra synthesis ruff check \
  longworld/core/issuerpdfworkflow.py \
  scripts/export_berkshire_annual_reports.py \
  tests/test_berkshire_annual_report_export.py
```

The five exporter/loader tests cover the successful replay, text tamper,
host/path rejection, signed bundle load, and bundle tamper rejection. This
source foundation does not relax near-duplicate, exact-band, derived-view,
truncation, source-lineage, or source-receipt gates.
