# Summary — P7 source-rich integration

## P40/P43 current checkpoint — 2026-09-04

No new IETF row is train-ready. P43's natural 16K/64K successor passes six
dense audits but fails cumulative semantic growth at selection; P38's promoted
64K-only rows failed the lower-band quality gate. Formal inventory remains 132
train + 18 eval rows and 7,274,169 exact tokens. P40 establishes enough
official-source capacity for a genuinely larger OAuth graph, but its five new
answer-changing fields are not implemented or generated yet.

## P17 current conversion — 2026-09-03

P17 adds two signed, independent local-probe training products: Microsoft
Finance 12 rows across 16/32/64/128K and Transformers CodeForge 6 rows across
64/128K. Together they add 18 rows, 1,309,015 exact context tokens, and six
128K rows. The current immutable-product inventory is 132 train + 18 eval rows,
7,274,169 exact tokens, with 15 train rows at 128K. These are diagnostic,
production-ineligible products and were not merged across trust roots. New-
domain research ranks SEC restatement, regression bisect, regulatory
correction, cross-spec propagation, native multi-sheet repair, and authentic
handoff/rollback programs; entity copies on existing operators count only as
volume.

## Superseding status — 2026-08-29

The canonical current truth is `docs/CURRENT_RELEASE.md`. All P7 “green” slice
claims below are historical results under retired gates. The current stricter
content-gated baseline contains 29 rows across four source-bound worlds and
1,120,639 receipt-reported exact Qwen context tokens. It is local engineering
evidence only: the 12-world target was not evaluated, production/KMS-qualified
P12 rows remain zero, and no P12 raw rows are authorized for HF publication.
The only existing private HF payload remains the 542-row P6 local-engineering
release; it is not production-approved.

The sections below are retained as an audit chronology and must not be used as
release authorization.

- Current P12 content evidence is the neutral 29-row/four-world inventory. P6
  v4 remains the only private HF payload; historical P7 slices stay local-probe
  engineering proofs and are not added to either count.
- Amazon 128k is an honest extra band on the existing Amazon world (not a
  new unique 64K world): 24/24 promoted, gate v5 green, packed 14.2k /
  28.9k / 49.4k / 123.4k, 64K wrap 64,174. Gold suffix CF+FX / TAX /
  LEASE / OI. Candidate `f63524b4…`; promoted `d663557f…`. Out-dir
  `data/releases/p7-sec-amazon-128k-slice-v1/`. Do not overwrite the 64k
  slice. Do not copy Apple `>Note 7/8/9`.
- Apple 128k is an honest extra band on the existing Apple world (not a
  new unique 64K world): 24/24 promoted, gate v5 green, packed 12.8k /
  32.9k / 49.6k / 121.1k, 64K wrap 64,854. Gold suffix CF/TAX/LEASE/DEBT.
  Candidate `03bbd5e3…`; promoted `7afaae39…`. Out-dir
  `data/releases/p7-sec-apple-128k-slice-v1/`. Do not overwrite v8.
- Company unique exact-64K worlds are now 2/4: Apple v8 and Amazon
  `0001018724-25-000004` (local `explicit_filings` import; CIK program, not
  Apple headings). Amazon wrap 64,174; 18/18 promoted; gate v5 green.
  Candidate `a21fcdd1…`; promoted `c2001e06…`. Do not start 12 worlds from
  this filing. This host still cannot reach sec.gov. Amazon 128k is green
  on the same filing (CF+FX, Note 4 leases, Note 9 tax axis, Note 10
  segment OI). Company 3/4 needs a third local 10-K.
- Wikipedia Jefferson is now an extra unique exact-64K biography beyond
  ResearchLab 4/4: later `page-29922-r1369059101` + Q11812, 18/18 promoted,
  16K tok 16,183, 32K tok 32,592, 64K wrap 64,915 / 64,921, pack 55,096.
  Candidate `27fb36a5…`; promoted `2a1a3a2f…`. Gold `BORN:1743-04-13` /
  `COMM:Corps of Discovery` / `ENTITY:Q11812` /
  `POP:Autobiography of Thomas Jefferson: 1743–1790`. Replayed graph
  depth is 4/5/6; hybrid 3/6/8. Do not start 12 worlds from this title.
