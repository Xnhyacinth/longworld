# Progress

- 2026-09-08 Unique-profile land (not CURRENT_RELEASE / HF): ACME 32k
  3/3 gate ok, B5 n=3 / 98339, motif `acme_issuance_succession+gold_rfc_evidence`.
  Authentic 8555-only (no leftover RFC Updates padding); 5 gold quotes /
  4 gold artifacts; between-gold leftover trimmed to exact band 32734.
  `production_eligible=false`. DNSSEC 64k already landed (B5 n=3 / 196643).
  Watch is already running; do not start a second execute. Do not pad or
  auto-promote.
- 2026-09-08 Unique-profile land (not CURRENT_RELEASE / HF): DNSSEC 64k
  3/3 gate ok, B5 n=3 / 196643, motif `dnssec_succession+obsoletes_and_updates`.
  Authentic `draft-ietf-dnsext-dnssec-protocol-09` → RFC 4035; 5 gold quotes;
  zipper-tail 33 artifacts at 65501 then packed 64k. `production_eligible=false`.
  ACME 8555-only 32k is packed (5 quotes / 4 gold artifacts, between-gold
  leftover) but dense audit is still open. Watch is already running; do not
  start a second execute. Do not pad or auto-promote.
- 2026-09-08 Unique-profile land (not CURRENT_RELEASE / HF): Amazon 128k
  3/3 gate ok, B5 n=3 / 395617. Meta 128k 3/3 gate ok, B5 n=3 / 393780.
  Honest 128k-only products; 16k full/cf stay 8k-zipper retrieval. SSH 32k
  rematerialized with coarsened RFC 4254 zipper tail (64 artifacts, 32359
  tokens) and dense-audited 3/3 `strict_long_dependency`; unique-profile
  gate ok, B5 n=3 / 97169. `production_eligible=false`. ACME export still
  fails `requested RFC relation target is not grounded`. DNSSEC still
  lacks a signed graph (draft became RFC 6840). Restart one watch after
  this land; do not start a second execute.
- 2026-09-08 Unique-profile land (not CURRENT_RELEASE / HF): HTTP/2 64k
  3/3 gate ok, B5 n=3 / 195480, motif `http2_succession+obsoletes_closure`.
  PKIX-path 64k 3/3 gate ok, B5 n=3 / 195555, motif
  `pkix_path_succession+obsoletes_closure`. Micron dual-partition 32k
  rematerialized through `financehistory` (32538 exact, structured CF JSON)
  3/3 `strict_long_dependency`, gate ok, B5 n=3 / 99206, motif
  `dual_partition_identity+europe_presence+mix_identity`.
  `production_eligible=false`. SSH 32k dense audit is in flight: RFC 4251 is
  only 16.6k tokens, so two-end gold fails the 16k intersecting bound unless
  leftover 4252/4254 are chunk-exploded after 4251 (`_dossier_spread` zippers
  chronology tails together). ACME stays 8555-only (48577 below 64k; unique
  82082 must not pad). DNSSEC has no signed
  `ietf_workflow_manifest.p57.dnssec.v1.signed.json`. Amazon/Meta keep the
  16k full/cf 8k zipper; do not promote 128k. Watch still running; do not
  start a second execute.
- 2026-09-08 Amazon v3 and Meta v1 128k-only dense audit 3/3
  `strict_long_dependency` (`global_proof_green`, 4k/8k insufficient, MiniLM
  top-k insufficient). `auto_promote=false`. World-atomic still blocked by
  the 16k full/cf 8k zipper (retrieval after re-validation, not auto-admit).
  Packed/audited 128k rows are not inventory.
  grounded targets; unique 82082 not padded). SSH packed 32k (32268) × 3
  views; official dense audit blocked (`essential_artifact_ids == 1`).
  Packed parents are not inventory.
  `market_mix_crossover` is a unique 64k local-probe (3 train / 195631 exact,
  `production_eligible=false`, not CURRENT_RELEASE). HTTP Semantics graph v2
  now compiles 9110 obsoletes 7538/7615/7694 (v1 left pinned). HTTP/2 and
  PKIX packed at natural 64k but V3 adapters are unregistered. Micron
  dual-partition packed 32k only. DNSSEC sign blocked (draft became RFC 6840).
  Amazon/Meta 8k zipper is 16k full/cf retrieval-after-revalidation, not a
  128k class; 12-view dense audit still running. ACME/SSH compiler track
  still open. Operator remains
  `scripts/run_p57_task_pipeline.py --catalog configs/p57_task_pipeline_v1.json
  --workers 4 --audit-workers 2 --watch`. Never pads or auto-promotes.
  Ledger `reports/p57_pipeline/ledger.jsonl`.
- 2026-09-08 Parallel synthesis restarted: NVIDIA extra mix task + Micron
  mix/geo design already running; added HTTP/2 64k, PKIX-path 64k, DNSSEC 64k,
  ACME 64k + SSH 32k, Amazon/Meta 8k-zipper classify, HTTP Semantics graph
  re-export. Shared `longworld/core` left to the NVIDIA adapter track. Do not
  pad HTTP/2 (103810), DNSSEC (126639), ACME (82082), PKIX-path (82943) to
  128k. SSH unique 52837 stays 32k. Packed parents still not inventory.
- 2026-09-07 HTTP Semantics succession v1 promoted as unique local-probe
  product `p57-ietf-http-semantics-128k-extension-probe-1-v1` (3 train / 0 eval,
  386,493 exact tokens). N_source-world=1, N_semantic-task=1, N_proof-family=1,
  N_training-view=3. Natural 128k only; 16/32/64 never packed. Dense audit 3/3
  with 4k/8k windows and MiniLM top-k insufficient. `production_eligible=false`.
  Not on CURRENT_RELEASE / HF. Next: extra independent Finance/IETF tasks from
  signed NVIDIA/Micron graphs.
- 2026-09-07 TLS 1.3 handshake succession v4 promoted as unique local-probe
  product `p57-ietf-tls13-64k-128k-extension-probe-1-v1` (6 train / 0 eval,
  581,651 exact tokens). N_source-world=1, N_semantic-task=1, N_proof-family=1,
  N_training-view=6. Length-view pairing, not nested 64k→128k relation growth.
  `production_eligible=false`. Not on CURRENT_RELEASE / HF. Next: extra
  independent tasks from signed NVIDIA/Micron/TLS/HTTP-Semantics graphs.
- 2026-09-07 TLS 1.3 handshake succession v4: 64k/128k × full/cf/ordered
  dense-audited 6/6 `global_proof_green=true` (exact tokens 65493–65513 and
  128364–128384; sum 581,651). Packed parents are not inventory. 16k/32k not
  packed: seven thick relation endpoints cannot fit those bands without quote
  isolation or leftover tinies. HTTP Semantics can now follow this 2-band
  promotion path. No HF.
- 2026-09-04 P38/P43 IETF conversion remains blocked with zero formal rows.
  Schema-fixed P33 rebuilt and audited 6/6; the 64K-only chain reached strict
  promotion but failed the existing lower-band quality gate. A natural
  16K/64K successor audited 6/6 at 16246--16277 and 64387--64418 tokens but
  selection rejected unchanged proof topology (relations 10→10, essentials
  7→7, supports 6→6, depth 2→2). P40 source-first growth is feasible: five
  new RFCs retain 76008 unique tokens and propose 10→20 relations. Inventory
  stays 132 train + 18 eval; no B5/HF.
- 2026-09-03 P17 converted two independent candidates into train-ready local-
  probe products: Microsoft Finance 12 rows (3 each at 16/32/64/128K) and
  Transformers CodeForge 6 rows (3 each at 64/128K). Both gates and B5
  manifests are green. P17 adds 18 train rows / 1,309,015 exact context
  tokens / 6×128K. Current signed inventory is 132 train + 18 eval rows,
  7,274,169 exact tokens, and 15×128K train rows. No HF.
- 2026-09-03 P17 diversity research recorded 26 primary URLs and six ranked
  executable designs. Entity copies on an existing operator count as volume,
  not semantic topology. BEA GDI 2005Q1 source/capacity/preflight/ranking is
  green but its 12-view strict replay remains pending and is not counted.
- 2026-09-03 P16 BEA local-probe extension is train-ready: 1 world / 12 rows
  / 3×128K at 128251 tokens, B5 export 12 rows, gate ok, near-dup 0.0.
  Independent of P15 v7. Finance stays unmixed (different probe). No HF.
- 2026-09-03 P16 four-track close: BEA 12/12 (128251) and Microsoft Finance
  12/12 (128217) are candidate-local 128K worlds; Pulumi FR blocked at
  114833 unique tokens; RFC9421 blocked at 72113 after reflow collapse.
  Formal train set remains P15 v7. No selection/HF. Next is a shared
  64K+128K profile, not leftover-PR or RFC padding.
- 2026-09-03 Pulumi failure-recovery blocked at 128K: 7 audited rows, 64K
  three-view in-band, 128K unique tokens 114833. DuckDB duplicate bodies.
  Remaining: BEA vintage 128K.
- 2026-09-03 Microsoft Finance 128K candidate: 12/12 dense audit, history
  128217 tokens, views 128584–128587, near-dup 0.0. `train_ready=false`.
  Commit `5cdb45e` plus adapter/chronology. Remaining: BEA, CodeForge FR.
- 2026-09-03 IETF RFC9421 blocked: unique Qwen tokens 72,113 after
  near-identical draft collapse; RFC is editor reflow of draft-19. No
  sidecar. Remaining: BEA, GitHub failure-recovery, MSFT Finance 128K.
- 2026-09-03 P16 start: P15 v7 closed (12 train / 6×128K / 2 worlds). Four
  disjoint workers: BEA vintage 128K, GitHub failure-recovery (new repo),
  Microsoft Finance 128K capacity, IETF RFC9421 unique-delta. No gate
  weakening. No uv/KEV relabel. No EDGAR hammer. No selection/HF until a
  new audited world exists.
- 2026-09-02 parallel fill closed: 0 new 9/9 worlds. CodeForge best oxc
  v12 is 6/9 (16k VS three-view + 64k RST; 32k RST `derived_view_gate`).
  dprint remains CodeForge world 1. Quota still Company 0/2, ResearchLab
  0/2, Finance 1/2, CodeForge 1/2. Ledger 8/6. `train_ready=false`. No
  selection/HF. Commit `7873977`.
- 2026-09-02 Company scout blocked: reconstruction ordered 16/32 cannot
  reach 8000/16000; four-annual `sec_annual_revenue_change` materialized
  queries but 0 rows (16k 6561<8000, wrap 15088, 32/64 YoY clones). Yaml
  not committed. Remaining: CodeForge world 2. No selection/HF.
- 2026-09-02 Megatron ResearchLab blocked (~50k unique tokens; 16/32
  undeclared by adapter). Config `9bd14e8`. Remaining: CodeForge world 2,
  Company scout. No selection/HF.
- 2026-09-02 CPT first-1000 oxc blocked at 207k ppm (cap 200k unchanged).
  skip-8000 not retried. Remaining agents: Megatron, CodeForge world 2,
  Company scout. No selection/HF.
- 2026-09-02 honesty + parallel fill: P13 still `train_ready=false` (formal
  train export 0). Inventory 8/6 / 72 rows / 2.71M SFT tokens plus 3047 CPT
  rows / 288M tokens. Four agents running: Megatron ResearchLab, CodeForge
  world 2 (oxc 32k or ruff), Company program scout (no reconstruction
  retry), CPT oxc first-1000 (not skip-8000). No selection/HF.
