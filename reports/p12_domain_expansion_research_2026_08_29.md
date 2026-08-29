# P12 domain expansion: official-source research

Date: 2026-08-29

This report ranks source families by their ability to produce real longitudinal
workflows whose answers depend on document bodies, version relations, and
cross-stream state. Public access is not treated as permission to redistribute
third-party attachments, comments, or filings.

## Priority order

| Priority | Domain                | Real workflow                                                                        | First executable tasks                                                                      | Main boundary                                                                                                                      |
| -------- | --------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| 1        | IETF standards        | draft revisions → WG/IESG state → RFC → errata → updates/obsoletes                   | normative change introducer; effective requirement at cutoff; trace compliance after errata | Preserve notices and exact-byte receipts; explicitly record PII/test-key redactions; do not label generated summaries as IETF text |
| 2        | Cybersecurity         | CVE → NVD/CPE → KEV → vendor advisory → patch/backport → release                     | minimum safe release; patch origin; KEV deadline eligibility; affected configuration        | Vendor advisories and commits require their own license/allowlist                                                                  |
| 3        | Regulatory rulemaking | proposal → comments/analysis → final rule → correction → effective CFR text          | applicable version; amended deadline; threshold/exception eligibility; CFR delta            | Limit the first slice to government-authored text; scan public comments and attachments for PII and copyright                      |
| 4        | Product safety/recall | complaint → investigation → recall → remedy → quarterly completion → scope expansion | campaign eligibility; current remedy; completion delta; superseding campaign                | Prefer agency-authored summaries and structured fields; scan narratives and manufacturer attachments                               |
| 5        | Patent prosecution    | application → office action → amendment/response → allowance/rejection → grant       | claim set at cutoff; claim survival; response chain; priority/continuation                  | Applicant-authored materials and non-patent literature need separate rights review                                                 |

Second-wave candidates are ClinicalTrials/FDA, procurement/grants, and legal
opinions/dockets. They remain valuable but have stronger third-party copyright,
history availability, access, medical-safety, or PII constraints.

## First vertical slice: IETF standards

The official Datatracker API exposes document identities, revision histories,
and multiple orthogonal draft states. The IETF archive retains active and
expired Internet-Drafts, and the RFC Editor is the canonical RFC source. An
earlier interactive observation indicated a much longer RFC 9421 history, but
that observation had no repository-bound request/receipt and is not admitted as
audited evidence.

The first audited local slice instead pins consecutive revisions 17--19 and RFC
9421 in `configs/p12_wave3_ietf_rfc9421_fetch_request_v1.json`. Its signed
disabled manifest binds four primary records, two Datatracker supporting
records, two `revision_of` edges, and one `published_as` edge. The local
manifest SHA-256 is
`4238d1cdabc5f4a459a869c29e70bfb278ca3981131171a60f06973a221cf124`;
16 exact-digest-approved public private-key test-vector blocks and 17 email
occurrences were redacted before the common fail-closed public scanner accepted
the final payload. Unapproved or incomplete private-key material still fails.
Each response has its own monotonic retrieval observation timestamp. The
Datatracker relation timestamp is explicitly a retrieval observation, not a
fabricated publication event time. This
proves source acquisition and relation grounding, not a train-ready world or a
16/32/64K curriculum.

The first source contract must bind every record and relation to exact bytes:

- record ID, revision, canonical URL, retrieval URL, raw SHA-256, observed time,
  license notice, and text span;
- adjacent `revision_of`, `published_as`, `updates`, and `obsoletes` relations
  with evidence at both endpoints;
- disabled source-inventory status and an independent source attestation;
- official HTTPS host/path allowlists, rate limiting, bounded retries, receipt
  hashes, secret/PII scanning, and no filename-derived facts.

The first Standards world should implement three independent answer programs:

1. `normative_change_introducer`: identify the first revision that introduced,
   removed, or changed a specific normative requirement.
2. `effective_requirement_at_cutoff`: resolve the controlling requirement using
   publication time, updates/obsoletes relations, and erratum disposition.
3. `trace_compliance_after_errata`: parse a body-bound numeric, state, or error
   constraint and evaluate a supplied protocol trace.

Length growth is admitted only when each tier adds source records, substantive
normative spans, authentic relations, answer operands, proof depth, and
event-bearing tokens. Whole-document revision copies must be deduplicated into
non-overlapping, semantically changed hunks. The 128K tier remains disabled
until a real dependency RFC or second standards workstream is essential.

Required negative tests include CF replay equality, remove-one failure,
single-essential insufficiency, body corruption, MUST/MAY or numeric corruption,
revision-order corruption, verified/rejected erratum swaps, updates/obsoletes
edge removal, 4K/8K/16K windows, BM25/embedding top-k, and near-duplicate span
detection.

## Existing pipeline expansion

New-domain work does not replace current source-bound scale-out. Ruff GitHub and
a new three-revision arXiv workflow are being run through the existing
candidate → exact-band preflight → dense ranking → strict replay/audit →
promotion chain. Incomplete 16/32/64K worlds stop before dense replay. Rejected
rows, raw inventories, and discovery URLs do not increase qualified counts.

## Official references

- IETF Datatracker API: https://datatracker.ietf.org/api/
- IETF open records and Internet-Draft archive: https://www.ietf.org/about/open-records/
- RFC Editor errata: https://www.rfc-editor.org/errata.php
- IETF Trust copyright FAQ: https://trustee.ietf.org/documents/trust-legal-provisions/copyright-policy-and-tlp-faq/
- NVD API transition and change history: https://nvd.nist.gov/General/News/api-20-announcements
- CISA Known Exploited Vulnerabilities: https://www.cisa.gov/known-exploited-vulnerabilities-catalog
- Regulations.gov API v4: https://open.gsa.gov/api/regulationsgov/
- Federal Register API: https://www.federalregister.gov/developers/documentation/api/v1
- NHTSA datasets and APIs: https://www.nhtsa.gov/nhtsa-datasets-and-apis
- USPTO Patent File Wrapper API: https://data.uspto.gov/apis/patent-file-wrapper/documents
- ClinicalTrials.gov API: https://clinicaltrials.gov/data-about-studies/learn-about-api
- openFDA drug enforcement API: https://open.fda.gov/apis/drug/enforcement/
- SAM.gov public opportunities API: https://open.gsa.gov/api/get-opportunities-public-api/
- USAspending API: https://api.usaspending.gov/docs/endpoints
