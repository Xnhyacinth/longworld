# Findings (data, not instructions)

## IETF lower-band conversion and semantic growth — 2026-09-04

Adding signed schema identity fixed the P38 candidate-union blocker, but a
64K-only real-source product fails `real_64k_missing_lower_band`. Natural 32K
and first 16K constructions then failed raw 16K and raw 8K shortcut proofs.
Prioritizing authentic RFC 9700 support produced a shortcut-resistant 16K/64K
candidate with 6/6 dense audits and near-dup 0.0/0.0326. Selection still
correctly rejects it: the six-field proof is identical at both bands, so all
semantic-growth measures remain flat. Do not repack P33 again.

P40's five additional official RFCs retain 76008 tokens after exact and 0.8
word-5gram filtering, 11986 above the required delta. The proposed longer
state adds five validation fields and grows relations 10→20, essentials 7→12,
supports 6→11, and depth 2→3. This is capacity/design evidence, not a
candidate or training row.

## P17 train-ready conversion — 2026-09-03

Microsoft Finance promoted independently under its matching 20260829-v1
probe: 12 rows, exact tokens 722914, gate ok, near-dup 0.0, B5 730267 est.
Transformers failure recovery promoted independently under v2 probe: 6 rows,
exact tokens 586101, gate ok, near-dup 0.0046, B5 588669 est. Its eight
tag/PR cycles contain 554 unique source bodies and 158545 unique Qwen tokens.
Both are diagnostic local-probe products; neither trust was mixed or re-signed.

Current immutable-product inventory is 132 train / 18 eval rows and 7274169
exact context tokens. Train buckets are 36×16K, 36×32K, 45×64K, 15×128K.
The two P17 products add entity/source diversity but reuse existing Finance and
failure-recovery operators; they are volume, not two new semantic topologies.

## Macro BEA vintage 128K — 2026-09-03

Existing as-of cell-revision program on the official GDP/GDI workbook.
Exact tokens 16102/32102/64102/128251. Projected 12/12; dense audit 12/12;
near-dup 0.0; 311 unique 128K docs. Unique same-series pool 819178 tokens.
Promoted as independent local-probe extension
`p16-macro-bea-128k-extension-probe-1-v1-promoted-v1`: 12/12 train-ready,
B5 12 rows / 725610 est tokens, gate ok. Not a KEV/uv relabel. Finance not
mixed (probe `20260829-v1` vs v2).

## Pulumi failure-recovery — 2026-09-03

Authentic `failure_recovery_release_trace` on pulumi/pulumi (8 unique tags).
Audit 7/7: 64K three-view exact; 16/32 full+cf; 16/32 ordered short;
128K 114833 below band. Next unused cycles overflow ~170k. DuckDB
same-tag 8-PR hit duplicate source bodies. Not a uv clone. near-dup 0.0033.

## Microsoft Finance 128K — 2026-09-03

Four-filing asset trajectory filled exact 128K from leftover unique XBRL
tables (110 source records, 45 essentials, proof depth 16). History 128217;
views 128584/128587/128584. Dense audit 12/12. near-dup 0.0. Still
`train_ready=false`. No Amazon clone, no EDGAR fetch, no reconstruction.
Config/reports `5cdb45e`.

## IETF RFC9421 unique-delta — 2026-09-03

Unique Qwen tokens after collapsing near-identical drafts are 72,113,
below exact 128K. RFC 9421 is RFC-editor reflow of draft-19 (word ratio
0.959), not a new controlling requirement. No `updates`/`obsoletes`.
Revision edges are MUST/MAY wording swaps. No sidecar or generate.
Commit `176e70d`.

## CodeForge world 2 — 2026-09-02

Not 9/9. Best is oxc v12 at 6/9 (`7873977`): 16k version_selection now
has all three views; 64k RST holds; near-dup 0.1213. 32k RST still fails
`derived_view_gate` because a 16k window covers both `#25403` and
`#26144` mappings. A fatter 0.147.0 would split that window but overflows
exact-32k. Leftover gap PRs do not sit between the two cores after
time-sort. ruff v3 is 3/9 (64k RST only). dprint stays the only countable
CodeForge world. Do not weaken `derived_view_gate`. Do not count v12.

## Company scout — 2026-09-02

Reconstruction is dead for ordered 16/32: best spans 4239<8000 and
11812<16000. 16k gold is same-year Item 8 corridors that share a timestamp;
later annuals pack after the last essential, so extra years cannot grow
distance. Cashflow is 64k-only and must not be mixed in.

`sec_annual_revenue_change` on four-annual FY2022–FY2025 did emit 16/32/64
queries (FY2021 has no unique `total_revenue`). Generate still 0 rows:
16k ordered 6561<8000 and wrap 15088; 32k/64k `duplicate_text_clone` on
YoY operations tables. Yaml not committed. Company remains 0/2.

YAML-only 9/9 is not available on disk. Next path is a new staged
narrative program on one attested 10-K with timestamp-offset unique
sections, not another reconstruction or revenue-change generate.

## ResearchLab Megatron — 2026-09-02

arXiv 1909.08053 is a new paper, not an MLRC SHA replay. After probe
re-sign, three-view generate still cannot 9/9: the revision adapter only
emits 64k for this layout, and unique Qwen tokens after v1=v2 collapse are
~50198, below the exact 64k band. Do not pad. Do not repeat revisions to
fake length (near-dup). ResearchLab remains 0/2. Next paper needs a
16/32/64-emitting layout and unique tokens that actually fill 64k.

## CPT oxc first-1000 — 2026-09-02

First-1000 oxc commits fail `git_truncation_quality` at 207000 ppm (207/1000)
against the 200000 cap. Materialize aborts before pack. skip-8000/250 was
188000 ppm (under cap) but packed 0 rows (`short_tail`,
`source_elapsed_below_minimum`). Do not raise the ppm cap. Do not retry
either oxc window. Next CPT candidate is a different allowlisted repo
(ruff/uv) whose slice can both pack and stay under 200000 ppm.

## Cyber cross-CVE v2 — 2026-09-02

v1 16k/2-CVE packs failed dense audit: dossier-spread bookends two giant NVDs
with a thin middle, so a 4k intersecting window retrieved gold
(`2159:6255`). The 4k gate was not weakened. Band selection now skips those
masks; 16k grew to 3 CVE units. Ordered-view chronology keys were aligned to
`date|cve_id|artifact_id` (promotion), not record kind.

v2 dense audit is complete at
`data/releases/p13-cyber-cross-vendor-task-views-v2-audit/`
(`projection_candidates_sha256=068a10da…`, `audits_sha256=d64aabeb…`).
9/9 cells; near-dup 0.0 / 0.0 / 0.0009; exact 16/32/64k; source-token v2
valid; `global_proof_green=true`; `train_ready=false`; not selected.

This is a new physical Cyber world, distinct from CISA KEV. Quota Cyber
becomes 2/2. Canonical P13 SFT inventory is now **8 physical / 6
quota-countable**, 72 rows, 2,710,819 Qwen tokens. No promotion, B-export,
or HF.

Do not `uv sync` the project venvs. Ranking/materialize uses
`/tmp/p13-st-venv` (`sentence-transformers==6.0.0`). A prior
`uv sync --extra synthesis` raced and broke `longworld/.venv`.

## Retry wave — 2026-09-02 later

Code landed on `worlds` (`83192ce` adapter, `302192b` oxc license-binding,
`0d8bac5` Company configs). Parallel retries:

- MLRC generate-time 9-cell, near-dup 0.015, SHA identical to prior
  rerun3. Previous dense audit of that SHA still has CF 32k 0.347 and
  CF 64k 0.317, so it is **not** a countable ResearchLab world.
- AEVB still missing 16K (14980) and near-dup 0.4586.
- Microsoft five-seed v2: 53 rows / 5 worlds; every world still lacks
  16k and 32k `ordered_artifact_view`. Not 9/9.
- CodeForge oxc v8: 5/9 cells (16k full/cf + 64k three-view); 32k empty.
- Oxc CPT skip-8000/250: truncation 188k ppm OK, packed 0 rows.
- Apple single 10-K: 0 rows. EDGAR four-annual fetch HTTP 403.
- Cyber 9-cell views still unranked (MiniLM extra install incomplete).

## Product split — 2026-09-02

LongWorld has two trainable products. They are not interchangeable.

1. **WorldLong-SFT**: question + answer + CF twin + ordered view. Current P13
   content-gated SFT is 8 worlds / 72 rows / 2,710,819 tokens; quota-countable
   6/12; `train_ready=false`.
2. **WorldLong-CPT**: chronological git-history documents, no QA/CF program.
   Canonical closure is 3,047 rows / 288,004,845 tokens; `train_ready=false`.
3. **Diagnostic / incomplete**: Microsoft five-annual reconstruction (64K now
   in-band, 16k/32k ordered missing), Microsoft cashflow 64K (different
   program), AEVB/MLRC/Oxc CodeForge partial cells. Not canonical SFT worlds.

HF private payload remains the P6 542-row local-engineering SFT package.

This session rematerialized Cyber cross-vendor v2 (9/9 dense audit,
`068a10da…` / `d64aabeb…`). The older v1 pipeline `792c64d7…` / views
`43aa199b…` failed the 4k intersecting-artifact gate and is not the
canonical pack. Oxc CPT license-binding is in the allowlist; a 1000-commit
slice failed `Git truncation ratio exceeds configured maximum`. NVIDIA IR
remains blocked by a challenge page.

Unused allowlisted git caches for CPT: uv (10,276 commits, dual LICENSE),
ruff (16,982), oxc (20,128), plus dprint/deno/wasmtime/pulumi.

## P12 current publication truth (2026-08-29)

- The current strict content-gated baseline is 29 rows across four source-bound
  worlds with 1,120,639 receipt-reported exact Qwen context tokens.
- The 12-world target was not evaluated; production/KMS-qualified P12 rows are
  zero. The neutral inventory is integrity evidence, not an HF release package.
- The provisional 47-row/five-world inventory is invalidated because it included
  Jefferson and Newton semantic false positives. It must not be committed,
  uploaded, or used in current totals.
- The private HF repository remains the immutable 542-row P6 local-engineering
  package. Its remote hashes are synchronized; no P12 payload is currently
  eligible for upload.

## P8 readable-source and publication truth (2026-08-27)

- Canonical source params were still not enough to protect a long-workflow proof:
  a valid SEC section could be swapped between event/artifact identities, or its
  time and causal edges could be jointly rewritten. Current replay therefore
  binds the full event envelope and artifact identity/time, not only text/facts.
- Artifact attestation revision v2 binds time. Missing-revision legacy payloads
  are rejected by default in candidate/production code; `allow_legacy=True` is
  reserved for an outer path that has already bound the immutable P6 release,
  profile, and digest.
- Exact raw coordinates alone were insufficient: if an attacker changed the raw
  coordinates and raw quote together, the old replay only proved that the new
  quote existed at the self-declared range. Full canonical regeneration is needed
  to bind raw→visible coordinates, XBRL role/fact/value, component metadata, and
  parent provenance to the trusted filing record.
- SEC visible normalization removes markup/hidden content while preserving
  table order and reversible raw-source coordinates. Representative Apple and
  Amazon fact tables shrink from 47,597→955 and 53,392→1,156 characters.
- Once visible text drives Company state, the one-filing financial programs are
  valid computations but not valid long-context samples. Amazon's first-stage
  essential SEC text is about 360 estimated tokens, far below 16K.