- 2026-09-02 Cyber cross-CVE v2: 9/9 dense audit complete (near-dup ≤
  0.0009). 16k is 3 CVE units after skipping 4k-leaking 2-unit packs.
  Ledger is 8 physical / 6 quota-countable (Cyber 2/2). `train_ready=false`.
  No selection/HF. Commits `bae500e` (4k skip) and `e1760ca` (chronology).
  Ranking used `/tmp/p13-st-venv`; do not `uv sync`.
- 2026-09-02 retry wave closed: no new countable SFT world. Microsoft
  v2 is 53 rows / 5 worlds, all missing 16k+32k ordered. Oxc CodeForge
  v8 is 5/9. MLRC generate-time 9-cell is the same SHA as a prior run
  whose CF audit near-dup exceeds 0.25. CPT oxc quieter slice packs 0
  rows. Apple 0 rows. Cyber 9-cell still unranked. Gated ledger remains
  7 physical / 5 quota-countable. Commits through `8414525` plus this
  note.
- 2026-09-02 retry wave (later): MLRC generate retry emitted a **9-cell**
  three-view world (9 rows, near-dup 0.015). Not yet ranked/audited, so
  ResearchLab is still 0/2 in the gated ledger. AEVB retry still missing
  16K (near-dup 0.46). Oxc CPT skip-8000/250 passed truncation (188k ppm)
  but packed 0 rows; skip-8000/1000 is running with GH pins. CodeForge oxc
  v9 generate running with GH binary pin. Apple P12 bundle has the wrong
  producer key; EDGAR four-annual fetch returned HTTP 403 (not retried).
  Microsoft five-seed generate still running. Cyber 9-cell ranking waits
  on sentence-transformers==6.0.0. Commits through `aa6ad9d`.
- 2026-09-02 retry wave: committed adapter/configs on `worlds`
  (`83192ce`, `302192b`, `0d8bac5`). Parallel tracks now running:
  Cyber 9-cell dense rank+audit, oxc CPT skip-2500/400 slice (ppm cap
  unchanged), Microsoft five-seed ordered-view retry, Apple 16/32/64K
  reconstruction, CodeForge oxc generate retry. No selection/HF.
- 2026-09-02 expansion: Cyber cross-vendor now has a new adapter
  (`cyber.cross_cve_remediation.v1`), sidecar-bound pipeline (3/3), and 9/9
  three-view projections (16/32/64K × full/cf/ordered, all in-band). Ranking
  and source-token v2 audit are still pending, so it is **not** added to the
  7-world content-gated ledger. Microsoft five-annual reconstruction 64K now
  lands in-band (65521/65287) but 16k/32k ordered views still fail distance,
  so Company remains 0/2. Oxc CPT license-binding v2 is pinned; materialize
  then failed the existing truncation-ratio cap. No selection, promotion, or
  HF upload.
- 2026-09-02 inventory: HEAD `44c8a0d` on `worlds` (same as `main`). P13 SFT
  remains 7/12 physical worlds, 5/12 quota, 63 rows, 2.37M tokens, all
  `train_ready=false`. CPT closure remains 3,047 rows / 288M tokens. Formal
  train release is still 0. Canonical data is in the `longworld` worktree;
  this worktree uses a gitignored `data/` symlink. Started Cyber history
  materialization, NVIDIA IR fetch, and uv/ruff/oxc CPT capacity scan. No
  selection, promotion, B-export, or HF upload this session.
- 2026-08-29 superseding P12 checkpoint: the strict content-gated baseline is
  29 rows / 4 source-bound worlds / 1,120,639 receipt-reported exact Qwen
  context tokens. The 12-world target remains unevaluated and production/KMS
  P12 rows remain zero. The older P8-zero and provisional 47-row/five-world
  entries below are historical diagnostics, not current release accounting.
  Code, tests, configs, and current status are being split into reviewed commits
  on `feature/p8-readable-source-gates`; P12 HF publication remains blocked.
- 2026-08-27 final P8 review/publish checkpoint: SEC semantic replay now binds
  canonical raw source bytes to the complete event envelope (identity, time,
  params, topology, visibility, and skip state) and to artifact identity/time.
  Seven coordinated tamper families fail closed. New artifacts use
  `artifact-semantics-v2`; current candidate/production verification rejects
  legacy by default, while explicit release-bound legacy audit remains possible.
  Independent review reports no remaining P0/P1. Final repository regression:
  755 passed; staged Python Ruff, targeted mypy, compileall, lock, diff, and secret
  checks pass. Git changes are grouped on `feature/p8-readable-source-gates`;
  newly qualified P8 rows remain zero, so HF is intentionally unchanged.
- 2026-08-27 SEC canonical replay hardening: independent review reproduced a
  synchronized world-event + artifact attack that could retarget a raw span and
  quote, replace an XBRL fact ID, or self-select a parent provenance while still
  returning gold. Three fail-first tests captured those bypasses. Semantic replay
  now regenerates the complete canonical section params from the trusted
  workflow/record and compares them exactly; its source-hash/metadata cache uses
  deep copies. The three attacks and the original coordinate attack now fail
  closed. Final focused grounded/visible/financial suite: 65 passed. Targeted
  Ruff, mypy, compileall, lock, and diff checks pass.
- 2026-08-27 P8 readable SEC integration: Company statement artifacts now use
  normalized visible filing text and retain exact absolute raw-source ranges
  for every XBRL/certification span. Apple/Amazon source→state→answer and CF
  tests pass, but readable proof text is naturally below the old 16/32/64/128K
  targets; the former markup-based length assertions were replaced by explicit
  insufficiency assertions. Focused SEC/promotion/data-contract regression is
  238 passed before the final canonical-replay hardening; the final 65-test
  focused suite covers that hardening.
- Independent review follow-up closed Amazon's interleaved-year leak: current
  geography spans exclude every `prior_geo_*` role and the unsupported 64K/
  128K programs are no longer emitted. Parent-context normalization preserves
  hidden ancestors, visible provenance binds raw/normalizer/visible hashes, and
  raw-coordinate tampering fails semantic replay.
- 2026-08-27 publication audit: Git `main` and `origin/main` are both at
  `564b826`; the private HF P6 payload is already byte/size synchronized at
  `32b5dcd`. A canonical `docs/CURRENT_RELEASE.md` and local machine-readable
  release status now state that newly qualified P8 rows are zero. No P7 row was
  uploaded. Historical invalid release directories remain audit evidence.
- 2026-08-27 domain-neutral grounded-span foundation added in isolated new
  files. It validates visible hashes, exact spans, source/fact/relation closure,
  typed provenance, and window-contained facts. Engine/retrieval integration
  across GitHub, paper, and Wikipedia is still required before 12 worlds.

- 2026-08-27 CORRECTION: SEC exact single-world v8 is invalidated and contains
  zero qualified rows. Raw-token source-span replay proves the nominal 16K MIX
  task is solvable in a single 4K window. Independent review also found that
  Amazon's current/prior geography rows are interleaved, leaving a fact-free
  essential tail, and that about 97.5% of the current statement-view characters
  are HTML/iXBRL markup rather than readable source text. Historical green
  receipts are reproducibility records only; 12/48/210 remain blocked.
- 2026-08-27 SEC exact single-world v8 reran from fresh candidate bytes after
  closing a semantic-role bypass. Equal-length Products→Services and
  Americas→Services edits now fail even when `params.text` is changed and the
  artifact is re-signed: semantic replay accepts only the immutable original
  event params or the query's explicit CF update, and verifies the raw section
  hash. The chain retained 12/12 rows (16/32/64K: 4/4/4), dense audit accepted
  12/12 with zero rejects, and gate v5 is green. Exact Qwen lengths are
  16,193/32,030/64,255; essentials 4/7/13; proof depth 2/3/4; ops 4/7/12.
  Promoted SHA `7c9b6e26…`; gate SHA `0b0bfe98…`. This closes the single-world
  source→state→answer criterion only. One workflow and zero authentic
  cross-record relations keep P7 12 worlds blocked; 48/210 remain blocked.

- 2026-08-27 P7 Wikipedia Jefferson one-world engineering slice is green.
  Extra beyond ResearchLab 4/4. Later `page-29922-r1369059101` + Q11812.
  16K tok 16,183 (middle `[[Sublime Porte]]`); 32K tok 32,592 (late
  `===Cabinet===`); 64K wrap 64,915 / 64,921 at pack 55,096 (leftover
  `===Thomas Jefferson Foundation sources===`). Graph proof depth 4/5/6
  after dropping ancestor-section shortcuts on later computes; hybrid
  3/6/8. Candidates 18/18; dense audit 18/18; promotion 18/18; gate v5
  green. Candidate
  `27fb36a5e9801d98803cb5590f372422696260c6f47e7c3f3c48ac6272dca121`;
  promoted
  `2a1a3a2faaa46a4b452e5ad54825bdb1e8d27512df0c92615311e16813e66bbc`.
  Gold `BORN:1743-04-13` / `COMM:Corps of Discovery` / `ENTITY:Q11812` /
  `POP:Autobiography of Thomas Jefferson: 1743–1790`. Do not start 12
  worlds from this title. Next N_task: Amazon eligibility 16K overflowed
  (tok 23,181); do not overwrite the Amazon 64K/128K slices. uv
  supersession is still queued.
- 2026-08-27 pulumi `#24184`→`v3.257.0` + `#24226`→`v3.258.0` did not land
  exact-64K. Allowlist Apache-2.0 pin `a48982fe…`. 16K emits 6 rows; 64K
  wrap 58,102 / 58,128 at pack 52,500 and still 58,102 after retune to
  58,510. Leftover is exhausted below the window. CodeForge stays 2/4.
- 2026-08-27 Amazon 128k honest extra band is green on the existing Amazon
  world (new out-dir, not overwriting the 64k slice). Gold grows with
  CF+FX / TAX / LEASE / OI, not leftover-fill and not Apple Note 7/8/9.
  Candidates 24/24 (6/6/6/6), zero rejects; dense audit 24/24; promotion
  24/24; gate v5 green. Packed 14,206 / 28,857 / 49,378 / 123,417; 64K
  wrap 64,174. Candidate
  `f63524b4004505ea941d46d0ce1bd0502353b0413405bdb13e618808f15e1c64`;
  promoted `d663557f959163360e64e8f3bb04a860831ae8fed583e32b09ac1250fe7ae6eb`.
  Do not merge into P6 v4; do not start 12 worlds from this filing.
- 2026-08-27 Apple 128k honest extra band is green on the existing v8 world
  (new out-dir, not overwriting v8). Gold grows with CF/TAX/LEASE/DEBT;
  pack target 100000 overflowed (gold ~116k). Retuned to 124000. Candidates
  24/24 (6/6/6/6), zero rejects; dense audit 24/24; promotion 24/24; gate
  v5 green. Packed 12,790 / 32,860 / 49,648 / 121,113; 64K wrap 64,854.
  Candidate `03bbd5e32c75efae27e595e0ae23d399edaaf14c70b64d83c4f51a50aaae460b`;
  promoted `7afaae390dace3986600fa561daf293cd535024782f6f7c93b2cb092a9974896`.
  Do not merge into P6 v4; do not start 12 worlds from this filing.
