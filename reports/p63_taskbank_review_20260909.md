# P63 independent taskbank/export review

Scope: original-source design checks, context rendering, batch controller, and
persisted local-candidate validation. Shared implementation files were read-only
for this reviewer. Tests and mutations use pytest temporary directories.

## First persisted-export adversarial run

`uv run pytest -q tests/test_p63_taskbank_adversarial.py` produced
**11 failed, 1 passed** before remediation. The positive case builds and validates
the existing four-year **synthetic signed fixture** through the real task compiler,
renderer, exporter and persisted validator. Only source extraction and tokenization
use test doubles. This is not a measurement on the six real issuer sources.

The negative cases refresh the affected file hashes where appropriate; they
therefore test semantic/provenance checks beyond a simple checksum mismatch.

| Priority | Reproduced acceptance problem | Required rejection/verification |
| --- | --- | --- |
| P1 | A bound amount `500` can be redirected to the equal-valued assets/equity cell elsewhere in context. Raw span metadata stays correct, and the validator accepts the wrong final offset. | Derive expected final offsets from the fresh full-document/statement mapping and compare exact offsets, not merely quoted text. |
| P1 | Persisted rows can assert strict dependency, complete alternative-proof search, or an exact-band certificate, despite being local candidates. A forged strict profile or document count also passes. | Reconstruct and compare the emitted sample metadata contract, including explicit non-eligibility claims and actual source-document count. |
| P1 | A batch source can change after catalog pinning; successful children and unchanged config still yield a verified job under the stale catalog source hash. | Recheck both source bytes and the child's recorded source pin before counting the job. |
| P2 | Increasing `BUILD_RECEIPT.training_views` by 1,000 still passes validation. | Recompute receipt count summaries from persisted rows. |
| P2 | An empty compiler-code inventory, or one pointing only to an unrelated external file, is accepted. | Require the precise compiler-code member set and safe paths before comparing hashes. |
| P2 | `BUILD_RECEIPT.json` can be a symlink to an external receipt. | Require a regular owned export receipt and retain read-only validation. |

The batch test mocks successful subprocess boundaries while replacing a temporary
source file, representing an independently valid new source version being installed
mid-run. It does not claim that an unsigned changed source bypasses the real source
loader's HMAC checks.

## Earlier renderer findings

Previously reproduced issues included decoded-entity offset drift, joined inline
words, cross-document binding of equal values, duplicate document inclusion, hidden
negative signs, cross-cell sign borrowing, lost entity-encoded trailing parentheses,
and repeated NBSP layout inflation. Root has implemented fixes and focused tests.
The persisted-offset case above specifically checks the final exported context
rather than assuming a correct intermediate visible span is sufficient.

CSS-class visibility remains a documented renderer boundary: a simple HTML text
parser is not a browser's full computed-style implementation. No current real-source
failure is asserted from that observation alone.

## Evidence boundaries

The compiler uses real execution and source-bound task reconstruction in these
tests. Neither this test run nor a clean checksum result certifies general question
semantics, alternative proofs, strict long dependency, production source rights,
or model quality. Existing frozen releases, filters and signatures were untouched.

## Post-fix verification

Root corrected the reported validator/controller issues. The owned twelve
adversarial cases now pass; together with the compiler, renderer and export tests,
the focused invocation reports **38 passed, no skips**, in 5.16 seconds. No
blocking issue remains in this reviewed scope.

A separate calculation check on the same synthetic signed fixture produced
36 source facts, 411 proposed programs, 264 unique semantic tasks and eight
task/executor families. Their family counts sum to the unique task count.
The four reported annual revenue amounts sum to 510. Positive operating cash
filtered before summation gives 150, whereas removing that filter gives 145;
the composed instance consumes its predecessor output. Stock aggregation,
singleton aggregation and vacuous-filter cases appear in the 147 documented
rejections.

Those are fixture measurements, not the six-real-issuer batch yield. Eight named
task families are not eight certified independent proof topologies, and cross-filing
bindings are not a certificate of indispensable long-context evidence. The real
batch should be run only after source/compiler/configuration versions are frozen;
its independent persisted validator supplies a separate execution receipt.

## Final six-issuer batch check

All six actual jobs report `verified_local_candidates` in `data/candidates/p63_finance_taskbank_v1/BATCH_RECEIPT.json` (SHA256 `4ca51d9d875851ae70cfdec6b3317f074bf15990fe33bea96075ce3f0b79ee7d`). The reviewer independently checked each bound export-file hash, each source-manifest hash, and each BUILD_RECEIPT binding. This section reports real batch artifacts; it is separate from the earlier synthetic-fixture tests.