- Wikipedia MLK Jr. is now an extra unique exact-64K biography beyond
  ResearchLab 4/4: later `page-20076-r1370915588` + Q8027, 18/18 promoted,
  wrap 64,504, pack 54,000. Candidate `5bf3678f…`; promoted `e077f35e…`.
  Gold `BORN:1929-01-15` / `COMM:oratorical preaching in Montgomery` /
  `ENTITY:Q8027` / `POP:I've Been To The Mountaintop`. `appendix_rest` was
  in the 64K pool; wrap looked skipped because unbound RFC `source_pack`
  files filled the cap. Drop those from the source-workflow query pool and
  leftover `==== ''The Measure of a Man'' ====` (~6,035 est).
- Wikipedia Obama is a fifth unique exact-64K biography (extra beyond the
  ResearchLab 4/4 quota): later `page-534366-r1371416342` + Q76, 18/18
  promoted, wrap 64,976 / 64,965, pack 49,300. Candidate `0244e4f9…`;
  promoted `4204bc06…`. Gold `BORN:1961-08-04` / `COMM:Madelyn Payne Dunham`
  / `ENTITY:Q76` / `POP:Obama Chooses Biden`. Do not start 12 worlds from
  this title.
- Wikipedia Churchill, Einstein, Thatcher, and Newton remain the four
  ResearchLab exact-64K worlds that would fill that domain quota.
- CodeForge unique exact-64K worlds remain 2/4: P6's 80-episode bundle and
  P7 uv. pulumi `#24184`/`#24226` 16K emits, 64K wrap 58,102 / 58,128 at
  pack 52,500 and still 58,102 after retune to 58,510 (leftover exhausted).
  dprint `#1174`/`#1207` 16K emits, 64K wrap 72,244; `#1215` secret-
  blocked; `#1210` leftover does not fill. deno `#33946`/`#34726`/`#35466` are exported inventory: the CI
  matrix makes required wrap ~70k, so pack retune cannot land exact-64K.
  wasmtime `#12315` exported on `v48.0.0`; patch tags `v48.0.1` diverge.
  ruff `#27170`→`0.16.1` and `#17804`→`0.16.4` are exported (MIT pin
  `9a51688c…`); 16K overflows; 32K emits; 64K wrap is 50,144 (first) /
  66,128 (late) and does not move with the pack cap. oxc still jumps
  55,149 → 71,820. `#33838` has a failing check. `#27766` has `cancelled`.
- Elizabeth II is programmed (unique birth `1926-04-21`, mid `Jallianwala
Bagh massacre`, late `death of Diana`) and fills exact-64K after leftover
  rest_end `==External links==` (~2,033 est). Wrap 64,847 / 64,846; pack
  50,000; 18/18 promoted; gate v5 green. Extra beyond ResearchLab 4/4.
- MLK Jr. inventory is signed (`page-20076-r1370915588` + Q8027) and now
  fills exact-64K. Stalin has no `{{birth date}}` template. Jefferson is
  green (unique birth `1743-04-13`, mid `Corps of Discovery`, late
  autobiography title, leftover Foundation sources, pack 55,096). Extra
  beyond ResearchLab 4/4; do not start 12 worlds from this title.
- Scale unit remains `N_proof × N_task`, not copies of one world × views ×
  lengths. Next task expansions on existing green worlds (new out-dirs,
  do not overwrite): Amazon `sec_filing_eligibility` 16k
  (`configs/p7_sec_amazon_eligibility_slice.yaml`); uv
  `release_supersession_trace` (`configs/p7_uv_supersession_slice.yaml`).
  Do not add a fourth domain. Company 3/4 still needs a third attested 10-K.
  CodeForge 3/4 still needs uv-class leftover fill; `psf/requests` v2.34.x
  cited PRs are Ada-class.
- FDR (~44k), Napoleon (~46k), and Queen Victoria (~32k) are unique births too short for 64K.
- Ada/Turing remain unique and too short. Attention `1706.03762` is signed
  semantic-delta, not 64K. Company is 2/4 (Apple + Amazon). 48/210 remain
  blocked. OpenReview remains 403.
- Pipeline: dense ranking now loads MiniLM on CPU; `retune_pack_target_for_exact_64k`
  converts observed Qwen wraps into the next pack cap. Generated JSONL stays
  gitignored. Do not upload P7 slices as train-ready.

- Wikipedia Newton completed candidate→dense-audit→selection→promotion→
  gate v5 on one later revision (`14627-r1371274988` + Q935): 18 rows,
  6/6/6 at 16K/32K/64K, exact-64K wrap 64,959, proof-bearing growth
  12,680 then 23,985 tokens, authentic relations 0, hybrid signatures 3
  (edges 3/5/10). Candidate row-set `50202f59…`; promoted `5f24fa4f…`. Gold is
  tagged BORN/COMM/ENTITY/POP from body claims; CF birth year +1. Duplicated
  `{{Birth date|df=y|1643|01|04}}` is bound via a unique infobox wrapper.