- 2026-08-27 dprint `#1174`→`0.55.0` + `#1207`→`0.56.0` did not land
  exact-64K. `#1215` secret-blocked. 16K of 0.55.0 emits; 64K wrap 72,244
  at pack 65,000. `#1210` leftover does not fill. CodeForge stays 2/4.
  Next leftover-fill scout: pulumi `#24184`/`#24226`, not another cited
  0.56.0 dprint PR.
- 2026-08-27 CodeForge ruff `#27170`/`#17804` did not land exact-64K.
  Allowlist MIT pin `9a51688c…`. 16K `strict_support_overflow`; 32K emits
  6 rows; 64K wrap 50,144 (first) / 66,128 (late) at pack 52,500 and
  51,420. Natural lengths, not cap-fill, so first/late straddle the
  1,536-token window. `#26460` is thinner than `#17804`. `#27766` has
  `cancelled`. Deno remains inventory (required wrap ~70k). CodeForge
  unique exact-64K worlds stay 2/4. Do not subset P6; do not pad biome.
- 2026-08-26 P7 SEC Amazon one-world engineering slice is green. Local
  `explicit_filings` import of `0001018724-25-000004` plus a CIK-keyed
  program (7 categories, 5 geos, no tagged Liabilities, EX-32.2, looser
  cert regex). Wrap 64,174; pack 49,600; 18/18 promoted; gate v5 green.
  Candidate `a21fcdd13fe38237676e320617a21c71b00cfefbb4531e40e4330b04e47e42fd`;
  promoted `c2001e06980f3179013eed389f45e49a74531f186d95d7da6842bfca078a22bc`.
  Company unique exact-64K worlds 2/4. Do not copy Apple headings; do not
  retry sec.gov; do not start 12 worlds from this filing.
- 2026-08-26 P7 Wikipedia MLK Jr. one-world engineering slice is green.
  `appendix_rest` was in the 64K pool; wrap ~59,775 was RFC `source_pack`
  cap-fill, not leftover skip. Query-pool filter plus leftover
  `==== ''The Measure of a Man'' ====` lands wrap 64,504 at pack 54,000.
  Candidates 18/18; dense audit 18/18; promotion 18/18; quality gate v5
  green. Candidate `5bf3678fd5047a382d67763c45731a9bc40f5f2bc9a14718c65cb3be766820ff`;
  promoted `e077f35ee6606c8ce74587abfc91ffb5b48d3335aefc2f173f180b47d516d293`.
  Gold `BORN:1929-01-15` / `COMM:oratorical preaching in Montgomery` /
  `ENTITY:Q8027` / `POP:I've Been To The Mountaintop`. Extra beyond
  ResearchLab 4/4. Do not restore Jane Elliott leftover.
- 2026-08-26 P7 Wikipedia Elizabeth II one-world engineering slice is green.
  Leftover rest_end retuned from Category to unique `==External links==`
  (~2,033 est) so pack 50,000 includes it inside exact-64K. Candidates 18/18
  (6/6/6, zero rejects); dense audit 18/18; promotion 18/18; quality gate v5
  green. Packed estimated tokens are 14,734 / 32,068 / 49,950; exact 64K wrap
  is 64,847 (first) and 64,846 (late). Candidate row-set
  `197fb8c680c2463848624238b08ad19323866dc2063c9ca73e3fe1397b480e78`;
  promoted row-set
  `88456e792441ccbddd7fb4bed0ea40d0074f7eda489adfba7d47e2d173550495`.
  Gold `BORN:1926-04-21` / `COMM:Jallianwala Bagh massacre` / `ENTITY:Q9682` /
  `POP:death of Diana`. Extra beyond ResearchLab 4/4. Do not start 12 worlds
  from this title.
- 2026-08-26 P7 GitHub uv one-world engineering slice is green. `astral-sh/uv`
  PRs `#17455` (tag 0.12.5) and `#21001` (tag 0.12.6) drive `version_selection`.
  Skipped CI no longer blocks a passed gate. Candidates 12/12 after dropping
  the filler-only 32K band (6×16K + 6×64K); dense audit 12/12; promotion
  12/12; quality gate v5 green. Exact 64K wrap is 64,758 / 64,848; pack
  52,500. Candidate row-set
  `28954cf16e878212fa3cc621f2d8e5607c959c7ad947671bfb920e2e835621fa`;
  promoted row-set
  `ce3231dce9a4778ba848ee30e922c752977875036f032da39b57c91b3ab88192`.
  This is CodeForge unique exact-64K world 2/4. Do not start 12 worlds from
  these two PRs; do not merge into P6 v4.
- 2026-08-26 P7 Wikipedia Obama one-world engineering slice is green.
  Barack Obama later revision plus Q76 drive BORN/COMM/ENTITY/POP. Candidates
  18/18 (6/6/6 at 16K/32K/64K, zero rejects); dense audit 18/18; promotion
  18/18; quality gate v5 green. Packed estimated tokens are 14,535 / 30,859 /
  49,284; exact 64K wrap is 64,976 (first) and 64,965 (late); pack 49,300.
  Candidate row-set
  `0244e4f9a85ae789c6303655f25d7fb43c0866713b8b10739b7efdf001cb2d30`;
  promoted row-set
  `4204bc063dca75f9e416ed2e1cb6fc60991d7c0f6771520dfbaa6deee9a01103`.
  ResearchLab 4/4 was already filled; this is extra unique-workflow inventory.
  Dense ranking now loads MiniLM on CPU after a CUDA OOM against GPU hold.
- 2026-08-26 P7 Wikipedia Newton one-world engineering slice is green.
  Isaac Newton later revision plus Q935 drive BORN/COMM/ENTITY/POP. The birth
  template is duplicated in the early body; evidence is a unique infobox
  wrapper, not the bare template. Candidates 18/18 (6/6/6 at 16K/32K/64K,
  zero rejects); dense audit 18/18; promotion 18/18; quality gate v5 green.
  Packed estimated tokens are 14,288 / 28,223 / 54,083; exact 64K wrap is
  64,959. Proof-bearing growth is 12,680 then 23,985 tokens versus 5% minima
  696/1,293. Authentic relations remain 0; hybrid signatures 3 (edges 3/5/10).
  64K proof stops before References; leftover is a References prefix ending
  at unique `=== Alchemy further reading ===`. Candidate row-set
  `50202f59fd709feadff42b8a5fc38b5a45094877c5feedd88e561c2f779ca3fe`;
  promoted row-set
  `5f24fa4ff10d4efe435f13882b26d1a047143b0396ccb31dad91ce3e852a0451`.
  Not merged into P6 v4; do not start 12 worlds from this title. Combined with
  Churchill/Einstein/Thatcher this is four unique ResearchLab exact-64K
  workflows.
- 2026-08-26 P7 Wikipedia Thatcher one-world engineering slice is green.
  Margaret Thatcher later revision plus Q7416 drive BORN/COMM/ENTITY/POP.
  Candidates 18/18 (6/6/6 at 16K/32K/64K, zero rejects); dense audit 18/18;
  promotion 18/18; quality gate v5 green. Packed estimated tokens are
  13,972 / 28,727 / 53,426; exact 64K wrap is 65,417. Proof-bearing growth
  is 13,500 then 20,146 tokens versus 5% minima 737/1,234. Authentic
  relations remain 0; hybrid signatures 3 (edges 3/5/10). 64K proof stops
  before Legacy; leftover is a Legacy prefix ending at unique
  `[[Scottish independence]]`. Candidate row-set
  `4bf6b6951d1fe27d92d69361e683f7a99dbabf000f8fea666e837af27704e6d3`;
  promoted row-set
  `b3916422ca481f3ec1f3cd1723132f10e38c629ed90a228d96a59e979cc18672`.
  Not merged into P6 v4; do not start 12 worlds from this title.
- 2026-08-26 Isaac Newton English Wikipedia/Wikidata inventory fetched
  (`wikimedia_p7_newton_v1`, Q935, later `14627-r1371274988`, ~56.2k est).
  Not programmed: the birth template is duplicated in the early body.
- 2026-08-26 P7 Wikipedia Einstein one-world engineering slice is green.
  Albert Einstein later revision plus Q937 drive BORN/COMM/ENTITY/POP.
  Candidates 18/18 (6/6/6 at 16K/32K/64K, zero rejects); dense audit 18/18;
  promotion 18/18; quality gate v5 green. Packed estimated tokens are
  15,946 / 32,823 / 54,055; exact 64K wrap is 64,982. Proof-bearing growth
  is 16,866 then 21,249 tokens versus 5% minima 843/1,061. Authentic
  relations remain 0; hybrid signatures 3 (edges 3/5/10). Full-page
  essentials overflowed pack 51,200; 64K proof stops before References
  and an authentic non-essential References prefix fills wrap. Candidate
  row-set `eee8896c19915e6628d86d2f2034bdcf08f021ccafced4c7805ca0dc0c08d68a`;
  promoted row-set
  `0c04fa828b43c5ee49d7942892385685cb3d9248c6782bb6c8f27ad65896bbe3`.
  Not merged into P6 v4; do not start 12 worlds from this title.
- 2026-08-26 Attention `1706.03762` signed as a unique paper workflow after
  generalizing the revision-added extractor beyond the Aviva/ONR/NSF regex.
  v1→v2 gold is the authentic 277-char parameter-attention sentence. Body
  ~21k estimated tokens: source→state, not exact-64K.
- 2026-08-26 P6's 80 signed GitHub episodes are already one CodeForge world.
  Leftover unused PRs cannot each fill exact-64K (only v2.34.0/v2.34.1 did).
- 2026-08-26 P7 Wikipedia Churchill one-world engineering slice is green.
  Winston Churchill later revision plus Q8016 drive BORN/COMM/ENTITY/POP.
  Candidates 18/18 (6/6/6 at 16K/32K/64K, zero rejects); dense audit 18/18;
  promotion 18/18; quality gate v5 green. Packed estimated tokens are
  14,757 / 28,006 / 51,141; exact 64K wrap is 65,073 (first) and 65,131
  (late). Proof-bearing growth is 13,182 then 23,187 tokens versus 5%
  minima 662/1,156. Authentic relations remain 0; hybrid signatures 3
  (edges 3/5/10). `generate.py` now emits Wikipedia hybrid edges; empty
  edges had blocked select (`0<1` real worlds). Candidate row-set
  `a31b7d1a3cd06c4c9e4d6ca4f13015891246231f69ee8b7959db18fed01e99ce`;
  promoted row-set
  `577644a028e0bc62d417482cc3c97364feb511b0637bf491a058dd89eb83995e`.
  Ada/Turing remain unique workflows that cannot fill exact-64K. This is
  not merged into P6 v4 and does not start 12 worlds.
- 2026-08-25 P7 SEC v8 one-world engineering slice is green. Exact XBRL facts
  from the attested Apple 10-K drive MIX/CAT/GEO/BS/CERT answers. Candidates
  18/18 (6/6/6 at 16K/32K/64K, zero rejects); dense audit 18/18; promotion
  18/18; quality gate v5 green. First tier is honest 16K (~12.7K estimated);
  exact 64K wrap is 64,854 Qwen tokens; proof-bearing growth is 16,997 then
  19,744 tokens versus 5% minima 1,004/840. Authentic relations remain 0;
  hybrid signatures 3. Candidate row-set
  `8579b9f83191d55750b416c7c1e438e2ba7c138d26c294e6bf932482d4495131`;
  promoted row-set
  `0fb120aeba8074bf218c2dbe25c084aecb4ab1133215e398d99f31a96b47fcfb`.
  This is not merged into P6 v4 and does not start 12 worlds.