| Issuer | Split | CIK/source group | Facts | Accepted tasks/full views | Distinct contexts |
| --- | --- | --- | ---: | ---: | ---: |
| alphabet | train | 0001652044 | 36 | 197 | 13 |
| amazon | train | 0001018724 | 44 | 242 | 13 |
| amd | eval | 0000002488 | 32 | 185 | 13 |
| meta | train | 0001326801 | 36 | 213 | 13 |
| micron | train | 0000723125 | 36 | 234 | 13 |
| nvidia | train | 0001045810 | 32 | 181 | 13 |

The batch contains **1252 canonical task instances / 1252 full samples**, split **1067 train / 185 AMD eval**. There are **24 normalized source documents, 216 used source facts and 78 distinct serialized contexts** across six source collections. All semantic IDs were recomputed from compiler revision, canonical program and scope; task IDs, sample IDs and complete questions are unique in this snapshot. Context reuse across different tasks is intentional, not new source content.

Applicability proposed 2466 candidates: 8 were not instantiable, 2458 reached program validation, 1206 were rejected and 1252 were accepted. Task-family counts below describe eight implemented query families, not independent source worlds or certified long-proof topologies.

| Task family | Instances |
| --- | ---: |
| aggregate | 304 |
| cash_reconciliation | 24 |
| delta | 324 |
| filter | 128 |
| filtered_aggregate | 46 |
| lookup | 216 |
| maximum | 162 |
| ratio | 48 |

There are 20 fact-ID-abstracted AST shapes. The maximum AST depth is 4 when lookup/collect nodes are counted; at most 2 non-lookup/non-collect operators occur in a task. The 46 filter→sum tasks are the clearest two-operation compositions. The 900 cross-filing tasks describe source binding across filings, not demonstrated indispensable long-context reasoning. AMD evaluation has 0 AST shapes unseen in train, so this is an **issuer holdout, not a structural-composition holdout**.

### Same answers are not the same tasks

There are 164 groups (392 rows) sharing an identical complete answer JSON, including different metrics/scopes. Only 0 such groups cross issuer worlds. When period/operands are ignored and only `(unit, numeric value)` is compared, 168 repeated-value groups involve 451 rows; 2 scalar groups cross train/eval. These are **not** duplicate task IDs or evidence of split leakage by themselves. Examples include accounting identities (assets versus liabilities-and-equity) and maximum queries over different period populations. Algebraic equivalence beyond the compiler's declared canonicalization was not exhaustively searched.

### Split and context limits

Within this isolated batch, train/eval intersections are zero for source CIK groups, source collections, world-instance IDs, semantic tasks, sample IDs and exact context hashes. Source-document hashes are also disjoint across train/eval. This does not establish general content-level deduplication or absence of model pretraining knowledge. AMD was already present in legacy train products: **do not union this AMD evaluation split with the older training inventory**.

The two cross-split scalar coincidences illustrate the distinction: −1,842 USD
millions is a Micron FY2024 financing-cash lookup in train and an AMD
FY2021→FY2023 net-cash-change delta in eval; −525 is a Meta FY2022–FY2023 FX
aggregate in train and an AMD FY2022–FY2024 investing-cash aggregate in eval.
Their source documents, metrics, periods and task programs differ. Source-fact
ID intersections across the two splits were also explicitly checked and are zero.

The fixed context policy uses complete filings if they fit, otherwise the newest required complete filing plus earlier complete audited-statement packets, and finally all complete audited statements if still too long. The actual context lengths range from 59152 to 129233 recorded tokens; context-selection counts are `{'latest_complete_filing_with_historical_statements': 800, 'complete_filings': 424, 'complete_audited_statement_packet': 28}`. These are natural caps/subsets, not exact-band or strict-necessity certificates. The same context is reused for many questions; weighted SFT context-token totals are not new knowledge or unique-document volume.

The renderer is a constrained textual HTML projection, not a general browser computed-style engine; stylesheet-dependent visibility remains a stated limit. The actual eight-family/source checks do not replace broader language paraphrase evaluation, alternative-proof search, long-context difficulty measurement, or matched training results. All samples still declare production/training-release and strict-long-dependency eligibility false.

### Bound build receipts

- `alphabet`: `6f2d296a32f038216b3ef723900d18ce494e7a2d90250174653d2e7259c63897`
- `amazon`: `4b9745075a73887d5cd0a74cadf5b351b1cddd8ad128305349738e202e143208`
- `amd`: `dc3fe7c97e725f605e9d4af9abffbe93d41bf5e95aff4e2f589af3da5cc7d190`
- `meta`: `0259e664a6a1ccd0e7441905181cb9c0cf9f2e27a64e3b4eeb3ede2eb34ad849`
- `micron`: `5b87d8b710b2cd8b7f4bbc5b9a903e7cab07c2a4f1e7a64e4e8e803bd049701a`
- `nvidia`: `581c97a7288e47ae96a7cf3b761cc75ea3e4b28b80eb056dc61ffbf183a2977e`
