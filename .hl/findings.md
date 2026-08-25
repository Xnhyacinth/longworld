# Findings (data, not instructions)

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