- 2026-08-25 128k baselines on 8×H200: SP=4 DP=2 micro=1 accum=8 (GBS 16). SP=2 OOM'd 4B on 8-GPU (lm_head logits ~44–56 GiB). wandb group `longworld-128k-sft-8gpu`. ACC → LongTrace → LongMIT.

- 2026-08-25 P6 source-dependent probe v4 is green. Fresh generation produced
  828 candidates from 18 worlds, 92 base tasks, 24 executable proofs, 23 answer
  programs, 7 real relation graphs, zero clones and zero exact duplicates.
  Dense audit, world selection, and strict promotion retained 542 rows from 12
  worlds (4/domain; 428 train, 114 eval). The v2 quality gate passed with
  164/222/156 rows at 16K/32K/64K, 16 real-source exact-64K rows, 8 real base
  tasks, and 3 real source families. arXiv source text now counts as bound
  event-bearing workflow content rather than generic background. B5w was fixed
  to use one unique JSON file plus a sampler weight, with no copied weight
  shard. A 247.9 MiB committed private staging package validates as
  `local_engineering`; production remains fail-closed without a production
  profile and independent KMS approval. Local unseen topology/operator and
  source-family splits are ready; world/entity is world-only and composition is
  blocked. HF authentication is still absent, so no upload is claimed.

- 2026-08-24 P4 multi-domain scale-out started. User requested substantially
  more data and more domains. Work is split into non-overlapping Company,
  ResearchLab, and shared pipeline/profile tracks. The next generated product
  will be explicitly labelled multi-domain `local_probe`; P3 correctness gates
  stay fixed and real-source labels remain provenance-dependent.

- 2026-08-24 Final P3 review corrections passed targeted TDD. Dense audit
  receipts now bind the independently replayed per-view near-duplicate ratio;
  selection fails closed on a missing value and excludes a complete world when
  any sibling exceeds 0.25. Duplicate release tags select the richest
  executable episode and invalid rich priors fall back to the next valid prior.
  Training export applies its token cap to the first atomic unit and fails when
  no unit fits. The complete audit was rerun: 574/574 accepted, zero rejects;
  346 rows / 12 worlds promoted and the gate remained green. Canonical B5/B5w
  manifests validate 346 source rows, 12 outputs and 1.15% token spread.
- 2026-08-24 Independent rereview closed the remaining release fallback P1.
  Release executability now inspects only the current cycle's required closure,
  cutting traversal at `supersedes`: nested release→merge→CI chains remain
  valid, while an ancestor's passed CI cannot mask the current release's failed
  CI. The final code replayed all 574 candidates with zero rejects; audit and
  accepted files are byte-identical to the canonical signed chain. Final
  validation: 326 tests, Ruff/format, compileall, shell syntax and diff checks
  pass; no local-probe P0/P1 remains.

- 2026-08-24 P3.3–P3.4 local probe release is green. Fresh scanner-v2 inputs:
  80/80 allowlisted GitHub episodes, 2,232 records. Candidate pool: 574 rows,
  20 worlds, 81 base tasks, 9 executable proofs/programs, zero clones and zero
  exact duplicates. Dense audit accepted 574/574 with zero rejects. World-atomic
  selection excluded two worlds whose per-row near-duplicate ratio exceeded
  0.25, then promoted 346 rows from 12 worlds (10 train / 2 eval). Final gate:
  28 hybrid real-workflow rows, 5 real base tasks, 5 real source relations,
  6 exact-tokenizer 64K rows, 12/12 worlds, no errors. The real release chain
  grows from v2.34.0 at 32K/166 strict support events to v2.34.1 at 64K/230.
  Signed LLaMA-Factory B1/B3/B5/B5w export has 1.15% token spread; B5w has 24
  unique rows and weighted_n 48 via sampler weights, not duplicated JSON rows.
- 2026-08-24 Two release-chain defects were caught rather than waived. First,
  floating accumulation over a set made surface replay hashes vary across
  `PYTHONHASHSEED`; a cross-process regression now enforces sorted artifact IDs.
  Second, equal-token export stopped at the first oversized atomic unit; it now
  skips that unit and fills from later smaller units while preserving CF twins.
  Full validation after the fixes: 320 tests passed, touched Ruff passed,
  compileall and shell syntax passed. The release remains explicitly
  `local_probe`; 48/210 still require production trust and broader sources.

- 2026-08-24 Strict audit attempt 3 exposed a real data-quality failure after
  identity contracts were fixed: all 30 eval synthetic CF rows were rejected
  for short-context shortcuts, while full/ordered and real rows passed. The
  generated release decision treated all merges as causal but not required, so
  a lone decision document interpreted missing integrations as the blocked CF
  answer. Added a failing required-input regression, then made the release
  decision require every merge. All three long synthetic motifs now pass
  signed dense audit without changing answers or weakening gates; full/CF
  dossier twins audit atomically. Validation: 141 CodeForge, real-workflow,
  promotion, generation and data-contract tests plus touched Ruff pass.
- 2026-08-24 Fresh post-fix candidate generation completed in about 18 minutes:
  20/20 worlds, 366 signed rows, 61 base tasks, 7 executable proofs, 7 answer
  programs, 4 source relations, 7 motifs, zero clones/joins/exact duplicates,
  and 0.1263 retention. All semantic report fields exactly match the archived
  pre-fix run. Identity preflight passes for 366/366 base-task IDs, world
  holdouts and candidate signatures. There are 12 real-relation 64K rows from
  one real-public world (version selection and cross-repo release dependency),
  with fixed-tokenizer contexts of 65,505–65,535 tokens.
- 2026-08-24 P3.3 identity-contract TDD: strict promotion first rejected all
  candidates because generation used the root materialization ID for
  `base_task_id`, then because world holdout used that root ID while replay
  uses the actual `:focal` world ID. Base-task generation was already aligned;
  added a failing emitted-row regression for holdout identity and changed the
  caller to use `focal_w.world_id`. Validation: the regression failed before
  the fix, then 52 dense-promotion/data-contract tests and touched Ruff passed.
- 2026-08-24 P3.3 gate correction passed TDD: profile-bound probes now honor
  their declared `min_domains=1` while non-profile and production runs retain
  their existing multi-domain checks. Natural synthetic CodeForge dossiers
  land near 32K, so signed training buckets are now 16K/32K/64K; real exact
  64K remains mandatory. The candidate pool is 20 for a strict 12-world
  world-atomic selection. Validation: 104 affected tests and touched Ruff pass.
- 2026-08-24 Probe trust recheck initially guessed obsolete approval field
  names, then validated the actual schema: six unique role IDs, six `0600` key
  files, both pins present, environment `probe`, and both production and
  independent approval false. No secret values were read or printed.
- 2026-08-24 Fresh generation correctly failed on duplicate real source bodies.
  Root cause: distinct GitHub check-run IDs with the same check name, commit and
  status rendered byte-identical CI records. Added the actual check-run ID to
  exported CI text under a failing-first test, then re-exported and newly signed
  all 80 episodes: 80/80 succeeded, 2,232 records, zero timeouts/failures. The
  rebuilt bundle loads all four real query specs and passes the duplicate-body
  gate. Validation: 89 exporter/real-workflow/data-contract tests and Ruff pass.
- 2026-08-24 The first green 20-world candidate set retained 20/20 worlds, 126
  rows, six exact-token 64K real rows, zero joins/clones/exact duplicates, but
  only three motifs and two real source-relation graphs. Kept the gate red.
  Added two distinct long answer programs over all 36 workstreams (CI receipt
  matrix and license clearance matrix); strict smoke retains all three
  synthetic long programs at natural 32K. Added a truth-labelled synthetic
  cross-repository integration policy whose answer joins a real release with a
  real merge/license from the other repository. Source relations are now
  recomputed from events visible in each actual view.
- 2026-08-24 Cross-repo strict replay then exposed one distinct approved review
  whose empty body duplicated another review in the same repository. Added the
  actual review/comment ID to exported text under a failing-first test and
  freshly re-exported/re-signed 80/80 episodes (2,232 records, zero failures).
  The rebuilt hybrid proof now has six individually necessary artifacts and 72
  strict support artifacts; factual replay and the changed-commit CF replay
  exactly equal their signed answers.

- 2026-08-24 P3 live execution started: user authorized dual-track synthetic
  executable expansion plus fresh real-public GitHub re-export. Run is high
  complexity (10/10), mixed routing. Success requires 12 promoted worlds,
  nonzero real 64K retention, and all strict replay/retrieval/semantic-growth
  gates; 48/210 remain blocked. GitHub read preflight passed as `Xnhyacinth`;
  probe keys/pins and the v2 bundle are not yet initialized.
- 2026-08-24 Probe trust initialization attempt 1 created only the protected
  `0700` directory: the installed OpenSSL CLI requires the byte count after
  `-out`, so no partial key files were produced. Retrying with the documented
  local syntax; this is logged rather than silently repeated.
- 2026-08-24 P3.0 complete: generated six distinct 48-byte probe role secrets
  outside the repository under a `0700` trust directory with `0600` files;
  assigned six distinct `probe-*-20260824-01` IDs; fixed the reviewed policy and
  `/usr/bin/gh` hashes in a `user_authorized_local_probe` record explicitly
  marked non-production and non-independent. Loader syntax, JSON, permissions,
  pin equality, key lengths and uniqueness all passed without exposing secrets.
- 2026-08-24 P3.1 smoke passed: freshly exported and loaded
  `psf/requests#7012` at `v2.33.0` (205 records) and
  `opensearch-project/opensearch-php#419` (42 records) as `real_public`. The
  first validation print guessed a nonexistent dataclass field; after reading
  `RealWorkflow`, the actual provenance loader passed. Starting resumable
  four-worker re-export of the remaining legacy identifiers without reusing
  legacy bodies or signatures.
- 2026-08-24 P3.1–P3.2 complete: four-worker live re-export succeeded for
  **80/80** identifiers with zero failures (78 exported, 2 smoke outputs
  revalidated), yielding 2,232 scanner-v2 records and 17 ancestry-verified
  release workflows. Created and consumer-loaded the signed
  `configs/public_repo_episodes_v2.json`: 80 unique workflow IDs, all
  `real_public`, 13,014 bytes. Advancing to fresh 14-world candidate generation.