- The private HF dataset remains the 542-row P6 local-engineering package at
  commit `32b5dcd`; its 19 payload files match the local committed stage. There
  is no qualified P8 payload to upload.
- Cross-domain review found the raw grounded-span gate is still SEC-specific.
  GitHub events can read hidden params, paper simulation skips review/response/
  benchmark relations, and Wikipedia answers do not yet depend on the earlier
  revision. These are correctness blockers, not diversity counters.
- Real-relation accounting must distinguish `authentic_source_text`,
  `authentic_source_api`, `verified_derived`, and `synthetic_executable`;
  semver inference and synthetic approvals cannot inflate authentic relations.

## P7 GitHub ruff first/late straddle (2026-08-27)

- `astral-sh/ruff` `#27170` → `0.16.1` (62 `ci_run`, 59k commit chars) and
  `#17804` → `0.16.4` (132 `ci_run`, 116k commit chars). CI is uv-class;
  patches are close to uv `#17455`/`#21001`.
- 16K overflows. 32K emits 6 rows (packed to 32,768). 64K wrap is 50,144
  (first) / 66,128 (late) at pack 52,500 and 51,420. Those wraps do not
  move with the cap: they are natural lengths. First cannot grow; late
  cannot shrink; they straddle `[64000, 65536]`.
- uv lands because leftover fills to the pack cap, so first/late wraps
  converge (~64,758 / 64,848). ruff's leftover queue is empty relative to
  the cap, so layout (`buffer` before vs after) dominates.
- `#26460` (13 commits) is thinner than `#17804` (70k vs 116k commit
  chars). `#27766` head CI includes `cancelled`.

## P7 GitHub deno CI-matrix wrap (2026-08-26)

- `#33946` has 1,337 `ci_run` records. 16K overflows. 32K of v2.9.0 fits.
  64K wrap 59,359 at pack 46,648 and 72,894 at pack 50,900: one leftover
  artifact jumps the window.
- `#34726` (401 `ci_run`) still overflows 16K. 64K required wrap is 70,395
  at pack 52,500 and 70,395 at 48,301. The proof itself is over the
  window. Deno's CI matrix is the wrap, not patch length.

## P7 SEC Amazon one-world proof (2026-08-26)

- Local attested filing `0001018724-25-000004` (AMAZON COM INC, FY2024,
  filed 2025-02-07). Import is `explicit_filings`; this host cannot reach
  sec.gov. Issuer spec is keyed on CIK `0001018724`: Note 2 is Financial
  Instruments, disaggregation/geo is Note 10, Note 13 end is unique iXBRL
  `id="f-1388"`. Geo axis `srt:StatementGeographicalAxis`. No tagged
  `us-gaap:Liabilities`; 64K gold uses reconstructed L = A − E.
- Mix Product 272311 + Service 365648 = 637959; seven category leaves plus
  five geos. SOX 906 names from EX-32.1 and EX-32.2 (Jassy / Olsavsky).
  Optional component type `EX-32.2` is extracted if present; Apple has none
  so still four components. Cert regex allows a title before `certify`.
- Note 13 `f-1387` wrapped 63,930 (70 short); `f-1388` wrapped 64,174.
  Packed est 14,298 / 28,950 / 49,471 at pack 49,600. 18/18 promoted; gate
  v5 green. Candidate `a21fcdd1…`; promoted `c2001e06…`. Company 2/4.

## P7 Wikipedia MLK leftover vs RFC fill (2026-08-26)

- `appendix_rest` was in the 64K pool. Wrap ~59,775 at pack 60,000 was
  mixed-density cap-fill from unbound RFC `.full` files that
  `bind_source_packs` injects onto every focal world. Dropping
  `doc_type == "source_pack"` from `source_workflow_artifacts_for_query`
  plus leftover rest_end `==== ''The Measure of a Man'' ====` (~6,035 est)
  at pack 54,000 wraps 64,504. Packed est 14,669 / 32,670 / 53,151.
- 18/18 promoted; gate v5 green. Extra beyond ResearchLab 4/4. Candidate
  `5bf3678f…`; promoted `e077f35e…`. Do not restore Jane Elliott leftover.

## P7 Wikipedia Elizabeth leftover retune (2026-08-26)

- Essentials-only wrap was 62,311. Full References leftover (~4,427 est) at
  pack 51,200 wrapped 66,981: one artifact, all-or-nothing. Cutting leftover
  at unique `==External links==` (~2,033 est) lets pack 50,000 include it.
  Wrap 64,847 / 64,846. 18/18 promoted; gate v5 green. Candidate
  `197fb8c6…`; promoted `88456e79…`. Gold `BORN:1926-04-21` /
  `COMM:Jallianwala Bagh massacre` / `ENTITY:Q9682` / `POP:death of Diana`.
- A leftover just under remaining pack budget is the unlock, not padding and
  not pack retune alone when the next artifact overshoots the 1,536-token
  exact-64K window.

## P7 GitHub uv one-world proof (2026-08-26)

- Two signed `astral-sh/uv` episodes (`#17455` → `0.12.5`, `#21001` →
  `0.12.6`) fill exact-64K from inlined commit patches, not changelog length.
  Skipped CI conclusions no longer force `blocked` / empty version when a
  gate passed. Gold is `commit -> tag`; CF is `BLOCKED-tag`.
- 16K (0.12.5) already contains the prior proof (depth 5, support 67). A 32K
  sibling of that same proof has zero causal/support/depth/proof-token growth
  and fails the P7 gate. The kept pair is 16K (0.12.5) plus 64K (0.12.6,
  depth 6, support 149). Wrap 64,758 / 64,848 at pack 52,500.
- This is CodeForge unique exact-64K world 2/4. Do not subset P6's 80-bundle.

## P7 Wikipedia Obama one-world proof (2026-08-26)

- Later Obama revision `page-534366-r1371416342` plus Wikidata `Q76`. Unique
  birth `{{birth date and age|1961|8|4}}` → `1961-08-04`; mid quote
  `Madelyn Payne Dunham`; late quote `Obama Chooses Biden`. Headings have no
  spaces (`==Early life and career==`). Leftover starts at H4
  `====Environmental policy====` and ends at `[[The Hill (newspaper)|The Hill]]`.
- Obama Qwen/est is ~1.31 (denser than Newton ~1.20). Pack 54,000 wrapped
  69,290; 50,200 wrapped 65,901; 49,300 wrapped 64,976 / 64,965. Leftover
  shrink does not lower wrap while the packer fills the cap.
- 18/18 promoted; gate v5 green. Extra beyond ResearchLab 4/4.

## P7 Wikipedia Newton one-world proof (2026-08-26)

- Later Newton revision `page-14627-r1371274988` plus Wikidata `Q935` drive
  tagged BORN/COMM/ENTITY/POP answers. The birth template
  `{{Birth date|df=y|1643|01|04}}` occurs twice in the early body (infobox
  and Old-Style prose). Evidence is the unique infobox wrapper
  `= {{OldStyleDateNY|{{Birth date|df=y|1643|01|04}}` → `1643-01-04`; mid
  quote `Hypothesis of Light`; late quote `William Chaloner`; entity Q935.
  CF birth year +1 → `1644-01-04`.
- Full later revision is ~56.2k estimated tokens. 64K proof sections stop
  before unique `== References ==` (~49.3k essentials). An authentic
  non-essential References prefix ending at
  `=== Alchemy further reading ===` wraps 64,959.
- Candidate generation emitted 18 rows (6/6/6 at 16K/32K/64K) with zero
  rejects. Packed estimated tokens 14,288 / 28,223 / 54,083 (pack target
  54,000). Exact 64K wrap is 64,959.
- Promoted proof-bearing growth is 12,680 tokens (16K→32K; minimum 696)
  and 23,985 tokens (32K→64K; minimum 1,293). Authentic source relations
  stay 0; hybrid causal signatures are 3 with edge counts 3/5/10.
- Candidate row-set `50202f59…`; promoted row-set `5f24fa4f…`. This slice
  is not merged into P6 v4 and is not a 12-world product.
- Combined with Churchill/Einstein/Thatcher, ResearchLab now has four
  unique exact-64K source workflows. That would fill the P7.5 researchlab
  world quota; it does not fill Company or CodeForge.

## P7.5 Company exact-64K blocker (2026-08-26)

- This host cannot reach EDGAR. Both `data.sec.gov` submissions JSON and
  `www.sec.gov` Archives/browse/company_tickers return HTTP 403 Akamai
  “undeclared automated tool”. Do not retry from this IP.
- Copying Apple into 12 worlds is forbidden. A second 10-K needs (1) an
  attested Archives `.txt` copied from a network that has declared SEC
  fair-access User-Agent traffic, imported with `explicit_filings` so
  fetch never HTTP, and (2) a CIK-keyed heading/fact program. Apple
  `0000320193` remains the only programmed issuer; unknown CIKs fail
  closed instead of reusing Note 2 / Note 13 / ProductMember cuts.
- Allowlisted CIKs still waiting on filings: Microsoft `0000789019`,
  Amazon `0001018724`, NVIDIA `0001045810`, Alphabet `0001652044`.

## P7.5 CodeForge exact-64K blocker (2026-08-26)

- P6 bound all 80 signed GitHub episodes into one world. A v2.34 subset
  or leftover unused PRs from that bundle is not a new unique workflow.
- 64K packing is commit-patch text plus CI stubs, not changelog length.
  urllib3, aiohttp, pytest, and fastapi release-cited PRs scouted here
  have tens-to-hundreds of added lines — Ada-class, not wrap-64K.
  `psf_requests_pr7272_v2340` (615 records, ~666KiB) remains the only
  individually long-enough episode and is already in P6.
- Unlock: allowlist a new MIT/Apache-2.0 public repo whose two later
  releases cite merged PRs with large inline patches (or many commits
  under `MAX_INLINE_PATCH_FILES`), export disjoint episodes, and run a
  one-world slice. Do not point a P7 slice at the full 80-bundle.

## P7 Wikipedia Thatcher one-world proof (2026-08-26)

- Later Thatcher revision `page-19831-r1370984131` plus Wikidata `Q7416`
  drive tagged BORN/COMM/ENTITY/POP answers. Birth
  `{{Birth date|df=y|1925|10|13}}` → `1925-10-13`; mid quote
  `The lady's not for turning`; late quote `Westland affair`; entity Q7416.
  CF birth year +1 → `1926-10-13`.
- Full later revision is ~68.4k estimated tokens. 64K proof sections stop
  before unique unspaced `==Legacy==` (~46.0k essentials). Qwen/est on this
  page is ~1.24. A Legacy-through-References leftover (~12k) wraps ~72k and
  overshoots; `[[Scottish Widows]]` leftover wraps 66,303; `====Reputation====`
  leftover wraps 65,553 (17 over). Unique `[[Scottish independence]]`
  leftover (~4,530 est) wraps 65,417.
- Candidate generation emitted 18 rows (6/6/6 at 16K/32K/64K) with zero
  rejects. Packed estimated tokens 13,972 / 28,727 / 53,426 (pack target
  54,000). Exact 64K wrap is 65,417.
- Promoted proof-bearing growth is 13,500 tokens (16K→32K; minimum 737)
  and 20,146 tokens (32K→64K; minimum 1,234). Authentic source relations
  stay 0; hybrid causal signatures are 3 with edge counts 3/5/10.
