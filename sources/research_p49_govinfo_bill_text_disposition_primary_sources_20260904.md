# P49 GovInfo bill-text disposition primary-source register

Observed 2026-09-04 UTC. Research used official `govinfo.gov` sources only.
Documentation and XML were inspected from `/tmp`; no raw bill, Public Law, or
Bill Status XML is committed. This is a technical provenance record, not a
legal opinion.

## Collection documentation

| Source | Official URL | Evidence used | Observed SHA-256 |
| --- | --- | --- | --- |
| Congressional Bills help | https://www.govinfo.gov/help/bills | GPO publishes bill versions from introduction through passage by both chambers; final published versions are available, XML bulk downloads are supported, and EAS, EAH, EH, and ENR version meanings are defined. | `44de78d3c28a3162e9dcb7ce83d0b58773c8420efe82707923c6454775bd3a87` |
| Bill Status help | https://www.govinfo.gov/help/bill-status | Identifies the official Bill Status collection and its structured data. | `8ac8b92ed7adaf72fc8a3c127a9564803526ade9a988b57f1f24e99652a5bb6d` |
| Bill Status resources | https://www.govinfo.gov/bulkdata/BILLSTATUS/resources | Official bulk-data schemas and user-guide resources for Bill Status XML. | `d477ced76ee7e72f04cd6327f1dc9ccf1115be9e224fc4b134258b4071e4ef35` |
| GovInfo policies | https://www.govinfo.gov/about/policies#copyright | Explains 17 USC 105 public-domain treatment for U.S. Government works and warns that third-party copyrighted content in a government publication is not automatically reusable. | `5189ea6f00ac5b788b6937d6024e9f5924ed958305e31afc7d94f7e4bc40c0a9` |

The help pages are current web pages; their hashes are observation receipts,
not immutable release identifiers. The executable preflight freezes the source
XML itself by exact length and SHA-256.

## Frozen official source bundle

| Chain/stage | Official URL | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| H.R. 815 Bill Status | https://www.govinfo.gov/bulkdata/BILLSTATUS/118/hr/BILLSTATUS-118hr815.xml | 1,110,644 | `41fbdbabe08dc06e609c56b117aa3d7c31844f0277bf7ac7270602aa6f99b6b2` |
| H.R. 815 EAS | https://www.govinfo.gov/content/pkg/BILLS-118hr815eas/xml/BILLS-118hr815eas.xml | 145,614 | `9a7ef67bcdce847f6f354a9c7e28df495e11fc938ddd80f83925b60de0f2631a` |
| H.R. 815 EAH | https://www.govinfo.gov/content/pkg/BILLS-118hr815eah/xml/BILLS-118hr815eah.xml | 492,555 | `f8656c3c1bfe96a228a50a81d8913177addd984dd91cf8e45d0253c355704a21` |
| H.R. 815 ENR | https://www.govinfo.gov/content/pkg/BILLS-118hr815enr/xml/BILLS-118hr815enr.xml | 491,950 | `06d095fd27b2f6ea27612e01aae48d2b61c93352f6a4060aa4bfe7cd3cabbfc7` |
| Public Law 118-50 | https://www.govinfo.gov/content/pkg/PLAW-118publ50/uslm/PLAW-118publ50.xml | 772,982 | `9bbe459e967a14803435bd970e0e27fcb2767caf4b73a23e041b8459d506d28a` |
| H.R. 4366 Bill Status | https://www.govinfo.gov/bulkdata/BILLSTATUS/118/hr/BILLSTATUS-118hr4366.xml | 1,459,193 | `4133df177ede5fd7b4ee70df83c1fe9d55fe1b9d5df8c9bd98fffc337a5225b3` |
| H.R. 4366 EAS | https://www.govinfo.gov/content/pkg/BILLS-118hr4366eas/xml/BILLS-118hr4366eas.xml | 835,739 | `7f5136031fbdc527798988b2527c9f2125a6c5f8901b44f58fd68c2a336ac80a` |
| H.R. 4366 EAH | https://www.govinfo.gov/content/pkg/BILLS-118hr4366eah/xml/BILLS-118hr4366eah.xml | 1,840,855 | `9b470b0e42d91b2f580f8b9314a2fb88a062df3654665aa6401200ee49f6af24` |
| H.R. 4366 ENR | https://www.govinfo.gov/content/pkg/BILLS-118hr4366enr/xml/BILLS-118hr4366enr.xml | 1,839,030 | `63ae3f506d9e06145b3713736e8c3122e90fe1261abcc8a67b19ab726e14cdb9` |
| Public Law 118-42 | https://www.govinfo.gov/content/pkg/PLAW-118publ42/uslm/PLAW-118publ42.xml | 2,586,115 | `563491684e2c60d3f80b6cc642c2bc92b9c91b08cbbcc5270de22d436ae80ddf` |