- 2026-08-24 P3 release-chain hardening: compatible train-ready real data remains **0 rows**; the 80 local episodes (4.8 MB raw workflow exports) remain legacy/incompatible. Release selection is deterministic and world-atomic, binds exact candidate/audit sets, requires an independently signed post-quality-gate receipt for 48/210, and enforces real-workflow split quotas (probe train ≥1; 48 train/eval ≥4/2; 210 ≥8/4), preventing the only real seed from landing entirely in eval. The gate receipt is issued only by the read-and-recompute API, binds exact report/train/eval hashes, green metrics, gate revision, and the original pinned producer identity, cannot overwrite its source files, and prevents a signed-but-failing report from unlocking scale. Real release proofs grow from an earlier 16K release to the latest 64K release through required supersession links; repeated license snapshots and duplicate release tags cannot fake growth. P3 uses no unbound source-pack budget. Signed training export is profile-fixed to 16K/64K and B1/B3/B5/B5w; B2/B4/B5_8k fail closed, minimal stays separate, B5w uses the verified LLaMA-Factory v1 weighted data index, Swift refuses unconsumed weights, and >5% equal-token spread fails before signing. Transform v4 preserves full/CF dossier twins atomically after the equal-token cap and puts the system instruction in the consumed ShareGPT conversation; the single-GPU trainer is unsigned diagnostic-only. Verification: **295 tests passed**; touched-file Ruff/mypy, all shell syntax, compileall, and diff checks passed. Repo-wide Ruff still reports 30 pre-existing style/executable-bit findings outside this surgical release-chain diff.
- 2026-08-23 P3 current-gate audit: the former 14→12 probe is historical only. The current loader accepts **0/80** bundled public episodes because they predate role-bound HMAC v2, scanner v2, and pinned policy/client receipts; 17 also lack merge-to-tag ancestry. Promotion now independently replays semantic/strict proof, CF text, remove-one, 4k/8k/16k, BM25/TF-IDF and dense prefixes, rejects CF short-context shortcuts, and validates view/composition/order. Multi-repo release selection now targets the latest dated release and names its source. The 12-world rerun is blocked on fresh authorized GitHub exports and external probe credentials/pins; 48/210 remain blocked on that rerun, asymmetric-key/KMS trust roots, and broader source families.
- 2026-08-20 P2.12 complete: train 128k/256k; all 18 source packs longest-first; rfc9110 no longer A–Z dropped; qwen/deepseek re-fetched (156k/140k chars). Probe `data/v2_p28` N_eff 50.47 join 0.220 pulse 0 rejects 0; by_length **1968 each** 16–256k; train 128/256k **1404** rows; RFC 16k 0.43 / 256k 0.85; B5 export 4530 rows with 128k+256k **840 each**, memory 0. pytest 55. Not 48/210, not pulse.
- 2026-08-20 P2.12 started: review — mix is right, compile was wrong, **length/source utilization is still wrong**. Train worlds never emit 128k/256k (`eval_length_buckets` only). `n_source_pack=16` alphabetical leftover **drops rfc9110/rfc9112** (longest unique texts). Read cap 120k chars (~30k tok) contradicts the 256k comment. qwen/deepseek on disk are arXiv chrome stubs. Fix: all packs, longest-first, higher cap, 128/256k on train, export those buckets. 12-world `data/v2_p28`. Not 48/210, not pulse fill.
- 2026-08-20 P2.11 complete: SFT export drops `memory`; default buckets include 16k; freeze `data/v2_p27` → `data/sft/causalcore_v2` / `_swift`. B5 345 rows, 16k largest, memory 0, long local_or_mixed 0, spread 0.51%. pytest 53. Recipe `configs/swift/B5_v2.yaml`. Not hop-5 / 48/210.
- 2026-08-20 P2.11 started: review of `v2_p27` — N_proof/N_artifact/N_style are in. Remaining value is **use the mix honestly**. B5 export trains `memory` (calendar card, gold still the long answer, length_bucket copied from 64k pack) and defaults drop 16k (the native-workspace majority). Fix view gold + export buckets; freeze `data/v2_p27`. Not hop-5 / 48/210.
- 2026-08-20 P2.10 complete: `source_choice` pair gold on existing ingest+alt+adopt. Probe `data/v2_p27` N_eff 50.581 join 0.215 pulse 0 rejects 0; unique answers **123→134**; deep **178→256** (21.8%); source_choice 78/78 pair gold, 0 overlap with source_grounded; RFC 0.450. pytest 50. Not hop-5 / 48/210.
- 2026-08-20 P2.10 started: review of `v2_p26` — cascade hops and RFC-preference are done. Remaining N_proof is a **non-cascade** distinct gold. GroundedWorld already ingests a competing alt file but `source_grounded` gold is only the adopted stem. Add `source_choice`: gold `adopted || unused`, n_ess 3, CF `adopt_alt`. No new events. 12-world `data/v2_p27`. Not hop-5 / 48/210.
- 2026-08-20 P2.9 complete: native workplace docs outrank unbound RFC; leftover RFC still last-resort length. Probe `data/v2_p26` N_eff 47.625 join 0.230 pulse 0 rejects 0; RFC share **0.801→0.445**; 16/32/64k majority native; 128/256k still RFC-backed. Proof mix unchanged (deep 178, docket 48). `core_as_of` no longer follows ratify past rollback. pytest 47. Not 48/210.
- 2026-08-20 128k: stopped 4B (~41min/step torch GDN fallback). Installed FLA 0.5.2 + causal-conv1d 1.6.2 into ms-swift venv. Liger skipped (`qwen3_5` unsupported; SP disables liger CE). Relight ACC 4B SP=2.
- 2026-08-20 P2.9 started: review of `v2_p25` — cascade hops stop. Weakest axis is N_artifact honesty: packer boosts unbound RFC (+6) and labels them structural HN, so 80% tokens are public fill while HN reads 0.43. Prefer native workplace docs; RFC last-resort length; unbound source_pack is background. 12-world `data/v2_p26`. Not hop-5 / 48/210.
- 2026-08-20 P2.8 complete: `seed_docket` (`DK-*`) + `docket_control`. Probe `data/v2_p25` N_eff 47.625 join 0.230 pulse 0 rejects 0; unique answers **117→123**; deep **130→178** (16.3%); docket_control 48/48 with gold occ 1.0 and LD- 48/48. pytest 44. Cascade hops stop here. Not 48/210.
- 2026-08-20 P2.8 started: review of `v2_p24` — 48 worlds still do not change mix; unique answers stuck at 117 because ratification copies `LT-*`. Add `seed_docket` (`DK-*`); ratify writes `controlling_docket` only after the latent cascade. New gold, not a fifth LT-* stamp. 12-world `data/v2_p25`. Not 48/210.
- 2026-08-20 P2.7 complete: `ratify_latent` 4-hop (`controlling_latent`). Probe `data/v2_p24` N_eff 44.89 join 0.241 pulse 0 rejects 0; deep 130/1046 (12.4%, was 82/998); ratification 48/48 with LD- and case-ratified; revisitation still 48. unique answers still 117 (same LT-* string). pytest 43. Not 48/210.
- 2026-08-20 P2.7 started: review of `v2_p23` — deep is 82/998 (48 revisitation + 28 compare_belief + 6 cross_stream). Relabeling contradiction would fake N_proof. Add `ratify_latent` after reopen: gold `controlling_latent`; seed+ack+reopen ≠ gold. New query type, JOIN skipped. 12-world `data/v2_p24`. Not 48/210.
- 2026-08-20 P2.6 complete: 4 workplace registers; killed universal `_procedure()`. Probe `data/v2_p23` N_eff 42.16 join 0.252 pulse 0 rejects 0; register_neff 3.68; near_dup 0.39→0.17; LD- 48/48; pytest 41. Next is N_proof (deep 82/998), not 48 worlds.
- 2026-08-20 P2.6 started: review says weakest paper axis is N_style (`style_cluster` was `doc_type`; universal `_procedure()` cloned onto almost every email). Implement 4 workplace registers, kill the padding manifesto, 12-world `data/v2_p23`. Not 48/210. Deep-dependency share stays a later N_proof problem.
- 2026-08-19 P2.2–P2.5: packer skip-oversized (0 distance_shortfall vs 290); competing `LD-*` decoy in 48/48 revisitation packs; export drops local_or_mixed on 32k+. Probe `data/v2_p22` N_eff 42.16 join 0.252 pulse 0. pytest 38. Next: N_style. Not 48/210.
- 2026-08-19 P2.1c: 3-hop revisitation + packer distance; `data/v2_p21` N_eff 42.08, 290 distance_shortfall, pytest 34.

- 2026-08-19 P2.0c: GroundedWorld ingest+adopt; 12-world `data/v2_p20` N_eff 39.2, join 0.266, pulse 0, rejects 0, 16/32/64/128/256k from unique public sources. pytest 32. 48 unblocked; not 210.
- 2026-08-19 Slice 2 complete: 12-world `data/v2_probe` N_eff 36.2, join_row_share 0.293, 32k largest bucket from real source packs, pytest 31. No 48/210.
- 2026-08-19 Slice 2 started: JOIN cap + compare/delayed/cross_stream + real source-pack widen; no 210.
- 2026-08-19 Phase A shipped (no pulse/prose fill).
- 2026-08-24 P3 strict audit retained 363/366 stale candidates and correctly
  rejected all three views of one real `ci_regression_origin` task. The CF
  views had a 4K shortcut and the chronological view placed the entire proof
  within 673–678 tokens. Old candidate/audit files are diagnostic evidence
  only. Current repair keeps the real failure→recovery source corridor,
  changes a body-visible commit with a same-format alternate SHA, and applies
  full replay/retrieval/window/distance gates independently to every emitted
  view before an atomic sibling set can be written.
- 2026-08-24 P3 CI/source-corridor repair is green. Window budgets now share
  the context token unit; proven necessary sets prune impossible window replays
  without reducing 4K/8K/16K enumeration. Real CI dossiers retain and
  deterministically compact only authentic records from their bound source
  interval, preserve semantic-essential versus strict-support roles, and use a
  same-format counterfactual SHA. Exact per-view gates run before atomic write;
  byte-identical ordered/full views are not duplicated. Seed 2 retains 22
  hybrid rows: 4 CI 16K rows, 12 real 64K rows, version-selection evidence
  count 230 and cross-repo evidence count 72, both with ~65.4K span. The older
  local 16K release task remains filtered. Impacted regression suite: 190
  passed; touched-file Ruff passed.
- 2026-08-19 Phase A shipped (no pulse/prose fill).
- 2026-08-19 Phase B/C lite: program_join N_eff 16→19; 18 tests pass.
- 2026-08-19 LongWorld v2 slice 1: probe N_eff 42.2; pytest 24.
- 2026-08-20 128k baselines: retry ACC 4B then 2B with LLaMA-Factory **v1 Ulysses cp_size=2** (2 GPU SP=2, GBS 16, FA2). Official LF blocks qwen3.5 SP; vendor check is warning. GPUs 6,7.
- 2026-08-19 Train configs: LoRA removed. All LLaMA-Factory yamls are `finetuning_type: full` + ZeRO-3, lr 1e-5. `train_sft.py` is full weights too.
- 2026-08-19 Backbone switched to `Qwen/Qwen3.5-4B` + `qwen3_5_nothink` (was Qwen2.5-7B-Instruct).
- 2026-08-19 Downloaded LongRLVR-Data (5.1G), LongMIT-128K (28G, 64392 rows), LongAlign-10k (664M, 9888 rows).
- 2026-08-19 B1–B5 / B5w / ext_acc / ext_longtrace: cutoff 262144, packing off. Survey in `.hl/related_work.md` §7.
- 2026-08-24 P4 multidomain integration started: frozen an isolated 18-candidate
  / 12-promoted local probe with deterministic 6/6/6 Company, ResearchLab,
  CodeForge allocation. Shared profile/scheduling and Company targeted tests
  are green; ResearchLab tests are intentionally red while its new
  revision-review-reproduction render/query chain is being completed. No P3
  artifacts are overwritten, and batch generation has not started before the
  three domain tests are green.
- 2026-08-24 P4 first strict batch completed at
  `data/p4_multidomain_candidates`: 166 unique candidate rows, 25 base tasks,
  0 exact duplicates, 16K/32K/64K all nonzero, but only CodeForge emitted
  (6/6 CodeForge worlds; 0/6 Company; 0/6 ResearchLab). This is not promoted.
  Root cause is measured native workflow size, not a scan or signature error:
  Company has 23 native artifacts / 5,983 exact tokens (max evidence span
  ~6.3K); ResearchLab 20 / 2,433 (max ~3.6K), below the unchanged 8K 16K-band
  distance gate. Started parameterized event-bearing multi-cycle extensions;
  no gate relaxation or filler is authorized.