- Candidate row-set `4bf6b695…`; promoted row-set `b3916422…`. This slice
  is not merged into P6 v4 and is not a 12-world product.
- Leftover ground no longer requires a spaced `== References ==` heading.
  Unspaced `==References==` or the first leftover H2 (`==Legacy==`) is
  accepted so bibliography-style leftovers are not the only wrap filler.

## P7 Isaac Newton inventory (2026-08-26)

- Fetched `wikimedia_p7_newton_v1`: later revision `14627-r1371274988`,
  Q935, ~56.2k estimated, References at ~49.3k. Later programmed: unique
  infobox wrapper around the duplicated birth template. See the Newton
  one-world proof above.

## P7 Wikipedia Einstein one-world proof (2026-08-26)

- Later Einstein revision `page-736-r1370002284` plus Wikidata `Q937`
  drive tagged BORN/COMM/ENTITY/POP answers. Birth
  `{{Birth date|df=yes|1879|3|14}}` → `1879-03-14`; mid quote
  `Russell–Einstein Manifesto`; late quote `Einstein–Podolsky–Rosen paradox`;
  entity Q937. CF birth year +1 → `1880-03-14`.
- Full later revision is ~59.5k estimated tokens and overflows a 51,200 pack
  target. 64K proof sections stop before unique `== References ==` (~42.3k
  essentials). Qwen/est on this page is ~1.14 on body vs Churchill ~1.28, so
  pack 51,200 wrapped only 51,167. An authentic non-essential References
  prefix (`appendix_rest`, unique end `<ref name="ILjYQ">`) is preferred in
  packing so generic source_packs do not consume the leftover budget first.
- Candidate generation emitted 18 rows (6/6/6 at 16K/32K/64K) with zero
  rejects. Packed estimated tokens 15,946 / 32,823 / 54,055 (pack target
  54,000). Exact 64K wrap is 64,982.
- Promoted proof-bearing growth is 16,866 tokens (16K→32K; minimum 843)
  and 21,249 tokens (32K→64K; minimum 1,061). Authentic source relations
  stay 0; hybrid causal signatures are 3 with edge counts 3/5/10.
- Candidate row-set `eee8896c…`; promoted row-set `0c04fa82…`. This slice
  is not merged into P6 v4 and is not a 12-world product.

## P7 paper extractor generalization (2026-08-26)

- The funding regex `funded by … (AGENCY), NSF DIGITS.` is too narrow for a
  second paper. Attention `1706.03762` v1→v2 has unique semantic LaTeX and
  no funding-regex hit. `format_revision_added_delta` prefers funding when
  present and otherwise keeps a unique semantic sentence that contains a
  digit for CF. MLRC Aviva/ONR/NSF 172251 is unchanged.
- Signed Attention inventory binds
  `Based on the similarity of these formulae, the two-layer feed-forward
network can be seen as a kind of attention...`. Body ~21k estimated
  tokens: not exact-64K. Do not invent funding facts. Do not retry OpenReview 403.

## P7 CodeForge leftover episodes (2026-08-26)

- `public_repo_episodes_v2.json` has 80 attested episodes; P6 v4 seed 3
  already bound all 80 into one world. Only `psf_requests_pr7272_v2340`
  is individually long enough for exact-64K and it is already in P6's 64K
  proof. The only unused two-cycle pair (`v2.33.0`+`v2.33.1`) is ~35k
  estimated and cannot wrap 64,000. Four leftover exact-64K CodeForge
  worlds are not available from this bundle.

## P7 Wikipedia Churchill one-world proof (2026-08-26)

- Later Churchill revision `page-33265-r1367982973` plus Wikidata `Q8016`
  drive tagged BORN/COMM/ENTITY/POP answers. Birth
  `{{birth date|1874|11|30|df=y}}` → `1874-11-30`; mid quote `We shall
fight on the beaches`; late quote `On the 8th, Churchill declared war
on Japan`; entity Q8016. CF birth year +1 → `1875-11-30`.
- Candidate generation emitted 18 rows (6/6/6 at 16K/32K/64K) with zero
  rejects. Packed estimated tokens 14,757 / 28,006 / 51,141 (pack target
  51,200 after 52,200 overshot Qwen wrap to 66,111). Exact 64K wrap is
  65,073 (first) and 65,131 (late).
- Promoted proof-bearing growth is 13,182 tokens (16K→32K; minimum 662)
  and 23,187 tokens (32K→64K; minimum 1,156). Authentic source relations
  stay 0; hybrid causal signatures are 3 with edge counts 3/5/10.
  `generate.py` must union `WIKI_HYBRID_CHILD_EVENT_TYPES` when writing
  `source_relation_edges`; SEC-only counting left Wikipedia candidates
  with empty edges and select failed (`0<1` real worlds).
- 16K essentials are early_work + compute + two copy rungs (BM25 top-3
  cannot cover). 32K/64K parent the prior compute, not the copies.
  Depth/essentials d4|n4, d5|n5, d6|n7. 64K leftover pack-to-cap is
  ~2.9% non-real-source tokens; those extras are not the proof.
- Ada later revision (~24k estimated) yields honest 16K/32K only. Turing
  later revision (~43k) wraps at 57,125, below 64,000. Do not pad either
  with pulses, leftover lab docs, or a near-duplicate earlier revision.
- Candidate row-set `a31b7d1a…`; promoted row-set `577644a0…`. This slice
  is not merged into P6 v4 and is not a 12-world product.

## P7 SEC v8 one-world proof (2026-08-25)

- iXBRL leaf facts from the Apple FY2025 10-K now enter replayed state and
  determine staged MIX/CAT/GEO/BS/CERT answers. Product+service = total
  `416,161,000,000`; category and geographic mixes also sum to that total;
  L+E=A=LSE on the balance sheet; EX-31.1/31.2/32.1 names are Cook, Parekh,
  and both.
- Candidate generation emitted 18 rows (6/6/6 at 16K/32K/64K) with zero
  rejects. First-tier natural length is 12,698 estimated tokens (honest 16K),
  not the v6 10.2K/8K underfill. Exact 64K wrap is 64,854 Qwen tokens.
- Promoted proof-bearing growth is 16,997 tokens (16K→32K; minimum 1,004) and
  19,744 tokens (32K→64K; minimum 840). Authentic source relations stay 0;
  hybrid causal signatures are 3. Gate v5 is green.
- 16K essentials are operations + compute + two copy rungs (BM25 top-3 cannot
  cover). 32K/64K keep the 16K compute but drop the copies. 32K leftover
  pack-to-cap still includes Company-cycle documents (~10.7% non-real-source);
  those extras are not the proof.
- The pinned Qwen3.5-4B tokenizer snapshot is now available from
  `HF_HOME=/workspace/wynckeliao/.hf`; its freshly resolved asset manifest
  matches the release-profile digest. `/root` is no longer required for exact
  tokenizer replay.
- Candidate row-set `8579b9f8…`; promoted row-set `0fb120ae…`. This slice is
  not merged into P6 v4 and is not a 12-world product.

## P5 implementation and live probes (2026-08-25)

- Live SEC smoke downloaded Apple accession `0000320193-25-000079`: one
  9,392,337-byte complete submission and four exact header facts. The signed
  manifest reloads as `source_inventory`, `hybrid_train_ready=false`, and
  `generation_integration=disabled`; financial/XBRL facts do not yet affect an
  answer program.
- Live Wikimedia smoke downloaded two Ada Lovelace page revisions plus one
  Wikidata entity revision. Three exact-span relations (`revision_of`,
  `page_describes_entity`, `entity_resolves_page`) passed signed-manifest reload.
- Paper revision/review/benchmark support is still a signed fixture/input
  contract, not a live OpenReview/arXiv export and not state/answer integration.
- The new executable query types are `legal_financial_release_trace`,
  `benchmark_revision_conflict`, and `failure_recovery_release_trace`. Each
  passed factual replay, actual CF replay, semantic verification, and every
  essential remove-one check for seeds 1, 37, and 91.
- A current-code audit of the old 48-world candidate eval pool is not fully
  reusable: 4 workers processed 664 candidates in 158.032 seconds, accepted
  564 (84.94%), and rejected 100 rows from two ResearchLab worlds because dense
  top-k already solved them. The prior promoted artifact must not be treated as
  freshly validated after program/code changes.
- Leakage-safe unseen v2 results: world/entity has 48 components and is ready;
  topology/operator has 9 connected components and is ready; verified source
  family has one component and is blocked; domain composition has no actual
  multi-domain groups and is blocked.
- External benchmark execution now requires pinned official source, pinned
  runner origin+commit, clean checkout, fresh outputs, minimal environment, and
  non-symlink regular result files. No RULER/LongBench-v2/MRCR/GraphWalks/HELMET
  score was executed in this slice.
- Production approval now embeds the signed envelope and downstream 210-world
  selection reverifies ECDSA against the current pinned roots. The local
  process still cannot establish that the trust-root pin is protected from the
  same operator; actual production approval remains blocked on protected CI/KMS
  configuration and an independent signature.

## P5 official external-eval interfaces (2026-08-25)

- NVIDIA's official RULER repository now directs users to the `rulerv1-ns` or
  `rulerv2-ns` branches; its historical entry point is `bash run.sh MODEL_NAME
synthetic`. A LongWorld runner must pin a repository revision/branch instead
  of silently assuming the deprecated main-branch commands.
- THUDM's official LongBench repository contains LongBench v2 (503 multiple
  choice tasks, roughly 8K to 2M words) with `pred.py` and `result.py`. It is a
  separate external evaluation and must never be ingested into LongWorld
  training rows.
- Princeton's official HELMET runner uses `python eval.py --config
configs/<task>.yaml --model_name_or_path ... --output_dir ...`; the full suite
  spans recall, RAG, reranking, citation, LongQA, summarization and ICL and has
  substantial data/model requirements.
- OpenAI publishes official `openai/mrcr` and `openai/graphwalks` datasets on
  Hugging Face. GraphWalks currently has <=128K and 256K-to-1M parquet tiers;
  MRCR has 2/4/8-needle variants. The repository currently has no single
  official local-model runner shared with RULER/HELMET, so LongWorld should pin
  dataset revisions and record the selected evaluation adapter rather than
  inventing an "official" command.
- Consequently P5 external evaluation should produce fail-closed execution
  receipts (repo/dataset revision, command, model digest, output digest, exit
  status) and distinguish `planned`, `smoke_passed`, and `completed`; cache
  presence alone is not a benchmark result.

## P3 live probe (2026-08-24)

## P4 multi-domain baseline (2026-08-24)

- All three existing simulators materialize consistently before P4 changes.
  Seed 1 with two parallel worlds yields: Company 18 events / 36 artifacts /
  12 queries; ResearchLab 12 / 30 / 10; CodeForge with 12 workstreams 77 / 95 / 14. Company and ResearchLab already expose multiple motifs and depth-2–4
  proofs, so the first gap is strict P3 compilation/retention rather than an
  absent simulator.
- Raw artifact-pool token totals are large (~455K per domain), but that is not
  evidence of a valid long sample. P4 must measure each emitted view after
  packing and require event-bearing dependency growth; unbound/background text
  cannot justify 64K retention.
- P4 is intentionally a three-domain local probe. The existing
  `p3-production-48-v1` profile remains production-only and cannot be reused
  under probe HMAC keys merely to increase row count.

- The final 12-world release retains **28** hybrid real-workflow rows. Version
  selection has a same-group 32K lower band (v2.34.0, 166 strict support events,
  ordered evidence span ~22.4K) and six exact 64K rows for v2.34.1 (230 events,
  ~65.4K span). Cross-repository dependency is honest 32K rather than padded
  64K. CI regression retains four full/CF 16K rows with an authentic
  failure→recovery corridor. Byte-identical or too-short ordered views are
  omitted, not copied.

- GitHub CLI is authenticated as `Xnhyacinth`; public repository, PR,
  check-run, Actions, release and license endpoints are readable. REST core
  quota was 5000/5000 at the preflight check.
- Current observed probe pins are `/usr/bin/gh`
  `2fd925d68889746976958342fb749bf102bc7dc8bcba3abfa533a80ad7791673`
  and `configs/public_repo_allowlist.yaml`
  `9d4fc8fbc7a934513692033e8a9d97469a4dba1f39ba1e15fe65d65b9068bb67`.
  They are fixed for the explicitly labelled local probe, not an independent
  production approval.
- Truth regimes remain separate: simulated events may be
  `synthetic_executable`; only freshly fetched, scanned and signed GitHub bodies
  may be `real_public`; hybrid worlds preserve that distinction per fact.
- Live export smoke passed for `psf/requests#7012` with release `v2.33.0`
  (205 replay records, including strict release ancestry) and
  `opensearch-project/opensearch-php#419` without a release (42 records). Both
  load as scanner-v2, pinned-policy/client, role-attested `real_public` inputs.
