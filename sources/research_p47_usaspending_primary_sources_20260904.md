# P47 USAspending certified-period reconciliation source register

Observed: 2026-09-04 UTC.

## Research boundary

The preflight asks whether one official USAspending slice can support this
specific chain without changing any LongWorld gate:

`certified NASA FY2024 P6/P9 submission -> File C account-by-award row ->
D1/D2 prime award and transaction -> File F subaward`.

Certification is deliberately limited to the agency account submission. File F
is a separately sourced prime-recipient reporting layer. No medical, recipient,
contractor-performance, policy-effect, fraud, or causal conclusion is in scope.
No raw transaction or subaward row was retained.

## Official API and certification evidence

- [Submission history API contract](https://github.com/fedspendingtransparency/usaspending-api/blob/master/usaspending_api/api_contracts/contracts/v2/reporting/agencies/toptier_code/fiscal_year/fiscal_period/submission_history.md)
  defines publication and certification timestamps for an agency fiscal period.
  The public endpoint returned one certified NASA submission for P6 and one for
  P9. P6 also has an older published but uncertified version, so selecting the
  certified version is essential.
- [Account download API contract](https://github.com/fedspendingtransparency/usaspending-api/blob/master/usaspending_api/api_contracts/contracts/v2/download/accounts.md)
  defines File C (`award_financial`) downloads by fiscal year and period. P6 and
  P9 requests were accepted without credentials, but both generated jobs later
  reported `failed`; their file objects returned HTTP 403 waiting pages.
- [Certified-data loading order](https://github.com/fedspendingtransparency/usaspending-api/blob/master/loading_data.md)
  states that certified agency data can be published after certification and
  that File C is matched to award records after D1/D2 loading. That describes the
  intended bridge; it does not substitute for observing the two requested File C
  extracts.
- [Bulk monthly-file list contract](https://github.com/fedspendingtransparency/usaspending-api/blob/master/usaspending_api/api_contracts/contracts/v2/bulk_download/list_monthly_files.md),
  [award download contract](https://github.com/fedspendingtransparency/usaspending-api/blob/master/usaspending_api/api_contracts/contracts/v2/bulk_download/awards.md),
  [spending-by-award contract](https://github.com/fedspendingtransparency/usaspending-api/blob/master/usaspending_api/api_contracts/contracts/v2/search/spending_by_award.md),
  and [download-count contract](https://github.com/fedspendingtransparency/usaspending-api/blob/master/usaspending_api/api_contracts/contracts/v2/download/count.md)
  define the other no-key endpoints used for aggregate measurement.

All API calls succeeded without an `Authorization` header or API key. That is an
observed access result, not a promise that endpoint availability will never
change.

## Official data-model and oracle evidence

- [File C](https://github.com/fedspendingtransparency/usaspending-website/blob/master/src/content/about-the-data/file-c.md)
  is the agency-submitted account breakdown by award. It contains lifetime award
  obligation/outlay data broken down by Treasury account, program activity,
  object class, and related account dimensions.
- [File D1](https://github.com/fedspendingtransparency/usaspending-website/blob/master/src/content/about-the-data/file-d1.mdx)
  derives procurement transactions from FPDS; [File D2](https://github.com/fedspendingtransparency/usaspending-website/blob/master/src/content/about-the-data/file-d2.mdx)
  derives assistance transactions from FABS.
- [Broker SQL validation rules](https://github.com/fedspendingtransparency/data-act-broker-backend/blob/master/dataactvalidator/config/sqlrules/sqlRules.csv)
  provide deterministic relations: C8 and C11 require File C award identifiers
  in D2/D1, C23.1 reconciles File C transaction obligations to D1, and C27.1
  checks continuation of File C outlay balances across periods. A future oracle
  should reuse these source-defined relations rather than invent a heuristic.
- [File F](https://github.com/fedspendingtransparency/usaspending-website/blob/master/src/content/about-the-data/file-f.mdx)
  is submitted by prime recipients through SAM.gov. The official
  [subaward data-quality note](https://github.com/fedspendingtransparency/usaspending-website/blob/master/src/content/about-the-data/subaward-data-quality.mdx)
  warns that duplication is common and describes missing, late, inconsistent,
  and incorrectly transcribed values. Federal agency personnel do not submit
  these rows themselves.
- The [unique award-key methodology](https://github.com/fedspendingtransparency/usaspending-website/blob/master/src/content/about-the-data/unique-award-key-methodology.mdx)
  changed on 2025-07-04 for assistance awards. Therefore a current regenerated
  FY2024 archive cannot be assumed to preserve the exact key topology visible at
  FY2024 certification time.

## Rights and privacy boundary

The official [USAspending website licence](https://github.com/fedspendingtransparency/usaspending-website/blob/master/LICENSE.md)
and [API licence](https://github.com/fedspendingtransparency/usaspending-api/blob/master/LICENSE)
place those government projects in the US public domain and apply CC0
worldwide. The licence also says that CC0 does not affect privacy or publicity
rights. It is not explicit dataset-field-level clearance for all third-party
recipient and subrecipient information. Public access and public-interest
reporting therefore pass source inspection but do not, on their own, authorize a
training-text release. Formal field-level reuse review remains open.

Official privacy documentation distinguishes [PII aggregate records](https://github.com/fedspendingtransparency/usaspending-website/blob/master/src/content/about-the-data/pii-and-aggregate-records.mdx)
and [PII-redacted records](https://github.com/fedspendingtransparency/usaspending-website/blob/master/src/content/about-the-data/pii-and-redacted-records.mdx).
The preflight excluded recipient/subrecipient names, UEI/DUNS/CAGE and recipient
hashes, addresses and contact fields, officer names and compensation, narrative
descriptions, free-text location fields, and all raw source identifiers from
reports and projected capacity. Source identifiers existed only ephemerally as
join keys and neither they nor their hashes were persisted.

## Frozen byte sources

The official monthly-file API advertised these current FY2024 NASA archives:

| Layer | URL date | Bytes | SHA-256 | Inner CSV bytes | Inner CSV SHA-256 |
| --- | --- | ---: | --- | ---: | --- |
| Contracts | 2026-08-06 | 10,138,004 | `88da9b1bb602a79cd30e6187a1fe3a63c826a0dd2295701b1eccd003327add2f` | 67,760,720 | `86c7d0fa693f25257ddf9a637ed730ad8e0eba26318060a34def037ebd46768f` |
| Assistance | 2026-08-06 | 3,750,666 | `f4a6ff63abeca97b70e10021826affab42081266b3c01f50c8e00a3b14974026` | 21,561,691 | `8c67504bf777f0d8e72bbc30421e239ca027edd59e51a677da4421a59ab2633c` |

The inner filenames carry 2026-08-08 while the list endpoint reports
2026-08-06. Both are recorded; neither archive is represented as a historical
P6/P9 certification snapshot. Archive bytes lived only in `/tmp` or process
memory and are not part of Git.

## Admission status

The authentic prime/subaward layers have enough projected volume for all three
target lengths, but the requested certified chain has zero admitted tokens. The
missing File C extracts, current-versus-certified snapshot mismatch, independent
File F provenance, observed count/search inconsistencies, and unresolved
field-level reuse boundary make conversion unsafe. Candidate generation remains
disabled.