- 2026-08-24 P4 v3 complete: explicit signed `n_workstreams` fixed promotion
  replay for non-code domains. Candidate 18 worlds / 716 rows; dense audit
  accepted 690 and rejected 26 dense-top-3 shortcuts. World-atomic selection
  promoted exactly Company 4 / ResearchLab 4 / CodeForge 4: 478 train-ready
  rows, 16,030,148 context tokens, 16K/32K/64K = 150/234/94, 57 base tasks,
  19 executable proofs, 5 verified real source relations, 6 verified real
  exact-64K rows, and zero duplicates/boilerplate/pulse. Signed quality gate
  and LLaMA-Factory export manifest both pass; full suite 351 passed.
- 2026-08-24 P4 v3 was invalidated after detecting six identical prompts with
  conflicting factual/CF answers. v4 was diagnostic only: dense audit exposed
  shallow Company `version_diff` shortcuts, so it was not selected or
  promoted. Both batches are excluded from training.
- 2026-08-24 P4 v5 is the corrected three-domain local probe. Eighteen
  candidate worlds emitted 688 rows; dense audit accepted 684 and rejected 4.
  World-atomic selection and strict replay promoted Company 4 / ResearchLab 4
  / CodeForge 4, totalling 450 rows and 16,775,741 recorded context-token
  estimates. The 126 exact-64K rows use a pinned Qwen tokenizer and split
  Company 46 / ResearchLab 74 / CodeForge 6. Prompt conflicts, exact
  duplicates, boilerplate and pulses are all zero. The truth split is 28
  verified real-workflow hybrid rows and 422 explicitly synthetic executable
  or schema rows. The quality receipt and 12-output training manifest pass;
  full tests are 361 passed.
- 2026-08-25 P4 local-48 completed the full signed pipeline from 72 candidate
  worlds to 48 promoted worlds, exactly 16 per domain. It produced 1,784
  train-ready rows (Company 544 / ResearchLab 802 / CodeForge 438),
  66,806,787 recorded context-token estimates and 16K/32K/64K =
  510/808/466. Dense audit rejected 38 shortcut rows and atomically excluded
  four ResearchLab worlds. The quality gate is green with zero prompt
  conflicts or duplicates; all 466 exact-64K rows pass the pinned Qwen range.
  Truth remains 28 verified GitHub hybrid rows and 1,756 explicitly synthetic
  executable/schema rows. The 12-output SFT manifest validates; B5w has 148
  unique rows at sampler weight 2, and condition token spread is 0.24%.
- 2026-08-25 semantic-growth gate repair: natural underfill created two honest
  32K variants from the nominal 32K and 64K caps. The gate incorrectly tested
  the pair as `32k→32k`. A failing regression now proves that same-band
  variants are not growth transitions; all variants across adjacent distinct
  bands are still checked at the unchanged 4,096 workflow-growth threshold.
  Full suite: 381 passed; Ruff/format/compile/diff checks pass.
- 2026-08-25 P4 local-48 independent final review found no P0/P1 and approved
  local/probe training only. It independently verified receipt/predecessor/
  source/SFT hashes, 466 exact-64K rows, zero prompt conflicts and 956 semantic
  growth groups. Same-band variants are ignored; 828 adjacent-band Cartesian
  comparisons all pass at the unchanged thresholds.
- 2026-08-25 P6 started: user requested source→state→answer, live
  OpenReview/arXiv, production unseen signing, a fresh 12-world release, durable
  data management, GitHub sync, and a new private Xnhyacinth HF dataset.
  Complexity 10/10, mixed routing. GitHub auth is active as Xnhyacinth with
  `repo` scope and origin points to `Xnhyacinth/longworld`; HF CLI is installed
  but not logged in. The worktree contains the accumulated user/project changes,
  so Git sync must use an explicit reviewed path set rather than a blanket add.
- 2026-08-25 P6 OpenReview live preflight: both the official v2 API and public
  forum route return the same Cloudflare `Verify you are human` challenge in a
  clean browser session. No challenge bypass was attempted. OpenReview live
  bytes therefore remain blocked pending a human-cleared session; search result
  snippets are explicitly disallowed as source evidence. arXiv and the other
  public-source tracks continue independently.
- 2026-08-25 P6 v4 completed and was independently reviewed: 828 candidates
  produced a 542-row, 12-world local-engineering release with 428/114
  train/eval rows, 16K/32K/64K = 164/222/156, 61 base tasks, 24 executable
  proofs, 23 answer programs, seven real relation graphs, three real source
  families, and 16 real exact-64K rows. The final gate, training export, and
  post-review private stage all bind the same promoted bytes. B5w mixed-weight
  launcher/index coverage and logical-copy validation gaps were fixed; full
  suite 571 passed. The immutable post-review stage is
  `08_hf_private_stage_v2`, inventory SHA-256
  `9bfbda84431a4cd3cad0ad511e2be5b8b05336bdcb3acaa0ffe3e85869533145`.
  GitHub sync remains pending final commit; HF upload remains blocked because
  `hf auth whoami` is not authenticated. OpenReview live source and independent
  production KMS approval remain honest external blockers.
- 2026-08-25 GitHub publication completed: reviewed source, configs, tests, and
  audit records were pushed to `Xnhyacinth/longworld` main at
  `828c0ead4c223469e5d4dba6e91757c2f4b09416`. Generated release data stayed
  ignored. HF remained untouched because `hf auth whoami` returned `Not logged
in`; no private-repository or upload claim was made.
- 2026-08-25 HF authentication later became available and resolved to
  `Xnhyacinth`. Created private dataset
  `Xnhyacinth/LongWorld-Real-Workflows` and uploaded only the 19-file committed
  `08_hf_private_stage_v2` package. Hub commit is
  `32b5dcd274c301300826be20f7a698b4d9b09f7d`; unauthenticated API access returns
  HTTP 401. Remote dry-run shows exactly those 19 files plus Hub-generated
  `.gitattributes`, totalling 260.0 MB decimal.
- 2026-08-25 P7 run 1 started with three disjoint tracks: SEC/Company
  source→state→answer, Wikimedia RealWorkflow materialization, and read-only
  replay/filter performance audit. Added an immutable source-rich profile gate
  requiring all 10 train and 2 eval worlds to contain verified real source;
  profile tests pass.
- 2026-08-25 P7 live Wikimedia acquisition completed: four exact titles yielded
  12 signed records, 12 relations, and four normalized workflows. P7 SEC
  acquisition attempt 1 returned Akamai HTTP 403 at `data.sec.gov`; a changed
  compliant User-Agent diagnostic also returned 403, so no identical retry or
  cache substitution was performed and no partial source file exists.
- 2026-08-25 P7 strict-replay hotspot repaired with a surgical graph traversal
  change: one build/subgraph per `graph_stats` call and single-source shortest
  paths replace all-pairs `has_path` plus shortest-path calls. The required
  failing call-count test was observed (4 builds), then passed at one build;
  8 focused tests and targeted Ruff/format checks pass. Targeted MyPy is deferred
  until the concurrently edited SEC branch settles; its current errors include
  those in the in-progress filing/company files plus inherited missing NetworkX
  stubs, not a runtime failure in this optimization.
- 2026-08-25 P7 SEC source slice v4 completed the full signed local-probe chain.
  One authentic Apple filing workflow produced three staged executable queries:
  16K/32K/64K require 4/5/6 support events, proof depth 4/5/6, and 3/4/5
  replayed hybrid-causal edges. Candidate generation emitted 18 unique rows
  (six per bucket), dense audit accepted 18/18, world-atomic selection retained
  all 18, strict promotion replayed all 18, and the quality gate is green. The
  release has six pinned-token exact-64K rows, zero duplicate/conflicting rows,
  zero boilerplate/pulse, three base tasks/proofs/programs/relation signatures,
  and one unique authentic source workflow. Candidate row-set SHA is
  `5686160f99627e4a9a9a52c6d8c7ade5fd1266a08341b6d105cde03cd19b10f9`;
  promoted row-set SHA is
  `ef06b2f7d76e5590fe514a20cdbfec79a57f518805d9f2aa3af0b9e25fdce597`.
  This is an engineering slice, not the 12-world source-rich release.
- 2026-08-25 P7 integrated validation after the SEC v4 gate: 596 tests pass;
  all 27 changed Python files pass Ruff and format; compileall, `uv lock
--check`, and diff checks pass. Targeted MyPy is clean with missing third-party
  stubs ignored; the environment lacks NetworkX/PyYAML stubs and Bandit is not
  installed, so those two tool-level gaps are recorded rather than hidden.
- 2026-08-25 P7 independent review revoked SEC source slice v4. SourceWorkflow
  queries had included ratification artifacts after the query `as_of`, synthetic
  policy edges had been reported as authentic source relations, and proof
  content grew only 73 tokens per tier while unrelated Company-cycle history
  supplied most added length. The current v4 gate rejects the retained bytes
  with revision/binding/provenance errors and `real_proof_growth_share`
  shortfalls (73 versus minima 837/1053). A local `REVOKED.md` preserves this
  decision beside the historical signed files; none of those 18 rows count as
  qualified data.
- 2026-08-25 Fresh SEC diagnostic v6 verified the future-artifact fix: shorter
  checkpoints no longer contain later ratifications. It emitted 18 candidate
  rows with one honest 8K tier (~10.2K estimated tokens), 32K, and six pinned
  exact-64K rows (~64.1K tokenizer tokens). Ranking/promotion intentionally
  stopped because the nominal 16K tier underfilled and longer tiers were still
  dominated by unrelated Company-cycle history. A `NOT_TRAIN_READY.md` records
  the failure; candidate row-set is
  `aa20490bc35b27962093907a3f71cd92af969de693c357c415978900aa7444cc`.
- 2026-08-25 P7 correctness hardening after revocation: strict replay is v5 and
  release gate v5; P7 requires proof-bearing/supporting content to contribute at
  least 5% of each length increment (minimum 256 tokens). Hybrid SEC edges now
  use real event endpoints plus a separate source-record ID instead of accession
  self-loops. Filing eligibility uses a real body-derived report date and a
  60-day reporting window rather than a filing-date-derived tautology.
  Wikimedia replay records now distinguish Wikipedia CC BY-SA 4.0/GFDL from
  Wikidata CC0-1.0 instead of mislabeling API access policy as content license.
  Focused validation is 211 tests; the preceding integrated suite was 597.
- 2026-08-25 SEC component provenance slice completed. The existing attested
  9.4MB submission now deterministically exposes non-copying coordinates for the
  main 10-K (sequence 1) and EX-31.1/31.2/32.1 (sequences 5/6/7). Every component
  binds the parent source hash, original non-overlapping char range, component
  hash, type, sequence, filename, extractor revision and derived provenance;
  offset/hash/identity tampering fails closed. Ten new focused tests pass. This
  is the parser foundation only; XBRL facts, staged state and answers remain the
  next implementation step.
- 2026-08-25 P7 source-rich profile now requires at least 12 exact real-64K rows
  in each of Company, ResearchLab and CodeForge (36 balanced rows total within
  the existing global minimum of 48). This matches four worlds per domain and
  three core views, preventing a nominally diverse release whose real long rows
  are concentrated in one domain.