- Full re-export passed 80/80 with zero failures: 2,232 current records versus
  1,711 legacy records, 17 release workflows and 80 unique workflow IDs. The
  signed v2 bundle is 13,014 bytes and every loaded source origin is
  `real_public`. Source diversity is still only two repos (79+1) and four
  release tags; this is enough for mechanism validation, not source-family
  generalization.

## P2.12 train 128k/256k + full source utilization (2026-08-20)

`data/v2_p28` / `reports/v2_p28_quality.json` / `reports/causalcore_v2_long/FREEZE.md`. pytest **55**. quality_gate ok.

- Train `length_buckets` now include 128k/256k. Leftover packs: all on-disk files, **longest first**, cap 900k chars. `rfc9110`/`rfc9112` no longer dropped. qwen 24k→**156k**, deepseek 24k→**140k** via ar5iv. Public ABNF is exempt from workspace KV-leak scan.
- vs `v2_p27`: rows 7032→**9840**; by_length **1968 each** for 16/32/64/**128/256k**; train 128k/256k **1404** rows each (was 0). N_eff **50.47** (was 50.58). unique **134**. deep 256→**340** (same programs, more length slots). RFC overall 0.450→**0.545** because long buckets are public-source span.
- Train-full RFC / evidence distance: 16k **0.43 / 15.5k**, 32k **0.31 / 31.5k**, 64k **0.42 / 62.6k**, 128k **0.71 / 126k**, 256k **0.85 / 252k**. Native majority at train 16–64k; 128/256k unique RFC/papers. pulse 0, rejects 0. `rfc9110` in **657** train-full contexts.
- B5 export `data/sft/causalcore_v2_long`: **4530** rows, 128k **840** + 256k **840**, 16k 1170; memory 0; long local_or_mixed 0; ~333M tokens. Recipe `configs/swift/B5_v2_long.yaml`.
- External ACC/LongTrace/LongMIT/DocQA remain in `data/external/` as **baselines**, not CausalCore gold.

Claim alignment: length caps are now on train with real unique sources and real evidence distance. Not hop-5. Not 48 worlds. 860/1640 full still retrieval.

## Alignment review before P2.12 (2026-08-20)

Plan axes: N_proof / process / artifact / task / style. Length is a **cap**, not a fill. Gold is replay. Public files are leftover unique mass, never unbound gold.

What is already correct on `data/v2_p27`: 3 domains; deep vs retrieval labels; source_choice pair gold; registers; pulse 0; export drops memory and long local_or_mixed. External ACC/LongTrace/LongMIT are **baseline SFT**, not CausalCore gold.

What is not correct:

1. **Length:** `_buckets_for_split` gives 128k/256k only to eval worlds (3/12). Train synthesis stops at 64k. P2.11 export then dropped 128k/256k again. The unique pool can pack those caps (eval already did: RFC share 0.71/0.85) but train never asked.
2. **Sources not used:** leftover `n_source_pack=16` walks files A–Z and stops before `rfc9110` (503k chars) and `rfc9112`. Read path then truncates at 120k chars (~30k tokens) while the comment says “cannot exceed a 256k pack.”
3. **Stub papers:** `qwen-technical-report` / `deepseek-v3` are 24k-char arXiv HTML chrome, yet they sit on the ingest prefer list.
4. **Volume:** 12-world probe is the mix pin, not a 210-world count. Do not confuse missing 128k train slots with “need 48 worlds.”

P2.12: load every on-disk pack, longest first, cap ~900k chars; put 128k/256k in train `length_buckets`; export those buckets; refetch stub papers. 12-world `data/v2_p28`. No pulse. Not 48/210.

## P2.11 CausalCore v2 freeze (2026-08-20)

`reports/causalcore_v2/FREEZE.md`. pytest **53**. Export token_spread **0.0051**.

- `view_answer(memory)` is `unanswerable`. B5/B5w no longer train calendar cards. Default export buckets include **16k**. `train_sft.py` drops `local_or_mixed` on 32k+.
- B5 freeze export (`data/sft/causalcore_v2`, swift in `data/sft/causalcore_v2_swift`): **345** rows, ~9.8M tokens; 16k **144** / 32k 93 / 64k 108; memory **0**; long-bucket local_or_mixed **0**. Views: full, minimal, cf, distractor_only, ordered_artifact_view.
- Equal-token vs B1 keeps ablations fair; 12-world B5 is smoke-scale. 48 worlds would scale those counts, not topologies.
- Generator mix unchanged (`data/v2_p27`). Raw jsonl still contains memory rows with copied length labels; SFT export is the honest product.

Claim alignment: N_task moved at the compile boundary (verified views only; native 16k kept). Not hop-5, not 48/210, not fake ACC. Next: train B5_v2 when GPUs are free, or leave 48 blocked.

## Alignment review before P2.11 (2026-08-20)

Core claim: executable twins; necessary evidence; scale topologies not n_worlds. Length is a sample attribute.

`data/v2_p27` already moved N_proof (cascade + DK-* + source_choice pair), N_artifact (native over RFC), N_style (registers). Another query type is kitchen-sink. 48 worlds scale counts. Relabeling 608 retrieval as deep is dishonest.

Weakest remaining axis is **N_task at the SFT boundary**, not the generator mix:

- `memory` context is a calendar card (~flags + event types) that tells the reader not to answer from the card. `view_answer` still emits the long gold. `length_bucket` is copied from the packed 16/32/64k slot. B5/B5w export trains this as long-context SFT. That is unverified gold and fake length.
- Export default `--train-buckets 8k,32k,64k` drops **16k**, which is the native-workspace majority of the v2 train mix. Generate prefers native at 16k; export would throw that away.
- `train_sft.py` B5 already omits `memory` but does not drop `local_or_mixed` on 32k+.

P2.11: memory gold is `unanswerable`; B5/B5w drop memory; default buckets include 16k; freeze `v2_p27` as CausalCore v2. Not hop-5 / 48/210.

## P2.10 12-world source_choice probe (2026-08-20)

`data/v2_p27` / `reports/v2_p27_quality.json`. pytest **50**. quality_gate ok.

- Query `source_choice` on existing `ingest_public` + `ingest_public_alt` + `adopt_public`. Gold is `<adopted> || <unused>`, not a copy of `public_norm`. CF `adopt_alt` flips the pair; public file texts stay put (surface_match). JOIN skip. No new events.
- vs `v2_p26`: unique_full_answers **123→134** (+11 pair strings from 11 grounded worlds). deep **178→256** (+78, all source_choice full rows, 21.8% of 1172). N_eff **47.625→50.581** from the new `public_norm_choice` topologies (3 domains), not JOIN. join **0.230→0.215**. source_choice full **78/78** pair gold, **0** overlap with source_grounded answers. retrieval **608** unchanged. pulse 0, clones 0, rejects 0.
- N_artifact held: RFC share **0.450** (was 0.445). By bucket: 16k **0.43**, 32k **0.32**, 64k **0.42**, 128k **0.71**, 256k **0.85**. Length **16/32/64k = 1968**, **128/256k = 564** (more rows because the new query packed).
- Lab/codeforge `gold_from_full` now dispatches through `domain.eval_answer`; otherwise pair gold would have collapsed to the single stem.

Claim alignment: N_proof moved because the **answer string** changed (P2.8 lesson). This is not hop-5 and not 48 worlds. 608/1172 remain 2-essential retrieval. Next: freeze this mix and use it. Another near-duplicate query would be kitchen-sink.

## Alignment review before P2.10 (2026-08-20)

Core claim: executable twins; necessary evidence; scale topologies not n_worlds.

`data/v2_p26` already moved N_style, N_proof (cascade + distinct DK-*), and N_artifact (native over RFC). Another LT/DK stamp is kitchen-sink. 48 worlds scale counts. Relabeling 2-essential retrieval is dishonest.

GroundedWorld already writes `pending_public_alt` but never asks for it. `source_grounded` gold is the adopted stem alone (2-hop). A clone that still answers with that stem would repeat P2.7.

P2.10: query `source_choice` on existing ingest+alt+adopt. Gold `public_norm || unused_pending` (`<adopted> || <unused>`). Distinct string from source_grounded. CF `adopt_alt` swaps the pair without changing public files. JOIN skip. No new events, not 48/210.

## P2.9 12-world native-over-RFC probe (2026-08-20)

`data/v2_p26` / `reports/v2_p26_quality.json`. pytest **47**. quality_gate ok.

- Packer: native workplace types score above unbound RFC; ingest-bound packs keep a boost. Unbound `source_pack` is `natural_background` unless it reveals an ingest event. RFC with empty propositions still pack as last-resort length (needed for 128/256k gold span).
- Also restored `core_as_of`: max non-extension time **strictly before rollback/invalidate**. P2.7 ratify after rollback had pulled current_state gold to the post-rollback value, against the existing test contract.
- vs `v2_p25`: proof mix **unchanged** (N_eff **47.625**, join **0.230**, deep **178**, revisitation/ratify/docket **48/48/48**, unique answers **123**, pulse 0, clones 0, rejects 0). Length buckets restored **16/32/64k = 1836**, **128/256k = 528**.
- N_artifact: mean_real_source_token_ratio **0.801 → 0.445**. By bucket: 16k **0.41**, 32k **0.32**, 64k **0.41**, 128k **0.71**, 256k **0.85**. Train lengths are majority workspace; long eval still uses unique public sources for span. That is the honest split.
- near_dup **0.17 → 0.27** (16/32/64k native register clones; 256k still 0.12). Do not read this as a style regression to P2.5's 0.39 procedure blob (still 0 blobs).
- HN scalar **0.43** barely moved: extra-world native email is real same-schema HN; remaining RFC-as-HN is mostly ingest-alt (`reveals_events` set). Unbound RFC is majority `natural_background` (5478 vs 3053 HN).
- First generate of this slice: novelty-0 skip on RFC → 348 `distance_shortfall`, no 128/256k. Fixed; this report is the second generate.

Claim alignment: N_artifact moved without a fifth cascade stamp and without n_worlds. 608/1094 remain retrieval. Next: freeze this mix and use it, or a **non-cascade** distinct gold. Not 48/210.

## Alignment review before P2.9 (2026-08-20)

Core claim: executable twins in a long-lived **workspace**; length is an outcome of unique documents, not a fill loop; scale topologies not n_worlds.

`data/v2_p25` already has 3-hop + 4-hop + distinct-gold docket. Another cascade stamp would clone that process. 48 worlds would scale counts. 608/1094 retrieval stays retrieval if relabeled.

Honest N_artifact gap: `mean_real_source_token_ratio` **0.801**. Packer `_semantic_score` gives every `source_pack` **+6** and exempts RFC from the novelty-0 skip, so unbound public files outrank native email/log. `_schema_family` then treats `w*.source.rfc*` as company, so RFC is counted as `structural_hard_negative` (HN 0.43). Phase A said filler is other worlds' native artifacts; RFC is last-resort unique length.

P2.9: native workplace types outrank unbound RFC; ingest-bound packs keep their boost (source_grounded gold). Unbound source_pack role is `natural_background`. No new events, no hop 5, not 48/210.

## P2.8 12-world distinct-gold docket probe (2026-08-20)

`data/v2_p25` / `reports/v2_p25_quality.json`. pytest **44**. quality_gate ok.

- New topology: `docket_control` (`seed+docket+ack+reopen+ratify`, depth 4, n_ess 5). Gold `controlling_docket` (`DK-*`). Docket seed prints the token once; ack/reopen/ratify do not. Latent seed still necessary (ratify no-ops without `active_latent`). `LT-*` and `LD-*` stay in the pack as competing tokens. JOIN skipped.
- vs `v2_p24`: canonical N_eff **44.885 → 47.625** (49 → 52 programs). join **0.241 → 0.230**. questions **1046 → 1094**. deep **130 → 178** (share **12.4% → 16.3%**). retrieval 608 and local 308 unchanged.
- docket_control full **48** (company 16 / lab 10 / code 22). Gold prefix `DK-` **48/48**. Gold occurs **exactly once** per full context. LD- **48/48**. `case-ratified` **48/48**. No `DK-*`/`LT-*`/`LD-*` in the question. Overlap with revisitation/ratification answers: **none**.
- unique_full_answers **117 → 123**. The +6 is six cascade worlds × one new `DK-*` each (same six worlds already contributed `LT-*` to the 117). Do not count 48 rows as 48 new facts.
- revisitation and ratification still 48/48 with the same six `LT-*` strings. pulse 0, clones 0, rejects 0, procedure blob 0. 4 registers (neff 3.68). near_dup **0.1698**.
- mean_hop_count **2.687** (was 2.627). mean_evidence_distance **61753**. Length: 16/32/64k **1836**, 128/256k **528**.

Claim alignment: N_proof moved by a **different identifier**, not a fifth copy of `LT-*` and not by n_worlds. 608/1094 are still long-range retrieval. proof_depth stays 4 (docket is a parallel early write, not hop 5). A further cascade stamp (`DK-*` or `LT-*` again) would be kitchen-sink. 48 worlds of this mix would scale counts. Next: stop process-cascade expansion; remaining value is a **non-cascade** distinct gold, N_artifact honesty (RFC share 0.80), or freeze and use the data.

## Alignment review before P2.8 (2026-08-20)

Core claim: executable twins; necessary evidence; scale topologies not n_worlds.

`data/v2_p24` already has 3-hop revisitation and 4-hop ratification, but both answers are the same `LT-*`. Unique answers 117 did not move. A fifth hop that still copies `LT-*` would be kitchen-sink cascade, not N_proof. Relabeling retrieval would be dishonest. 48 worlds would scale counts.

P2.8: independent early `seed_docket` writes `DK-*` into `pending_docket`. `ratify_latent` copies it to `controlling_docket` only if `active_latent` is set. Query `docket_control` gold is `DK-*`. Latent seed is still necessary (otherwise ratify no-ops). `LT-*` in the same pack is a competing token. JOIN skip. Not 48/210.

## P2.7 12-world 4-hop ratification probe (2026-08-20)

`data/v2_p24` / `reports/v2_p24_quality.json`. pytest **43**. quality_gate ok.

- New topology: `ratification` (`seed+ack+reopen+ratify`, depth 4, n_ess 4). Gold `controlling_latent` (`LT-*`). Ratify prints `case-ratified`, not the token. seed+ack+reopen answers revisitation and does **not** answer ratification.
- vs `v2_p23`: canonical N_eff **42.16 → 44.885** (46 → 49 programs). join **0.252 → 0.241**. questions **998 → 1046**. deep **82 → 130** (share **8.2% → 12.4%**). retrieval 608 and local 308 unchanged.
- ratification full **48** (LD- in **48/48**, `case-ratified` in **48/48**). revisitation still **48** with LD- **48/48**.
- pulse 0, clones 0, rejects 0, procedure blob 0. 4 registers remain (neff 3.68). near_dup **0.1705**.
- unique_full_answers still **117**: revisitation and ratification share the `LT-*` string; they differ by necessary evidence, not by a new identifier. Do not count this as 48 new facts.
- mean_hop_count **2.627** (was 2.561). mean_evidence_distance **61125**. Length (all views): 16/32/64k **1764**, 128/256k **492**.

Claim alignment: N_proof moved by a new write, not by relabeling 2-hop contradiction and not by n_worlds. 608/1046 are still long-range retrieval. 48 worlds of this mix would scale counts, not the mix. Next value is another _distinct_ gold program (different answer key / different token), or stop process expansion and use the data.

## Alignment review before P2.7 (2026-08-20)

Core claim: executable twins; necessary evidence; scale topologies not n_worlds.

`data/v2_p23` deep_dependency **82/998** decomposes as revisitation 48 + compare_belief 28 + company cross_stream 6. The other 608 are 2-essential long-range retrieval. Relabeling lab/code contradiction (`d2|n3`) as deep would inflate the share without a new write. A second 3-hop query with the same `active_latent` gold would clone revisitation.

P2.7 operator: `ratify_latent` after reopen. Distinct state key `controlling_latent`. Reopen activates; ratify is the later controlling write. Token stays in the seed only. JOIN must skip `ratification`. Keep revisitation as the 3-hop program. Not 48/210.

## P2.6 12-world N_style probe (2026-08-20)

`data/v2_p23` / `reports/v2_p23_quality.json`. pytest **41**. quality_gate ok.

- Proof mix unchanged vs `v2_p22` (register RNG is seed-derived, not the schema stream): canonical N_eff **42.155**, join **0.2525**, pulse 0, clones 0, rejects 0, revisitation full **48**, LD- in **48/48**, deep **82** / retrieval **608** / local **308**.
- New axis: **4 registers** (`instrument` 204, `ops_log` 394, `standup` 280, `audit_trail` 120 full slots). register_neff **3.679**. `style_cluster` is `{register}:{doc_type}` (20 clusters, neff **14.62**), not `doc_type`.
- Universal procedure blob: **0** rows. mean_near_dup_sentence_ratio **0.1718** vs v2_p22 train-full **0.391**. That is the anti-clone win.
- mean_real_source_token_ratio **0.803** (was 0.591): native discourse shrank, so internal-gap fill is more RFC. Length is still unique public sources, not pulse. Do not read this as “more grounded gold.”
- mean_evidence_distance **60435** (was 59978). Length buckets unchanged 16/32/64k = 1692, 128/256k = 456.

Claim alignment: N_style moved; N_proof did not. 48 worlds of this mix would not change deep_dependency share. Next operator should add a process hop, not n_worlds.

## Alignment review before P2.6 (2026-08-20)

Core claim: executable counterfactual twins; necessary evidence verified by intervention; scale unit is `N_proof × N_world-process × N_artifact × N_task × N_style`.

Honest gaps after `data/v2_p22`:

- 608/998 full slots are `long_range_retrieval`; only 82 are `deep_dependency`. Distance ≠ 5-hop. Do not restyle 64k packs as deeper programs.
- Weakest remaining axis is **N_style**. `style_cluster` is currently `artifact.doc_type`. `discourse._procedure()` pastes the same Working-record / Procedure / “Why this file is not padding” blob onto almost every email/log. That is the anti-pattern unique_prose was killed for: one author-facing paragraph, not workplace variety.
- Naive P2.6 (“more paraphrase templates”) would regress the paper. N_style must be a **world-sampled register** (`instrument` / `ops_log` / `standup` / `audit_trail`), short typed expansions, no new gold tokens, no 20-sentence bank.
- 48/210 worlds of the same JOIN mix still do not buy this axis.

This slice does **not** add a fourth hop. After P2.6, if deep_dependency share is still the claim risk, the next operator is another process program, not more worlds.

## P2.5 12-world packer + competing-token probe (2026-08-19)

`data/v2_p22` / `reports/v2_p22_quality.json`. pytest **38**. pulse 0, clones 0, **rejects 0** (P2.1c had 290 `distance_shortfall`).

- canonical N_eff **42.155** (46 programs). join_row_share **0.252**.
- Packer: skip fillers that individually exceed the cap; continue past oversized docs instead of stopping; shrink the end-buffer when min distance ≥8k. Length buckets are clean **16k/32k/64k = 1692 each** (no leftover 8k-labelled 16k-cap packs).
- Competing dormant token `LD-*` is in **48/48** revisitation full contexts (never acked; not essential). Gold remains `LT-*`. Copy-first-token is no longer a valid program shortcut.
- mean_evidence_distance **59978** (P2.1c 53764). Revisitation mean dist **75010**. real_source_token_ratio **0.59** (down from 0.82): more native workspace docs in the gold span, fewer RFC-only fills. Still public sources, not pulse.
- Labels (full): deep **82**, long_range_retrieval **608**, local_or_mixed **308**. Export drops `local_or_mixed` on 32k+.
- 48-world batch still optional. **Do not run 210.** Next slice is N_style, not more worlds.

## 128k related-work SFT baselines (2026-08-20)

Trainer is **ms-swift** latest main (`4.5.x.dev`, `.vendor/ms-swift`), not LLaMA-Factory. Official Qwen3.5 SP path (`examples/train/sequence_parallel/sequence_parallel_qwen3_5.sh`): `--attn_impl flash_attn`, `--padding_free true`, `--use_logits_to_keep false`, **no DeepSpeed**. Qwen3.5-4B full SFT, 2×H200 (physical 6,7), `max_length` **133120**, **packing off**, Ulysses **SP=2** (DP=1; true GBS = micro×accum×DP = 1×16×1 = **16**). CE chunk `CELOSS_PARALLEL_SIZE=1024` (ACC Table 1). lr 1e-5, **max_steps 680**. ckpt every 100, `save_total_limit=2` + `load_best_model_at_end`. wandb `wyncke/longworld`. Order: ACC → LongTraceRL → LongMIT (10802 subsample, seed 42). Data: ShareGPT dump then `scripts/export_swift.py` → `data/external/swift/*.jsonl` (OpenAI messages). OOM → Qwen3.5-2B, then ZeRO-2. Literal `<video>` in ACC SWE is remapped in the ShareGPT export.

## P2.1c 12-world revisitation + distance probe (2026-08-19)

`data/v2_p21` / `reports/v2_p21_quality.json`. pytest **34**. pulse 0, boilerplate 0, clones 0.

- canonical N_eff **42.08** (46 programs), up from P2.0c **39.2**. New topology is `revisitation` (`seed_latent+ack_latent+reopen_latent`, depth 3, n_ess 3), not JOIN resampling. join_row_share **0.254** (was 0.266).
- `revisitation` full slots **48** (all 3 domains: company 16 / lab 10 / code 22). Gold is `LT-*`. Token is in the seed artifact only; ack/reopen print `latent-acked` / `case-reopened`. Pack+ack without reopen ≠ gold.
- Labels (full): deep_dependency **82**, long_range_retrieval **597**, local_or_mixed **305**. mean_evidence_distance **53764**. Revisitation mean dist **66652** (min 9855, max 252721); 64k+ full mean dist **99675**.
- Packer rejected **290** `distance_shortfall` packs (mostly 32k wanting 16k span). No `local_8k_solves` kept. 8k length_label **48** rows are 16k-cap packs whose natural unique length sat under 12k — not pulse fill.
- Length (all views): 8k 48, 16k 2010, 32k 1278, 64k 1656, **128k 456**, **256k 456**. 256k is still a real-source cap tail; revisitation at 128k/256k is 3-hop with forced gold span, not 5-hop 256k reasoning.
- 48-world `configs/causalcore.yaml` is unblocked as a distribution batch. **Do not run 210.**

## P2.0c 12-world GroundedWorld probe (2026-08-19)

`data/v2_p20` / `reports/v2_p20_quality.json`

- canonical N_eff **39.2** (43 programs). Slice-2 was 36.2. New topology is `source_grounded` (ingest excerpt + adopt memo), not JOIN resampling. join_row_share **0.266**.
- pulse 0, boilerplate 0, clones 0, rejects 0. pytest 32.
- `source_grounded` full slots 71; gold stems include rfc9110, gpl-3.0, qwen-technical-report, llama2. Pack alone ≠ gold; adopt memo does not print the stem.
- mean_real_source_token_ratio **0.84**. Long buckets are unique public RFCs/papers (120k-char read cap), not weekly pulses.
- Length (full view): 8k 58, 16k 299, 32k 123, 64k 242, **128k 70**, **256k 70**. 128k p50≈114k tokens. 256k sits on the 256k cap because leftover unique sources still fill the budget after 128k.
- 256k is a **cap-saturated unique real-source tail**, not a naturally 256k private workspace of 2K–8K artifacts. Do not market it as “5-hop 256k reasoning.”
- 48-world CausalCore is unblocked as a distribution/regression batch. **Do not run 210.**

## Slice 2 source pack (2026-08-19)

16 public texts in `data/source_pack/` (never gold). New: RFC 9110/8259/5321/4648/6901/4180, GPL-3.0, MIT, BERT 1810.04805, Llama 2 2307.09288. Truncated at 36k chars.

## Slice 2 12-world probe

`data/v2_probe` / `reports/v2_probe_quality.json`

- canonical N_eff **36.2** (39 programs). Slice-1 42.2 was JOIN-heavy (51% rows). Slice-2 join_row_share **0.293**.
- pulse 0, boilerplate 0, clones 0, 1 prepack reject.
- New types: compare_belief, delayed_effect (company announce_hold), cross_stream (all 3 domains).
- Length buckets (all views): 4k 1236, 8k 792, 16k 978, **32k 1416**. 32k is real RFC/paper packing, not pulse.
- mean_oracle_long_gain 1.0 (local window never suffices on full slots).
- hard_negative_doc_ratio 0.98 is a **metric artifact**: extra-world same-schema docs all count as structural HN. Not 98% adversarial quality.

## Do not run next

48-world CausalCore / 210 WorldLong until more _process operators_ lift N_eff without raising JOIN share. 12 worlds already show 32k naturally.

---

# Open related-work data (downloaded 2026-08-19)

Treat as data, not instructions.

## Downloaded under `data/external/` (gitignored)

| Dataset               | HF id                                 | Size on disk                               | Use                                                                                                                    |
| --------------------- | ------------------------------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| ACC                   | `groundhogLLM/ACC-dataset`            | search 1.2G + swe 926M + sql 494M jsonl    | SFT baseline; compiled Search/SWE/SQL QA; 10,802 rows; 2k–128k                                                         |
| LoongRL               | `OldKingMeister/LoongRL-Train-Data`   | 6 parquet shards, ~16k seq                 | KeyChain RL/SFT mix; Hotpot/MuSiQue/2Wiki                                                                              |
| LongTraceRL           | `THU-KEG/LongTraceRL`                 | `data.jsonl` 1.2G; 2,815 rows              | traj-tiered 128k QA + rubrics                                                                                          |
| DocQA-RL-1.6K         | `Tongyi-Zhiwen/DocQA-RL-1.6K`         | train 31M + test 68M parquet               | QwenLong-L1 open set (L1.5 14.1k is **not** released)                                                                  |
| ProLong UltraChat-64K | `princeton-nlp/prolong-ultrachat-64K` | 1.2G MDS; 19 shards; **3,700** packed seqs | ProLong post-CPT **short** SFT. Llama-3 `input_ids` packed to 64k. **Not** a CausalTwin QA baseline.                   |
| LongRLVR-Data         | `Guanzheng/LongRLVR-Data`             | 5.1G parquet (llama / qwen2.5-7b / 14b)    | 8k–64k synthetic grounded QA. Train rows: llama 40,545; qwen7b 25,423; qwen14b 18,870. Test `64k_128k` is 61 rows.     |
| LongMIT-128K          | `donmaclean/LongMIT-128K`             | 28G `train.jsonl`; **64,392** rows         | Multi-hop packed passages (hop 2/3/4/1; en+zh). Concat length varies; first-200 tok-est p50~~65k, p90~~110k, max~123k. |
| LongAlign-10k         | `zai-org/LongAlign-10k`               | 664M `long.jsonl`; **9,888** rows          | Self-Instruct long SFT. `length` field 8k–64k (p50 12.6k, p90 36k, max 65510). **0% ≥64k.**                            |

`prolong-data-64K` / HELMET on this box are HF cache **stubs** (refs only). Real ProLong 64k MDS lives under `sparda/datasets/` (Llama-3 / MiniCPM / GLM tokenizers — wrong for Qwen3.5). Training backbone is `Qwen/Qwen3.5-4B` (download on the GPU machine).

## Not downloadable as a ready training dump

| Work                   | Why                                                                                    |
| ---------------------- | -------------------------------------------------------------------------------------- |
| SearchArt 2607.24850   | No public GitHub/HF dataset found                                                      |
| EXACT 2605.10544       | Method paper; corpus is FineWeb-Edu / SlimPajama / OpenWebMath, no EXACT-weighted dump |
| LongFilter 2510.25804  | Code at `HaoranDeng/LongFilter`; filtered SlimPajama subset not shipped                |
| QwenLong-L1.5 14.1k    | Not released; only L1 DocQA-RL-1.6K is open                                            |
| LongCrafter 2607.06160 | No linked HF dataset                                                                   |

## Converted ShareGPT

`uv run --extra train python scripts/export_external_llamafactory.py` → `data/external/llamafactory/{acc_search,acc_swe,acc_sql,loongrl,longtracerl,docqa_rl}.jsonl`

Train: `bash scripts/train_llamafactory.sh ext_acc` (or `ext_loongrl` / `ext_longtrace` / `ext_docqa` / `B5_8k`).

## ACC SFT packing (checked 2026-08-19)

No training repo. HF only ships `groundhogLLM/ACC-dataset` + `ACC-Qwen3-30B-A3B`. Paper Table 1 / model card: sequence length **131,072**, global batch 16, SP=8, 4 epochs, lr 1e-5, CE chunk 1024. **No packing flag.** Compilation budget \(B=131072\) (shuffle-concat evidence into one QA) is not dataloader packing.

Measured `data/external/ACC-dataset` (dialog char/4): Search n=3369 all labeled `100k`, p50≈89k, **98.4% ≥64k**, max≈101k (none actually ≥128k). SWE p50≈50k, 29% ≥64k, max≈129k. SQL p50≈43k, **0% ≥64k**, max≈60k. Fair ACC replay needs cutoff ≥~101k (paper 131072). A **64k cutoff truncates almost all Search** — that is ACC's long mode; SQL still fits. Full packing survey: `.hl/related_work.md` §7.

## B5 train cutoff (2026-08-19)

LLaMA-Factory `B1`–`B5`, `B5w`, `ext_acc`, `ext_longtrace`: `cutoff_len: 262144`, `packing: false`. 256k is the Qwen3.5 **native cap**, not a fill. `batch_size=1` ⇒ an 8k sample stays 8k. `B5_8k` stays 8192 + packing (small-GPU diagnostic). `p0.yaml` `max_seq_len: 65536` is the **data-gen** cap for the freeze; do not rewrite `data/p0`.

# P3 live probe findings (2026-08-24)

- GitHub CLI is authenticated as `Xnhyacinth`; public repository, PR,
  check-run, Actions, release and license endpoints are readable. REST core
  quota was 5000/5000 at the preflight check.
- Current observed probe pins are `/usr/bin/gh`
  `2fd925d68889746976958342fb749bf102bc7dc8bcba3abfa533a80ad7791673`
  and `configs/public_repo_allowlist.yaml`
  `9d4fc8fbc7a934513692033e8a9d97469a4dba1f39ba1e15fe65d65b9068bb67`.
  They may be fixed for the explicitly labelled local probe, but are not an
  independent production approval.
- At the original preflight, `configs/public_repo_episodes_v2.json` and the six
  role keys/pins were absent. They now exist: the signed bundle is in the repo,
  while probe secrets remain outside it under restricted permissions. This is
  still local-probe trust, not independent production approval.
- Truth regimes remain separate: simulated events may be
  `synthetic_executable`; only freshly fetched, scanned and signed GitHub bodies
  may be `real_public`; hybrid worlds must preserve that distinction per fact.

# P4 multidomain audit findings (2026-08-24)

- Shared selection and quality-gate code already counts retained domains and
  motifs, and at scale requires nonzero 16K, exact-token 64K, and verified
  real/hybrid source relations. P4 should add a separate three-domain
  `local_probe` profile rather than weaken these gates.
- Promotion replay already reconstructs `company`, `researchlab`, and
  `codeforge` candidates from their seed/spec and verifies question, answer,
  motif, essential artifacts, view, and prompt. A new domain task that cannot
  be deterministically replayed will fail promotion instead of silently
  entering SFT.
- The first P4 strict generation run proved that schema/query correctness alone
  is insufficient for long-context retention. Exact native focal dossiers were
  Company 5,983 tokens and ResearchLab 2,433 tokens, versus 36,032 for a
  36-workstream synthetic CodeForge world. With the P3 distance thresholds
  unchanged, Company and ResearchLab correctly retained zero rows. The fix must
  add event-bearing cycles and cross-cycle dependencies; reducing distance
  gates or reintroducing source-pack/filler would invalidate the objective.
- The corrected v5 batch validates semantic growth rather than merely
  `length × view` growth. Company 64K portfolio tasks cover 36 cycles with 144
  individually essential artifacts and proof depth 179; ResearchLab matrix
  tasks cover 88 workstreams with 89 essential artifacts, 441 sufficient
  artifacts and proof depth 76. All three domains have pinned-tokenizer 64K
  retention.
- Real-source scope remains narrow: 28/450 promoted rows are verified GitHub
  hybrid examples, while 422/450 are explicitly synthetic executable/schema
  examples. Company and ResearchLab are domain pipelines, not yet real SEC/PDF
  or paper-review exporters. The next value increase should come from those
  source-backed exporters and real workflow evals, not row replication.
- Scaling to 48 worlds increased the release to 1,784 rows and 203 unique base
  tasks, but executable proof and answer-program identities remain 18/18.
  Therefore 210-world scaling would mostly add instances, not sufficient
  semantic novelty. The next scale gate should require new programs/operators
  and independent source families.
- Runtime profiling on the 72-candidate run shows generation (~5 minutes) and
  dense ranking (~8 minutes) are not the dominant cost. Serial strict audit
  and promotion dominate at roughly half an hour each for the train split.
  The next performance change should shard by world with atomic manifests and
  deterministic merge; semantic/retrieval checks must remain exhaustive.
- A fixture-only SEC/company exporter skeleton now validates authorization,
  sec.gov identity, accession/CIK/form/date, source hash, grounded evidence
  spans, duplicate filings, secret patterns and email redaction. It remains
  `production_eligible=false` and `generation_integration=disabled`; no real
  EDGAR source has yet entered a world or answer.
- JPMorgan's official-PDF task proves a new Company program rather than another
  financial-value reconstruction: 10/21/43 bounded risk-taxonomy sections are
  necessary across 16/32/64K, while signed source relations grow 0/1/2. Strict
  audit accepted all nine views (342,420 tokens) after generation and promotion
  replay were wired to the same fail-closed relation selector.
- Walmart adds a distinct Company program over four official annual reports:
  segment identity, strategy risk, capex allocation, ICFR, and distant segment
  notes are jointly necessary. All nine views passed with 0/1/3 signed adjacent
  report relations after the fixture-signed source was re-attested by the
  current probe source role without changing content hashes.
- Sparks establishes a reusable but still fail-closed multi-file paper task:
  complete compiled-reachable sections retain exact scientific claims while
  comments/format markup are removed with source and compiled hashes. Its
  3/5/10-section stages passed all nine views with the authentic v5→v4 edge.

# P5 real-source, eval, trust, and audit findings (2026-08-25)

- Live SEC evidence is one authentic Apple 10-K complete submission (9,392,337
  bytes) plus submissions metadata and four exact filing-header facts. Live
  Wikimedia evidence is two Wikipedia revisions plus one Wikidata entity
  revision and three exact-span relations. Both are signed source inventories,
  deliberately `hybrid_train_ready=false`; neither yet affects world state or
  answers.
- Wikimedia public provenance is v2. It binds the canonical oldid URL separately
  from the exact API retrieval URL and requires the complete unique query
  contract. Missing fields, extra query semantics, identity mutation, fixture
  relabeling, and signed-manifest tampering fail closed.
- Three new query programs pass factual and CF replay, semantic proof, and every
  essential remove-one check over seeds 1/37/91. This is three validated program
  additions, not proof that the materialized 31 labels are 31 canonical
  executable IRs; the prior promoted batch still publishes 18.
- Four-worker strict audit processed 664 old candidates in 158.032 seconds,
  accepting 564 and rejecting 100 rows from two ResearchLab worlds because dense
  top-k solved them. Strict replay/filter remains the measured bottleneck. There
  is no completed single-worker full-filter baseline, so no speedup claim.
- The unseen manifest now fails closed on every missing/noncanonical dossier ID.
  Current 48-world output is honest `world_atomic_only`; topology/operator is
  ready, while entity independence, source-family breadth, and true multi-domain
  composition remain blocked.
- External benchmark execution and ECDSA/KMS approval verification are code
  paths, not results or independent trust. No RULER/LongBench v2/MRCR/GraphWalks/
  HELMET score exists. Production remains blocked on protected approved commits,
  fixed adapters, signed benchmark receipts, immutable KMS root/ARN pins, and an
  independently issued signature.
- Validation after review: 447 tests pass; targeted Ruff/format, mypy,
  compileall, and `uv lock --check` pass. Repository-wide inherited Ruff debt is
  intentionally not reformatted in this surgical slice.
- Security review additionally closed forged requested-title receipts,
  URL userinfo/nonstandard-port lineage, credential-shaped URL metadata, and
  stale ready split artifacts after an invalid rerun. Public Wikimedia v2 is
  exact-title-only until raw metadata resolution responses are retained.
- Production unseen eval remains HIGH-blocked: the builder does not yet verify
  every `sft_row` promotion attestation or bind/sign the split against an
  independently approved release manifest. Current unseen output is an
  engineering artifact, not a production eval release.

# P6 official API and publishing preflight (2026-08-25)

- OpenReview API v2 production endpoint is `api2.openreview.net`; forum-linked
  notes expose forum/reply relationships, while official retrieval guidance
  uses API v2 note queries by forum/invitation. The exporter must derive IDs,
  reply edges, invitations, and timestamps from returned note bytes rather than
  caller labels.
- arXiv's official API is Atom-based and official version records distinguish
  immutable submission versions. The source contract must retain an explicit
  versioned arXiv URL and bind the version in the source bytes; latest-only
  metadata cannot prove a revision relation by itself.
- Current Hugging Face Hub CLI supports browser or token authentication, private
  dataset creation, and resumable `hf upload`; official upload APIs support
  explicit allow/ignore patterns. P6 will stage a release-only directory and
  upload that directory, rather than point HF at the repository or `data/` root.
- GitHub is authenticated as Xnhyacinth with `repo` scope and origin is
  `Xnhyacinth/longworld`. Hugging Face CLI currently reports not logged in, so
  private dataset creation/upload is blocked until Xnhyacinth authentication is
  established. No token should be written to the repository or shell history.

# P6 final findings — 2026-08-25

- A signed real-source artifact can legitimately carry the source workflow ID
  rather than the simulated world ID. Semantic accounting must derive the set of
  workflow IDs bound by replayed world events; otherwise authentic arXiv bodies
  are falsely counted as generic background.
- Real source-relation scale is measured from replayed relation graphs, not row
  or view count. The live GitHub workflow supports two distinct valid CI
  failure→recovery chains; invalid/unknown candidates must be skipped rather
  than emitted to satisfy a relation quota.
- A weighted sampler is not duplicate-free if both the combined JSON and an
  identical single-weight shard are published. When only one weight exists, the
  sampler index now points directly to the combined unique file. With mixed
  weights, disjoint shards replace the combined file.
- The P6 local release is green at 542 rows / 12 worlds, but production claims
  remain blocked independently of code correctness: OpenReview live fetch is
  403, production KMS approval is absent, world/entity coverage is world-only,
  domain-composition has fewer than two groups, and HF authentication is absent.

# P7 source-rich run 1 findings — 2026-08-25

- The qualified P6 product has 542 executable rows and about 19.8M estimated
  context tokens, but only 44 rows in two worlds carry authentic public body
  text into answer execution; 16 of those are exact-64K. P7 must optimize this
  source-grounded numerator rather than total row count.
- Existing live local inventories are sufficient for an executable integration
  slice but not the 12-real-world exit gate: one Apple SEC 10-K, two Wikipedia
  revisions plus one Wikidata entity revision, one multi-version arXiv work,
  and the GitHub episode bundle. Additional authorized live episodes must be
  fetched after the adapters cross source→state→answer correctly.
- The immutable profile can already enforce all 12 promoted worlds as real by
  setting `min_real_train_worlds=10` and `min_real_eval_worlds=2`; no weaker or
  producer-authored `real_world` count is needed. The planned profile also needs
  four source families, at least 12 real task/relation identities, and 48 real
  exact-64K rows.
- Source loading is seed- and domain-bound in `scripts/generate.py`. ResearchLab
  currently accepts only `paper_workflow`; Company SEC and Wikipedia need
  explicit typed adapters rather than relabeling those workflows as papers.
- The P7 Wikimedia live fetch succeeded for four exact allowlisted titles and
  produced 12 source records, 12 exact-span relations, and four independently
  normalized workflows of three records each. The source manifest remains
  `generation_integration=disabled` until a domain adapter crosses the world
  boundary.
- The P7 SEC live fetch failed closed before writing any source file. Both the
  Python fetcher and a diagnostic request with a different compliant contact
  User-Agent received Akamai HTTP 403 from `data.sec.gov`, indicating a network
  access block rather than a parser/cache fallback. The existing signed Apple
  filing remains the only usable SEC live inventory on this host.
- Strict replay profiling isolated the primary CPU cost rather than blaming
  source fetch or dense ranking: one 66-row ResearchLab world spent 140.254 of
  193.121 seconds in repeated graph statistics. The old implementation issued
  roughly 1.22M reachability and 1.33M shortest-path calls. Reusing one causal
  subgraph and one single-source traversal per proof node preserves the current
  maximum-shortest-path metric while removing repeated graph construction.
- The graph optimization has no persistent cache and does not weaken replay or
  filtering. Focused Company/ResearchLab equivalence tests match an independent
  pairwise reference, and a regression proves `graph_stats` builds the world
  graph once. Full 1-vs-4-worker row-set/byte-SHA benchmarking is still required
  before claiming the estimated 3x end-to-end audit improvement.
- A single SEC query copied across length buckets failed the semantic-growth
  gate even after exact-64K packing. The correct fix was three chained
  publication-control stages whose answer keys depend on the preceding stage;
  `required_inputs` alone was insufficient because the semantic remove-one
  replay deliberately runs without strict preconditions.
- Candidate and promotion relation accounting initially diverged. Promotion
  replay correctly reconstructed 3/4/5 SEC policy/approval/ratification edges,
  while the view metric counted only GitHub and arXiv and emitted 0/0/0. The
  repaired metric filters the exact visible graph by the query's declared
  sufficient event set, preventing later packed events from inflating a shorter
  query. The final candidate and promotion both report 3/4/5.
- SEC edges after the authentic filing are simulated executable workflow edges,
  not independently observed SEC source-to-source relations. The resulting rows
  are valid hybrid causal training examples, but the source-provenance inventory
  remains only one authentic Apple filing workflow. P7 scale decisions must use
  unique authentic workflow/source coverage rather than treating these three
  programs as three independently acquired filings.
- The signed v4 SEC slice is not recoverable by relabeling. Once query-time
  filtering is correct, its first checkpoint contains only about 10.2K natural
  tokens, while the 32K/64K increments mostly comprise unrelated Company-cycle
  records. The corrected gate therefore requires a material proof-token share,
  not merely one additional short ratification memo.
- A valid offline path exists despite EDGAR HTTP 403: the already attested 9.4MB
  complete submission contains a main 10-K and EX-31.1/31.2/32.1 components plus
  exact XBRL facts for sales mix, geographic totals, and the balance-sheet
  identity. These must be parsed as non-overlapping parent-hash-bound components
  and sections; only sections whose facts enter the answer may supply 16/32/64K
  growth. This remains one authentic filing plus a simulated review workflow,
  not a multi-period real filing history.
- Wikimedia API access policy and content license are different provenance
  fields. Wikipedia revision text is represented as CC BY-SA 4.0/GFDL and
  Wikidata structured entity data as CC0-1.0; the User-Agent policy remains an
  access receipt and no longer populates `SourceLineage.license`.
- The local SEC complete submission contains exactly the four required reusable
  components at verified coordinates: the main 10-K plus EX-31.1, EX-31.2 and
  EX-32.1. Storing coordinates rather than component text avoids introducing a
  second body copy while allowing downstream section/fact derivations to bind
  the same immutable parent bytes.

# 2026-08-27 SEC exact-fact single-world closure (active)

- Existing uncommitted code already contains an exact-fact parser
  (`longworld/core/secxbrl.py`), non-overlapping SEC components/sections, and a
  staged `sec_financial_reconstruction` Company program. This is work to audit
  and validate, not evidence that a release is qualified.
- The staged code currently exposes 16k/32k/64k/128k variants. This user gate
  is specifically 16k/32k/64k; 128k must not substitute for a failing 64k
  closure or inflate the single-world result.
- The single-world pass is necessary but not sufficient to start the P7
  12-world run: unique workflow/source-family quotas remain independent hard
  gates. 48/210 remain blocked.

## 2026-08-27 — SEC source-to-state-to-answer finding

The successful unit is not a whole-filing paste. Exact source facts are parsed
first, then deterministic non-overlapping source views reveal those facts into
state, and staged answer events consume the state. This produced exact Qwen
lengths of 16,193 / 32,030 / 64,255 while evidence count, graph depth, and
program size all increased. Dense/BM25/TF-IDF/window shortcuts remained
insufficient and counterfactual text replay produced the declared CF answer.

The remaining limitation is structural rather than a length problem: the v6
world contains one Apple filing workflow. Hash/span lineage is real provenance,
but the causal `reads_section`/`extends` edges are simulated executable logic,
so `n_real_source_relations=0` is correct. Do not use this slice to claim the
12-world or production diversity gates are met.
# 2026-09-02 P14 conversion findings

- The absence of a formal training package is a quota/release-chain failure,
  not an absence of all source data: the current P13 inventory is 8 physical
  worlds, 6 quota worlds, 72 rows, and 2,710,819 exact tokens, but selection
  cannot legally run to completion before 12/12.
- The four just-finished routes are now negative controls: Megatron lacks
  enough unique tokens/materialized bands; Company reconstruction lacks
  ordered 16/32 support; oxc first-1000 CPT exceeds the fixed truncation cap;
  oxc/ruff CodeForge candidates do not complete nine cells. Further cap tuning
  or reconstruction retries are lower-value than changing entity and program.
- Efficient verification can omit unrelated full-suite reruns while retaining
  every data admission gate: validate new semantics locally, audit all nine
  cells once, and defer global selection/promotion/export until quota closure.
- Microsoft issuer GCS manifests bind both raw `source_sha256` and normalized
  `text_sha256`; SEC iXBRL parsing must verify and consume `text_sha256` while
  retaining the raw source digest as lineage. Treating the two as identical
  fails on FY2025 and is incorrect even when earlier filings happen to match.
- A program can pass remove-one and exact-band checks yet still fail genuine
  long dependence when ordered essential artifacts fit a shorter contiguous
  window. Adding answer-bearing operating-cash rows raised the Microsoft
  essential span enough to pass the unchanged window gate; adding unrelated
  filler would not have fixed the task.
- Reusing one source manifest for disjoint task sections may expand programs,
  but quota accounting must still apply the profile's source-workflow identity.
  Adapter-local workflow IDs are not evidence of independent provenance.
- CodeForge's generator and strict auditor previously disagreed on authentic
  relation semantics: a shared repository URL is not enough. The child record's
  signed binding must name the exact parent record. Applying the auditor's
  fail-closed predicate in generation made all 1/2/4-cycle Wasmtime relation
  sets replay exactly without weakening the audit.
- Single-file Company narratives can have enough total tokens yet remain
  locally answerable. Apple reached 32K/64K but its 16K disclosure answer fit an
  8K contiguous window; adding further sections solely to cross the threshold
  would be gate-driven filler and was rejected.
- Source-type expansion is sometimes the useful constraint reduction:
  Berkshire has four issuer-owned PDF annual reports and 555,529 natural tokens,
  but no native PDF extractor existed. A single locked `pypdf==6.0.0`
  synthesis dependency is justified because the standard library, installed
  platform tools, and existing lock cannot extract PDF text.
- Paper payload size is not revision-dependence size. GPT-3 has 106,903 unique
  source tokens but only 49,623 adjacent changed-file tokens; PaLM has 170,869
  and supports nested 16/32/64K changed-file stages. File-delta receipts must
  still exclude byte-identical, whitespace-only, and macro-metadata changes.
- Total narrative capacity is not executable proof distance. Berkshire's
  selected Pilot/cyber/governance facts occupy only about 8K of causal span at
  every band despite a 115,874-token section pool; unrelated authentic PDF
  chunks remain filler unless the answer program consumes them.
- PaLM v5 offers a more promising content task than its failed file-delta
  task: 39 recursively reachable TeX files provide about 84.9K tokens, and a
  strict 6/11/26-file content plan exists after excluding bibliography,
  caches, and one unreachable file. Packed-view distance still requires
  verification before implementation.

## 2026-09-03 P18 topology findings

- Microsoft FY2024/FY2025 filings contain a genuine presentation recast:
  every segment revenue and operating-income endpoint changes while the two
  disclosed totals remain 245,122 and 109,433 million dollars. Exact
  source/fact/span binding and remove-one-to-unknown semantics make this a
  viable deterministic oracle component, but its eligible notes and
  qualifiers retain only 3,889 deduplicated Qwen tokens.
- Raw iXBRL size is not long-context capacity. The same recast source has
  193,529 raw HTML tokens but only 4,267 relevant visible tokens before
  sentence deduplication; counting markup would manufacture length from
  presentation syntax.
- A real regression report, named bisect culprit, reviewed fix, maintenance
  backport, and first containing release still do not make a runnable
  regression-bisect world. The pandas #19970 chain retains only 21,200 Qwen
  tokens after near-deduplication, and its public record omits the ordered
  midpoint pass/fail trace needed to replay first-bad localization.
- The existing `ci_regression_origin` program is not a substitute: it proves a
  failed check to same-name recovery path, not ordered narrowing, a first-bad
  boundary, a distinct repair, or culprit/fix removal counterfactuals.

## 2026-09-03 P19 source-first findings

- The OAuth cross-specification graph is the first new P18/P19 topology with
  natural 128K capacity: RFC 6749/6750/6819/7636/8414/9207 plus RFC 9700 retain
  140,405 Qwen tokens after collapsing the draft-29/RFC 9700 `published_as`
  identity. This is capacity evidence only; packing and all admission gates
  remain unrun.
- RFC 9700 supports a deterministic six-field compliance vector and remove-one
  transitions, but the current adapter cannot ingest RFC 6750 because repeated
  page headers produce five RFC-identity matches. Dependency relation closure
  and a cross-specification DAG task compiler are also absent.
- OpenReview access failure must leave capacity undefined, not zero. The live
  official notes endpoint returned a human-verification challenge and the
  repository fetcher published no partial inventory; tokenizing a standalone
  arXiv paper would measure the wrong operator.

## 2026-09-03 P20-P23 implementation findings

- The OAuth source graph now has a deterministic six-field task, byte-bound
  dependency relations, a first-page RFC identity rule, source-attested replay
  sidecars, materialized full/cf/ordered projections, and a dense proof path.
  Counterfactual bytes exclude the exact authenticated RFC 9700 update span;
  derived order binds source timestamps and real relation identifiers.
- eLife 94586 is a useful 64K-only world rather than a failed 128K world. Its
  v1/v2 pool retains 76,142 near-deduplicated tokens and an exact 65,536-token
  witness; strict replay and review/response/three-delta remove-one audits pass.
- Public email redaction is compatible with exact provenance when raw hashes
  remain lineage-only, clean text gets an independent hash, and all evidence
  spans are recomputed and replayed against the redacted text. The eLife source
  workflow binds six redactions and four authentic relation kinds.

## 2026-09-03 P24-P34 candidate and domain findings

- IETF whole-artifact omission is not an admissible counterfactual shortcut:
  once the child artifact is absent, the CF row cannot replay back to the
  factual parent. Keeping one non-empty, source-contiguous RFC 9700 artifact
  that covers both `metadata_current` and `bearer_current` preserves exact-span
  editing and makes removal independently answer-changing.
- Source-span metadata is not provenance unless the artifact bytes equal the
  signed manifest slice. P33 now verifies every IETF artifact byte against that
  slice and accepts a synthetic child only for the exact task-bound operation.
- A declared dependency edge must bind its unique Datatracker supporting fact;
  endpoint records and an in-range quote alone cannot authenticate whether the
  relation is normative or informative.
- The final P33 world has six strict-audited full/CF/ordered rows at exact 64K
  and 128K. All raw 4K/8K/16K views are insufficient, but the rows remain
  candidate-only until selection, promotion, release gate, and B5 complete.
- eLife 94586 materializes one 64,512-token parent with 367 unique natural
  artifacts after rejecting a duplicate-bearing predecessor. Five are causal
  gold and 362 are explicitly natural background; projection is still needed
  before its long-dependency claim can be admitted.
- EPA PFAS proposal/final/correction PDFs retain 473,285 page-level near-dedup
  tokens, yet the currently decisive four pages total at most 6,213 tokens.
  Authentic source capacity therefore does not rescue a short-view oracle.

## 2026-09-04 P40 conversion findings

- A materialized counterfactual artifact must remain in the independently
  minimal evidence set; source authenticity alone does not make the edit
  answer-causal.
- Ordered-window failures should be repaired with a later answer-bearing source
  fact. RFC 9700 reverse-proxy sanitization creates real cross-window dependence;
  moving or adding background would only game the layout.
- P40 v14 passes all nine shared proofs with exact 32K/64K/128K views. Essential
  artifacts grow 13→14→17, authentic relations 11→12→15, and strict supports
  8→9→12; graph depth honestly stays 2.
- Generic training exporters must treat legacy descriptive `query_type` as
  optional for registered task rows. Both writer and deterministic validator
  must preserve the same `null` projection; inferring it from motif would create
  metadata not present in the promoted source.
- Manifest path identity resolves the repository `data` symlink to the physical
  LongWorld data root. Validators must receive the bound physical path rather
  than a lexical symlink alias.
- P53 OSV's current local artifacts are geometry diagnostics, not audited
  candidates. They reuse a P51 authorization that explicitly prohibited
  generation and persisted patch/release data; a new authorization record is
  required before rebuilding.
- Declaring essential IDs and returning `unknown` whenever one is absent makes
  leave-one and dense-subset tests tautological. Likewise, checking only that
  the total essential span exceeds 16K is not an exhaustive raw-window replay.
  P53 must adopt shared proof semantics before any 9/9 claim or Git commit.

## 2026-09-03 P14 closure findings

- A parent directory symlink in a portable task-sidecar registry must be
  canonicalized after the registry's relative-path and regular-leaf checks;
  copying sidecars or weakening the downstream `O_NOFOLLOW` walk hides the
  actual portability bug.
- Standard task-view candidates intentionally omit producer proof fields. Task
  promotion must serialize top-level `hop_count` from the independently
  replayed candidate graph so the final proof metadata is internally closed.
- Requiring one unique motif per world was redundant with stricter independent
  minima for real base tasks, executable proofs, answer programs, and semantic
  base tasks. Two paper entities and two BEA series honestly share task motifs;
  entity-specific renaming would overstate diversity.
- Exact source lineage plus a synthetic CF origin is insufficient by itself.
  Direct source-workflow CF rows now bind the exact factual parent, transformed
  child text, provenance operation, and source bundle; the dense audit binds
  the resulting digest before promotion.
