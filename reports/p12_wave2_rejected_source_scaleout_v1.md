# P12 Wave 2 rejected source-scaleout diagnostics

Status recorded: 2026-08-29

This report records three real-source local-probe runs that produced useful
pipeline evidence but **zero qualified training rows**. It contains no source
payloads, credentials, or promotion authorization. The current qualified
baseline remains 35 rows from five source-bound worlds and 1,347,609 exact Qwen
context tokens.

| Diagnostic world                          | Candidate result                                                                                                        | Downstream result                                              | Qualification result                          |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- | --------------------------------------------- |
| Microsoft adjacent annual filings         | 6 distinct 32K rows; 32685 or 32701 exact tokens; 6 essential events; 1 authentic annual-filing relation; proof depth 3 | dense and strict semantic audit 6/6                            | rejected: missing exact 16K, 64K, and 128K    |
| Winston Churchill adjacent revision hunks | 4 distinct 64K rows; 65501 exact tokens each; 15 essential events; 4 authentic relations; proof depth 3                 | not run after required-band failure                            | rejected: 16K and 32K strict support overflow |
| Thomas Jefferson adjacent revision hunks  | 4 distinct 16K plus 4 distinct 64K rows; 326,588 exact tokens total; zero generic background                            | dense 8/8, strict semantic audit 8/8, historical promotion 8/8 | rejected: exact 32K absent                    |

The Microsoft answer program reads two issuer-owned annual filing identities,
validates their grounded `prior_annual_filing` relation, reads both exact XBRL
revenue facts, and computes the current-minus-prior change of 33,207,000,000.
Counterfactual, remove-one, text grounding, contiguous-window, BM25, embedding
top-k, strict executable proof, and semantic proof checks all passed for the
32K rows. The immutable `p12-sec-source-slice-1-v1` profile still rejected the
world because a single 32K program is not a long-history curriculum.

The two Wikimedia runs use three consecutive public revisions and two adjacent
`revision_of` relations. Their answers consume bounded before/after revision
hunks rather than repeated full article bodies. Churchill's real hunk support
does not fit the lower bands, while Jefferson's intermediate 32K program also
overflows strict support. Neither failure may be repaired by deleting necessary
evidence, copying prose, or adding generic background.

Jefferson exposed a historical profile gap: `p7-wiki-source-slice-1-v1` could
issue a green one-world receipt without requiring every exact band. That receipt
is diagnostic-only and is not accepted for Wave 2. New immutable profiles now
enforce per-world exact 16K/32K/64K coverage:

- `p12-wiki-source-slice-1-v1`, digest
  `f2150f81e1b9bc77bc23729123afdce75fdc8fcb875fa7f4ab2f944a99279fd0`
- `p12-current-source-probe-12-v2`, digest
  `1f02a750ea7e0ede1c51d3ab8cfd6ae590aff359e322849fdf065e2819c314c9`

Selection, train-ready reporting, and the final quality gate now all reject a
world that lacks 32K. Historical profiles and their digests remain unchanged.

Tracked replay-request/configuration records and their SHA-256 digests are:

| File                                                         | SHA-256                                                            |
| ------------------------------------------------------------ | ------------------------------------------------------------------ |
| `configs/p12_wave2_company_microsoft_annual_run_v1.json`     | `2e9ce0dea87d93a5110cc290644b96a8ae9be3d217c248dee591b1862d4172e4` |
| `configs/p12_wave2_company_microsoft_annual_v1.yaml`         | `3c897b10d0ecff2f1b45455ad6957afc63c09f5038e79eed15b3a86fe8ae8740` |
| `configs/p12_wave2_wikimedia_hunk_churchill_request_v1.json` | `2f1f23e30b300755eb12ee2e8bd17e3cf64c0ad8871f3ac2fb55b4550c9d874e` |
| `configs/p12_wave2_wikimedia_hunk_churchill_v1.yaml`         | `c66ed9db8fe0d796adf29910a9b31f0d44e7d05eafa46409a73fce1976f4d79b` |
| `configs/p12_wave2_wikimedia_hunk_jefferson_request_v1.json` | `f1be025103013e1d2093c166222fb6b0db16c487496785e342171ea4f1f2c77f` |
| `configs/p12_wave2_wikimedia_hunk_jefferson_v1.yaml`         | `a5618b722260f53c5a462074c344ccafc5d3289108eb062c419dca45cc043675` |

All three records have `qualified_rows=0` and `production_eligible=false`.
They do not change the five-world inventory, do not authorize a 12-world
target receipt, and must not be uploaded to Hugging Face. A new HF package
remains blocked until a complete current-profile union reaches `COMMITTED`
under the independent production trust chain.