The ten sources total 11,574,677 bytes. Hashing
`url:sha256(raw)\n` in config traversal order gives bundle SHA-256:

```text
2b0adaf00511007bdcdb4db06ff8ea426da59ff8c7cbd9a70876b9ad210275b2
```

The Public Law 118-42 URL is absent from that bill's `textVersions` format list.
It is not inferred from title alone: the Bill Status record supplies the Public
Law 118-42 relation and became-law action, while the Public Law XML identifies
document 42, approval date 2024-03-09, and links back to `/us/bill/118/hr/4366`.
Public Law 118-50 is checked the same way.

## Version and action relations

For each bill, the reproducer requires the EAS, EAH, and ENR type/URL relations
from the main bill's `textVersions` node, then requires all three dated action
edges from the main bill's `actions` node:

| Bill | EAS → EAH | EAH → ENR | ENR → final law |
| --- | --- | --- | --- |
| H.R. 815 | 2024-04-20 House agreed with an amendment to the Senate amendment | 2024-04-23 Senate agreed to the House amendment | 2024-04-24 became Public Law 118-50 |
| H.R. 4366 | 2024-03-06 House agreed to the Senate amendment with an amendment | 2024-03-08 Senate agreed to the House amendment | 2024-03-09 became Public Law 118-42 |

The status files link to the corresponding `congress.gov` legislation records
and contain the Library of Congress action stream. This preflight uses the
GovInfo bulk serialization because it is anonymously downloadable and hashable;
the Congress.gov HTML action pages returned HTTP 403 to the automated client and
are not treated as independent receipts.

## Rights, privacy, and serialization boundary

All eight selected legislative-text XML files contain this exact metadata:

> Pursuant to Title 17 Section 105 of the United States Code, this file is not
> subject to copyright protection and is in the public domain.

The preflight nevertheless does not assume that arbitrary third-party material
inside government publications is public domain. Only legislative section text
is inspected. Metadata names, sponsors, committees, amendment authors, and
action prose do not contribute token capacity. These sources are public
legislative records, not private case/person datasets; no separate private-data
field is present in the retained section-text boundary.

## Section comparison boundary

Sections are aligned only by a deterministic structural key: normalized numbers
of enclosing division/title/subtitle/chapter/subchapter/part/subpart elements,
followed by the section number. Direct unnumbered section siblings use a 1-based
ordinal. If a key occurs more than once in either version, all units with that
key are excluded from disposition counts rather than guessed into an alignment.

Before comparison, XML presentation-only `page`, `sidenote`, `sourceCredit`, and
`note` subtrees are removed. This prevents Public Law page furniture and source
credits from masquerading as legislative changes. A same-key section is retained
when exact-normalized or when five-word-shingle Jaccard is at least 0.90; it is
otherwise modified. Missing keys are removed/added. These are deterministic
source-text dispositions, not legal interpretations of amendment effect.

Capacity uses sections of at least 80 characters, applies the same 0.90 global
near-duplicate collapse within and across all eight stages, and never splits,
truncates, pads, or clones a section. The preflight does not replace the formal
candidate near-duplicate or semantic gates.
