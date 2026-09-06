# P46 Ofgem price-cap workbook source register

Observed: 2026-09-04 UTC.

## Retrieval method

`parallel-cli` was not installed, so no dependency was added. Discovery used a
read-only web search restricted to `ofgem.gov.uk`, followed by direct HTTPS
retrieval from the official Ofgem attachment host. Workbook bytes existed only
under `/tmp` or an automatically removed `/tmp/longworld-p46-*` directory.

## Official publication and reuse statements

- [Energy price cap (default tariff) levels](https://www.ofgem.gov.uk/energy-regulation/domestic-and-non-domestic/energy-pricing-rules/energy-price-cap/energy-price-cap-default-tariff-levels)
  is the official attachment index. It states that Ofgem reviews the cap every
  three months and warns that the XLSX attachments are not fully accessible.
- [Ofgem copyright](https://www.ofgem.gov.uk/c-ofgem-2026) states that website
  material is Crown copyright unless otherwise indicated and permits reuse,
  excluding logos, under the Open Government Licence. The permission does not
  cover material identified as third-party copyright.
- [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)
  is the referenced licence. Attribution and the licence notice remain required.

This is a technical reading, not a legal opinion. Every inspected workbook also
contains `Internal Only` print-header/custom-property markers despite being
published on the public attachment index. That conflict is recorded as an
explicit rights-review blocker; raw workbooks and those markers are not approved
for redistribution in a LongWorld candidate.

## Frozen workbook observations

| Period | Official attachment | Bytes | SHA-256 |
|---|---|---:|---|
| 2024Q4 | [Default tariff cap level v1.23](https://www.ofgem.gov.uk/sites/default/files/2024-08/Default_tariff_cap_level_v1.23.xlsx) | 4,236,086 | `b4aa5e7b1504d706e5297ad525857dde2a586db5a981b79fa63f85b575c8f267` |
| 2025Q4 | [Energy price cap levels: pre-levelised rates model](https://www.ofgem.gov.uk/sites/default/files/2025-08/energy-price-cap-levels-pre-levelised-rates-model.xlsx) | 5,040,045 | `d0cf3749e43081bec2f6503b3440a884044208703cd2f0ec53801f4da2d73957` |
| 2026Q3 | [Energy price cap levels: pre-levelised rates model v1.30](https://www.ofgem.gov.uk/sites/default/files/2026-05/Energy-price-cap-levels-pre-levelised-rates-model-v1.30-July-September-2026.xlsx) | 5,240,596 | `f5b5c04a48b130ccf654ee9e7bfe81620e2fe85e0837004007fa36476c6b1a9e` |

All three downloads were HTTP 200 OOXML ZIP containers and passed `unzip -t`.
The committed aggregate report stores final URLs, response metadata, byte
lengths, and hashes, but not workbook bytes, document-author fields, comments,
people metadata, or external-link targets.

## Source-boundary findings

- No VBA project, macro-enabled content type, ActiveX payload, executable, DLL,
  JavaScript payload, embedded OLE object, encrypted member, duplicate member,
  or unsafe archive path was observed.
- The packages contain 11, 11, and 12 external relationship targets. Only target
  scheme counts are retained: legacy file/UNC, HTTP, and HTTPS links were seen.
- Each workbook has 1,027 external defined-name entries and 1,112 broken
  `#REF!` defined-name entries. Static worksheet formulas contain zero external
  workbook references and reference none of the external defined names, so these
  appear stale, but candidate conversion must prove and enforce their exclusion.
- Core document properties expose author/modifier metadata. Those package parts,
  comments, images, print headers, and external relationship targets are outside
  the training-text boundary.

## Status

The sources are authentic and have ample structural capacity. They are not
candidate-ready: the embedded classification markers need an explicit rights
decision, and a network-disabled cross-engine formula oracle has not yet been
installed or validated.