- 2026-08-25 Final review closed a remaining scale-accounting loophole: P7 now
  also requires four distinct worlds with exact real-64K coverage in each
  domain. Six view/timing rows from one task can no longer satisfy a domain's
  long-source requirement. Release gate revision advanced to v5.
- 2026-08-25 Final integrated validation: 610 tests pass; all 30 touched Python
  files pass Ruff and format checks; targeted MyPy, compileall, `uv lock
--check`, and `git diff --check` pass. Repository-wide Ruff still reports 26
  inherited findings in untouched `anchors.py`, `cascade.py`, and
  `compose_queries.py`; they were not mass-reformatted in this surgical P7 diff.
  Bandit remains unavailable. Independent review reports no P0. Its two open P1
  items are intentional next-phase blockers: component/XBRL provenance is not
  yet connected to SEC state/answers, and the old short ratification chain cannot
  pass the new material proof-growth gate.

## 2026-08-27 — SEC exact single-world v6 closed

- Parsed exact iXBRL facts, periods, contexts, units/scales, dimensions, and
  Section 302/906 officer certification fields from signed SEC components.
- Derived exhaustive non-overlapping fact corridors and bound every view to
  parent hashes/spans; no copied or randomly concatenated long-text blocks.
- Added executable sales mix, category/current-geography, prior-geography/YoY,
  balance identity, and certification-scope answer programs.
- v1-v5 were retained as explicit `NOT_TRAIN_READY` tuning diagnostics.
- v6: 16 candidate rows, 0 generation rejects; 16 dense audits accepted, 0
  rejects; 16 rows promoted; final quality gate `ok=true`.
- Exact Qwen counts: 16K=16,193, 32K=32,030, 64K=64,255. Essential counts:
  4→7→13→18; graph depth: 2→3→4→5; program ops: 4→7→12→17.
- Candidate row-set SHA-256:
  `9f007ebb7ab40b60f1d7295d2171540d398b6167638f6d1ab52f31184c341cb2`.
- Gate receipt file SHA-256:
  `f6a62f51d9a4eee0d0e047edc0877df5022ce495bfa9d1066a7aae84900cd8aa`.
- Honest boundary: one source workflow, zero authentic cross-record relations;
  12 worlds stays blocked pending workflow/relation diversity, 48/210 blocked.
# 2026-09-02 P14 conversion run

- User confirmed all four prior P13 agents ended with no new 9/9 world. Current
  honest snapshot remains 8 physical / 6 quota / 72 rows and
  `train_ready=false`; formal P13 training export is zero.
- Created a persistent goal for authentic long-context data conversion. The
  new P14 plan replaces the four blocked routes with disjoint Company,
  ResearchLab, CodeForge, and Finance/integration tracks.
- Hard admission gates remain unchanged. Verification is narrowed to
  fail-first tests for changed behavior, candidate-local audits, and one final
  release-chain run after 12/12 rather than repeated whole-repository suites.
- Git status required command-local removal of malformed inherited
  `GIT_CONFIG_COUNT`/`GIT_CONFIG_VALUE_*`; global/user Git configuration was not
  changed. Existing untracked P13 diagnostic configs are preserved pending
  ownership reconciliation.
- Finance P14 added a distinct Microsoft FY2022--FY2025 SEC iXBRL program. The
  first revenue/assets-only version failed the 32K ordered-view 16K-window gate;
  operating-cash observations were made answer-bearing rather than weakening
  the gate. The final histories are 16,119/32,028/64,083 tokens with 8/12/16
  essential rows and 1/2/3 temporal relations.
- All nine Finance task views passed independent dense/source-window replay.
  Candidate/audit/ranking SHA-256 values are recorded in
  `reports/p14_conversion_status_v1.json`. The honest ledger is now 9 physical
  / 7 quota / 81 rows / 3,047,978 tokens; Finance is 2/2 and formal export stays
  zero because the release is still 7/12.
- ResearchLab Adam has 70,674 de-duplicated natural source tokens but needs a
  cross-file section view. CodeForge Wasmtime source lineage is valid, but the
  existing release-summary program remains locally solvable at 8K/16K and is
  not counted.
- CodeForge replaced the locally solvable release-summary task with a
  patch/review/test/release-ancestry program over Wasmtime v45--v48. Its
  1/2/4-cycle histories produce 16,363/32,694/64,842 exact tokens and all nine
  native views passed pinned dense and strict episode replay. This adds nine
  rows and 341,697 tokens; commit `17145f1` records the implementation and
  fail-closed authentic-edge repair.
- The honest ledger is now 10 physical / 8 quota / 90 rows / 3,389,675 tokens.
  CodeForge and Finance are 2/2; Company and ResearchLab remain 0/2. Formal
  selection, promotion, and training export remain intentionally unrun at
  8/12.
- Company Apple stopped at 6/9 because its 16K ordered answer fits one 8K
  window. Berkshire 2021--2024 official PDFs provide 555,529 natural tokens and
  a current source-role inventory; a bounded official-PDF adapter and staged
  non-financial disclosure task are in progress.
- ResearchLab Adam stopped at 6/9 (16K 14,531); GPT-3 revisions have only
  49,623 changed-file tokens. PaLM v1--v5 has 170,869 adjacent changed-file
  tokens and sufficient 16/32/64K stage capacity; a fail-closed file-delta
  receipt and nested file-view task are in progress.
- Berkshire's official-PDF source foundation is now isolated in commit
  `c9d0878`: four signed/replayable reports, 555,529 natural tokens, five
  focused tests, and no failed task code. Its disclosure task remains an
  honest 0/9 at spans 7,837/7,904/8,044 and contributes no quota.
- The formal ledger remains 10 physical / 8 quota / 90 rows / 3,389,675
  tokens. JPMorgan source/task preflight and PaLM compiled-content preflight
  are the active quota-closing tracks; selection/export stays deferred.
- JPMorgan's signed 2022--2024 official-PDF workflow is source-GREEN (3
  records/2 adjacent relations). Its bounded risk-taxonomy/governance plan
  preplays at 16,049/32,134/64,083 tokens with 10/21/43 essential sections and
  ordered spans 13,804/29,889/61,838; Company TDD implementation is active.
- JPMorgan risk-taxonomy is now 9/9 strict-audited: 342,420 tokens, exact
  16,377/32,731/65,032 per view, 10/21/43 essential sections, and 0/1/2 signed
  adjacent-report relations. Commit `b0eafee` records the source adapter, task,
  replay symmetry, tests, and closeout. The honest ledger is now 11 physical /
  9 quota / 99 rows / 3,732,095 tokens; Company is 1/2 and formal export remains
  zero at 9/12.
- Walmart reconciliation is now 9/9 strict-audited: 338,286 tokens, exact
  16,371/32,250/64,141 per view, 5/10/20 essential sections, and 0/1/3 signed
  adjacent-report relations. Commit `ce5e171` records the task and closeout.
  The honest ledger is now 12 physical / 10 quota / 108 rows / 4,070,381
  tokens; Company is 2/2 and formal export remains zero at 10/12.
- Sparks section reconciliation is now 9/9 strict-audited under the current
  trust: 341,709 tokens, exact 16,243/32,338/65,322 per view, 3/5/10 required
  scientific sections, and one signed v5→v4 relation per row. Commit `24f1c23`
  records the task and closeout. The honest ledger is now 13 physical / 11
  quota / 117 rows / 4,412,090 tokens; ResearchLab is 1/2 and formal export
  remains zero at 11/12.
- PaLM's failed file-delta/Adam implementation was fully removed before the
  new compiled-content task began. The new 7/14/27-file plan preplays at
  16,182/32,709/64,846 with every selected file bound to one required claim.

## 2026-09-03 P14 release closure

- Llama 3 section reconciliation supplied the final ResearchLab quota world;
  inventory reached 14 physical / 12 quota / 126 candidate rows.
- Company JPMorgan and Walmart now have cumulative reconciliation computation
  events with replayed proof depth 3/4/5 and direct source-CF parent bindings.
- Selection-v4 and promoted-v4 contain 12 worlds and 108 rows (90 train, 18
  eval; 4,079,561 exact context tokens). The p13 six-domain gate is green with
  108 promotion-ready rows and no errors.
- B1/B3/B5/B5w export validation passed: 11 bound outputs, zero contract
  rejects, and 1.34% equal-token spread. Trust remains local-probe and no HF
  publication was attempted.

## 2026-09-03 P15 kickoff

- Live baseline rechecked: P14 promoted-v4 has 90 train / 18 eval rows and
  4,079,561 exact context tokens; the 126-row inventory is not itself a
  training set. There are no 128K rows in the formal release.
- Started three disjoint candidate tracks for Company, ResearchLab, and
  CodeForge. Root owns profile integration and will run global release gates
  once candidate-local audits finish, avoiding repeated full-suite/release
  validation on unchanged P14 data.

## 2026-09-03 P15 128K closure

- Company failed closed at 0 rows; ResearchLab retained three independently
  audited 128K candidates under a different probe trust; CodeForge uv retained
  six 64K/128K rows under the active workspace trust.
- Added an exact 128K CISA KEV tier and six strict-audited 64K/128K task views.
  The release gate exposed a missing exact evidence-span field in generic task
  projections; the field is now recomputed from causal-gold artifact positions,
  and the rebuilt Cyber views pass without weakening lower-band rules.
- Selection-v5/promoted-v5 passed the original gate but were superseded after
  independent review found candidate-declared evidence-span metadata was not
  recomputed by the auditor.
- Selection-v6/promoted-v6 were then superseded after review showed that a
  re-signed background artifact could be relabeled causal-gold and enlarge the
  recomputed span.
- Final selection-v7 and promoted-v7 contain two worlds and 12 train rows:
  6×64K / 389,283 tokens and 6×128K / 774,639 tokens. Gate is green with
  retention 1.0, zero duplicate/conflicting rows, and mean near-dup 0.019975.
- The auditor now derives span endpoints from the independently replayed
  essential-artifact receipt, requires exact agreement with the causal-gold
  labels, recomputes difficulty metadata under the exact tokenizer, and the
  profile requires both exact 64K and exact 128K.
- The signed B5 export preserves full/CF/ordered views, contains 12 rows and
  1,167,816 estimated tokens, and passed manifest validation. The P15 extension
  is local-probe train-ready but remains production-ineligible.
- Broad-scenario research and two read-only feasibility agents produced a P16
  queue led by BEA spreadsheet vintage, GitHub failure recovery, and Microsoft
  five-year cross-statement programs. Diversity is tracked by executable task
  topology and oracle, not by relabeling domains.

## 2026-09-04 P40 IETF closure

- V14 projected 9 source-bound candidates at exact 32K/64K/128K with
  full/CF/ordered views; all four replay directions and structural preflight
  passed.
- The complete dense audit ran once and accepted 9/9. Candidate/audit digests
  match, dense/BM25/TF-IDF top-3 are insufficient, every 4K/8K/16K raw window
  is insufficient, and maximum near-duplicate ratio is 0.0399.
- Selection, candidate-union, source-bound promotion, train-ready reporting,
  and quality gate completed. The promoted product has 9 train / 0 eval rows,
  682,458 exact tokens, retention 1.0, and `production_eligible=false`.
- B5 export produced 9 examples / 683,635 estimated tokens with zero duplicate
  drops or contract rejects. Deterministic manifest validation returned
  `ok=true` for 9 source rows and 4 outputs.
