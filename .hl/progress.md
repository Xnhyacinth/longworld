# Progress

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