- Wikipedia Thatcher remains a separate unique exact-64K proof (wrap
  65,417; candidate `4bf6b695…`; promoted `b3916422…`).
- Wikipedia Einstein remains a separate unique exact-64K proof (wrap
  64,982; candidate `eee8896c…`; promoted `0c04fa82…`).
- Wikipedia Churchill remains a separate unique exact-64K proof (wrap
  65,073 / 65,131; candidate `a31b7d1a…`; promoted `577644a0…`).
- Ada Lovelace and Alan Turing are unique workflows with source→state
  programs, but their later bodies cannot fill exact-64K (Ada 16K/32K only;
  Turing wrap 57,125). Do not pad them. Hopper/Johnson remain fail-closed.
- Attention `1706.03762` is now a signed unique paper workflow: v1→v2 binds
  authentic revision-added semantic LaTeX (277-char parameter-attention
  sentence), not the Aviva/ONR/NSF funding regex. The manuscript is ~21k
  estimated tokens, so it is not an exact-64K world.
- P6's 80 GitHub episodes remain one CodeForge world. uv is a second unique
  exact-64K world; leftover unused PRs from the P6 bundle still cannot each
  fill wrap. urllib3/aiohttp/pytest/fastapi/ruff/pydantic release-cited PRs
  scouted here are Ada-class (tens-to-hundreds of added lines).
- P7 SEC v4 stays revoked. v6 remains a not-train-ready diagnostic. v8 remains
  the Apple 10-K engineering proof (wrap 64,854; candidate `8579b9f8…`;
  promoted `0fb120ae…`). This host cannot reach `data.sec.gov` or `www.sec.gov`
  (Akamai 403 undeclared automated tool). Do not retry. Company expansion is
  local `explicit_filings` import of Archives `.txt` copied from an allowed
  network, plus a CIK-keyed financial program (only Apple `0000320193` is
  programmed; other issuers fail closed).
- Validation at this checkpoint: Obama and uv one-world gates v5 green;
  Elizabeth program tests pass; pack-retune and CPU MiniLM ranker tests pass.
  Generated JSONL stays gitignored. Do not upload P7 slices to Hugging Face
  or GitHub as train-ready.

## 2026-08-27 — SEC semantic-lineage correction and exact-single v8 rerun

The prior exact single-world bytes passed numeric-offset replay but an
independent review found that a role label and `params.text` could be changed
together and re-signed while retaining the original answer. Two fail-first
tests reproduce this with Products→Services and Americas→Services. Semantic
replay now compares the complete source event params with the immutable world
event, permits only explicit query-owned CF overrides, checks the raw
parent-derived section hash, and suppresses all fact spans on a mismatch.

Fresh exact-single v8 completed candidate, dense rank, strict audit, selection,
promotion, and the quality gate: 12 rows, 12 audits accepted, zero rejects,
exact 16,193/32,030/64,255 Qwen-token contexts, and no duplicates or conflicts.
The data bytes remain deterministic, but the earlier v7 receipt is not accepted
as evidence for this code revision and v7 is marked `NOT_TRAIN_READY`.

This is still one Apple filing, one base task, three staged answer programs,
and zero authentic cross-record relations. The single-world engineering gate
is closed; the source-rich 12-world gate is not. Do not start 12, 48, or 210
from this result alone and do not upload it as the multi-world release.

## 2026-08-27 — independent raw-span review invalidation

The preceding v8 conclusion is superseded. Exact source-span replay found the
16K MIX task solvable inside one 4K window, so v8 has zero qualified long-context
rows. Promotion now binds the approved Qwen model/revision, verifies the loaded
commit, recounts the reconstructed prompt, and keeps exact-token evidence span
separate from chars/4 metrics. SEC source replay now rejects duplicate,
foreign, malformed, or non-monotonic-offset inputs; certification metadata must
have complete body spans; XBRL member duplicates fail closed.

The next single-world run is still blocked as a data release: current SEC views
are roughly 97.5% HTML/iXBRL markup; Amazon current/prior geography is
interleaved and its former 64K proof includes a fact-free essential artifact;
authentic cross-record relations remain zero. A v9-preflight4 attempt emitted
no rows because the original external probe role keys were absent. Restore the
real probe trust root or rebuild a newly validated source bundle; never invent
a replacement key for an existing signature. Do not run 12/48/210 or upload any
SEC slice yet.