- TDD fixed the exporter and deterministic validator to preserve optional task
  `query_type` as null. Commits: `0748d29`, `a38e3e0`, `eb5c8c1`, `8fd28db`.
- Six independent local-probe products now total 141 train rows / 7,274,595
  exact tokens: 36×16K, 39×32K, 48×64K, and 18×128K. Production/KMS-qualified
  inventory remains zero; no HF upload was attempted.
- P53 OSV local outputs were independently reviewed and deliberately left
  uncommitted. Two focused tests and hashes reproduce, but authorization is
  invalid for generation and its custom essential-ID/span audit cannot support
  shared remove-one or exhaustive raw-window claims. Inventory delta remains 0.

## 2026-09-05 scale-out kickoff

- Re-audited the current boundary at 141 local-probe train rows / 7,274,595
  exact tokens, including 18×128K; production/KMS-qualified inventory remains
  zero.
- Started three disjoint tracks: authentic-parent rebuild for P52 GovInfo,
  formula-DAG feasibility for P46 Ofgem workbooks, and P54 comparison of new
  official multi-document lifecycle sources. Root retains shared-gate,
  inventory, `.hl`, and release-document ownership.
- Success is measured by executable task/proof diversity and fully verified
  train-ready rows, not candidate count, entity copies, or nominal source
  length. Failed capacity/shortcut probes remain useful rejection evidence.
- Reverted `d9c80b4` with `11c5d66` after confirming that the former P53
  candidate commit contradicted its authorization and overstated custom
  shortcut checks as shared audit evidence. The revert preserves history and
  restores the documented zero-inventory boundary for P53.
- P46 finished at commit `c93efcf`: raw formula-template capacity passes all
  requested bands, but executable answer-dependent capacity is zero, so it
  generated no candidate and ran no unnecessary dense/promotion stages.
  Independent review returned PASS with no blocking or actionable correctness
  finding; the next Ofgem entity is an explicitly reusable public cap-level
  table plus dated decision chain.
- P52 rebuilt three authentic parents and projected nine exact 32K/64K/128K
  views. Shared preflight initially rejected the registered GovInfo v3 sidecar
  because promotion's domain map omitted its adapter. RED→GREEN commit
  `769e635` adds the one missing mapping; valid and wrong-domain tests pass.
- P52 shared preflight and ranking subsequently passed 9/9, but the first 32K
  ordered dense audit failed because an 8K raw window at artifacts 38:47
  retrieves every required answer unit. Inventory remains zero while one
  authentic, structurally separated key combination is tested without moving
  background or changing the gate.
- P52's sole structural-span variant rebuilt from source but failed exact 32K
  before signing (`full=32621`, corrected shared `cf=31000`, `ordered=32621`).
  Commit `af86d64` preserves the authentic-parent builder and both blocker
  receipts. No third key combination, dense audit, promotion, or B5 run was
  attempted; inventory delta is zero.
- Independent P52 review reproduced the 8K shared-artifact blocker and v2
  byte-identical exact-band blocker, then requested owner-level wrapper tests.
  Added a fresh-state three-band nested-pack/sidecar/signing regression and a
  fixed-counter v2 fail-closed regression; P52 plus adapter suite passes 13
  tests. Reporting now distinguishes 9 persisted candidate-stage rows from 0
  dense-audited, selected, promoted, B5, and inventory rows.
- A second review found registered packing had counted the legacy CF JSON form.
  The packer now uses the shared GovInfo counterfactual materializer; v1 counts
  reproduce the shared projections exactly and a fresh source run regenerated
  the v2 blocker and receipt hash with CF=31,000.
- P54 finished at commit `3490181` with a new EUR-Lex MDR legislative-chain
  preflight. It compared NTSB, NHTSA, and EUR-Lex, selected the reproducibly
  frozen EUR-Lex source set, retained 224,955 source tokens, and formed natural
  aggregate 32K/64K/128K packs. Review removed hash-based evidence spreading,
  corrected checked-window counts, and replaced quadratic regex replay with
  precomputed role masks before the commit.
- P54 remains aggregate-only: metadata is a normalized projection, prompt and
  serialization costs are absent, raw-token windows and shared replay are not
  implemented, and a refresh after the successful frozen receipt hit a
  CloudFront WAF response. Candidate/train/inventory counts remain zero and the
  pinned successful receipt was not silently refreshed or loosened.
- Independent review also showed that the five-role remove-one check was a
  role-presence tautology and that the pinned reuse Decision is Commission-
  scoped. The report now marks topology `PARTIAL`, executable minimal evidence
  `UNVERIFIED`, and rights `NEEDS_CANDIDATE_REVIEW`; the corrected metadata
  projection retains only the structurally parsed corrected-by edge.

## 2026-09-06 execution checkpoint

- User requested sustained parallel synthesis and world expansion; three workers
  now execute P54 exact-source oracles, P52 alternative transitions, and Ofgem
  public tables with a subsequent Alphabet entity track. Root owns shared audit
  and inventory integration. No gate/profile relaxation or GPU training.
- Existing trust loader rejected two old private roots because their parent
  directories no longer have mode 0700. Left shared roots and signed products
  unchanged; created separate per-track local-probe roots outside Git through
  the existing initializer, then validated mode/ACL/role isolation with the
  existing loader. They remain local diagnostic trust, not production KMS.
- P52 froze 21 combinations from two actual bills, rejected nine 32K geometry
  cases, and materialized 36 signed candidate parents. These are neither 36
  worlds nor train-ready rows. HR815 has flat necessary support at 64K→128K;
  HR4366 needs a shared-formula proof-growth precheck before dense computation.
- Root projected HR4366 EAS→EAH trial 01 into nine standard candidates and
  verified shared structural preflight 9/9. The candidate file digest is
  `1ba404afdb46f856fadee19128ac6f8cbc96c402b120c3e5da232e8b90b57ae7`.
  Dense/raw-window/selection/promotion remain pending, not implied by preflight.
- P54 rebuilt proposal→act qualification values and an exact metadata adoption
  edge from official bytes. Its first 32K pack exposes a 16K shortcut; preserve
  that rejection and test a different real cross-reference dependency program.
- Ofgem's public table delta is answerable from 483 tokens; three NTSB final
  statuses also have short evidence. Those probes do not produce long tasks.
  Alphabet's current issuer index yielded five genuine annual filing bundles;
  explicit source-parser compatibility is being implemented before parents.
- Actual B5 content/answer identity matches the corresponding product rows via
  the existing four-field training identity. Current B5 spans 14 train world IDs,
  32 dossiers, and seven domain labels; counts remain 80. The P14 B5 reduction
  follows the frozen equal-token ablation export, so it is not a dropped-row bug
  or an invitation to relax that profile to inflate training inventory.
- P52 growth 01 was directly rejected by shared raw-window upper-bound replay
  at ordered 32K offsets 4258:20642. Existing frozen bases 04/05 also failed in
  full/CF. New necessary-request schedules 4→8→16 and 6→12→24 passed 32K
  shared raw proof across all views and exact proof-growth checks. Root's full
  shared signed audit has completed 32K/64K for the first schedule; 128K is
  running. All-M factual labels imply a separate analytical shortcut, so these
  remain diagnostic while P56 creates bounded mixed R/M tasks.
- Alphabet v3 supplies four authentic parent bands and twelve exact standard
  views. The 128K task consumes source-bound segment/geography revenue and
  hedge reconciliation; lower bands exclude these tables. Independent review
  reproduced and then verified fixes for annual-duration, axis/member, and
  hedge-concept substitution bugs. All 48 source/finance tests pass; original
  v1/v2/v3 signed manifests still validate without re-signing. Root's full
  shared signed audits have passed 16K/32K/64K; 128K is running.
- Shared audits use the existing `create_task_dense_audit` function in separate
  CPU-bounded processes by length band, retaining the same candidate, ranker,
  source, and auditor bindings. Partial audit files do not enter inventory;
  full profile selection and promotion remain mandatory.
- P54's first PMS parent is superseded diagnostic evidence pending a corrected
  source-driven Article83 scope and updated selected-span rights coverage.
  Independent review also rejected requiring an unrelated neighboring sentence
  after the complete risk-control list. No P54 train-ready claim is made.
- P73 pilot wave (2026-09-23): counterexample-guided synthesis modules landed
  (mutations/witness audit, shared-world compiler with solve_context provenance,
  answer-contract export, C/D arm split). Bank p73_shared_v1: 588 rows, 42
  worlds, solver recheck 588/588, collapse gate pass, world-level split fixed
  (row-level cut had leaked 1 world). Witness audit: 7/7 families >=81%
  witness-rich; two near-dead mutants recorded honestly (latest_text 0/84,
  tighten_one_condition 1/84). Honest limits: single ~64K length band only;
  C/D contrast weak at 42 worlds. Contract arms: answer-only cuts supervised
  tokens to 14% of full-provenance on the frozen arm_a bank. Training still
  user-gated; see .hl/p73_execution.md.
- P74 wave 1 (2026-09-24, charter .hl/design/p74_real_shared_worlds.md):
  W0 witness three-way metrics landed (latest_text now real: asof 0.964;
  wrong_rule_family honestly 4/84; semantic C/D 360+360, set_complete 0%
  overlap); W1 field-level decomposition (85.9% drop = 89.3% id enumeration
  + 9.7% wrappers + 0.9% scalars, offset-exact); T1+T2 unified semantic
  world + dependency operators with non-foldability gate, span-lineage proof,
  intervention checks, scope-recovery-from-render (demo chain depth 7);
  T4 wiki adapter with a REAL frozen snapshot (Category:Astronomical
  observatories: 15 pages, revids pinned, 196/196 spans verbatim, CC-BY-SA
  recorded) under data/capability_records/p74_wiki_snapshot_v1/. 75 tests
  green. Next: snapshot->world->task wiring, T3/T5/T6/T7; training still
  user-gated. See .hl/p74_execution.md.
- P74 wave 2 (2026-09-24, 11 agents): wiki->world bridge (7 real snapshots,
  zero fact loss), structure-driven task bank (no per-topic code), proof
  certificates (minimal evidence via real re-execution, OR duplicates, W1!=W2),
  length controller (calibrated, no padding), dual-path renderer (cross-
  expression invariance), mutant refresh (wrong_rule_family 4->73/84 via
  direct formula application), 6 new frozen wiki categories (805 spans
  verbatim), p73_shared_v2 32K+64K multi-band bank (8K structurally
  infeasible for multi-family sharing, recorded), support matrix with
  visible infeasible cells, real-world INT demo (fold-gate chains on 6/7
  snapshots, certificates filled). VER adversarial audit found 3 defects —
  all fixed this wave (_map_position coordinate mix, resolve_version $ref
  lineage, as_of_state self-answering degeneracy). 202 tests green. Honest
  milestone verdict: locate+chains pass on real wiki; aggregate/multi_hop/
  as_of skipped on real structure — needs richer sources, not code. See
  .hl/p74_wave2_execution.md.
- 2026-09-24 P75 real-reader slice: reconciled Wiki structural typing across
  demo and support matrix; preserved 1,003 observatory alias-surface mentions;
  exported 21 train / 3 eval local research candidates from five source worlds
  with doc-only reader messages and audit sidecars. The pinned chat-template
  verifier checked 24 masks, 48 exact proof-span token mappings, source splits
  and file hashes; two rows compare named columns across two real Wiki lists;
  train_ready remains false. See `.hl/p75_execution.md` and
  `data/p75_real_reader_candidates_v1/verification.json`.
