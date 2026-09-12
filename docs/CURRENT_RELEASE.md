# Current release status

Last updated: 2026-09-12

This file is the canonical publication-status summary. Historical receipts and
`.hl/` logs remain useful for reproducibility, but they do not override this
status. The canonical project root is `/workspace/wynckeliao/longworld`;
project code, source inventory, generated data, release receipts, reports, and
durable progress records must live there. Tool caches may be reconstructed
outside the repository, and credentials must remain outside Git.

## 2026-09-12 sync: P64 product on Hub, P65 adapters on GitHub

LongWorld **training rows** for the current P64 primary set are on private
`Xnhyacinth/LongWorld-Real-Workflows` at
`local-probe-train-ready/p64-primary-training-v2/` (Hub `30f4a0194bad`, 1953
train / 533 eval jsonl; sizes match the local snapshot). They are
`local_training_eligible` and **not** `production_eligible`. Synthesis
intermediates for P64/P65 (candidates, signed source inventories, retained
filings) belong on `Xnhyacinth/LongWorld-Synthesis-Workspace`; HMAC keys stay
off Hub and Git. There is still **no** P64/LongWorld 256k full-SFT checkpoint
(the 4-GPU 256k run OOM'd after step 1).

P65 taskbank adapters (CodeForge reading-proof, finance disclosure versions,
GovInfo HR4366) landed on `main` from the worlds worktree without probe-trust
files or report jsonl dumps. P65 candidates are synthesis intermediates, not a
new Real-Workflows product.

## 2026-09-11 P64 4B-Base SFT (not a LongWorld product release)

ACC and LongTrace already have held-out val sets (32 rows each), so the signed
P64 eval split stays eval and is **not** folded into train. Local product is
`data/sft/p64_primary_training_v2/` (finance+codeforge: 1953 train / 533 eval;
max chat tokens 261,954; Hub copy under
`Xnhyacinth/LongWorld-Real-Workflows` `local-probe-train-ready/p64-primary-training-v2/`).
`production_eligible` remains false.

The run reuses the latest 4B-Base ACC/LongTrace ms-swift recipe on GPUs 4–7
only (GBS 16, SP=4, DP=1, micro=1, accum=16, lr 1e-5 cosine, 680 steps, FA2+FLA,
DeepSpeed none, packing off, `SKIP_HOLD=1`). Cutoff is native 262144 so those
rows are not truncated. Related-work Base ACC/LongTrace checkpoints stay 128k
baselines, not this product. Config: `configs/swift/ext_p64.yaml`.

## 2026-09-07 128k related-work baselines on private Hugging Face

ACC / LongTrace / LongMIT 128k SFT jsonl was **not** on Hub (private
`Xnhyacinth/LongWorld-Real-Workflows` remains the P6 542-row LongWorld product
and was left unchanged). A new private parquet dataset now holds the valid
ms-swift 128k baselines:

- Dataset: [`Xnhyacinth/longworld-128k-sft-baselines`](https://huggingface.co/datasets/Xnhyacinth/longworld-128k-sft-baselines) (private)
- Hub commit: `72eecdd3f8774399c75c9b7ba1aa1d5e00453246`
- Configs: `acc`, `longtrace`, `longmit` (each `train` + `validation` parquet)
- Rows: ACC 10770/32, LongTrace 2783/32, LongMIT 10770/32

Latest valid full-SFT checkpoints (step 680, inference weights only;
optimizer/RNG omitted):

| Condition | Private model | Local run | Train / eval loss |
| --- | --- | --- | --- |
| Base ACC | [`Xnhyacinth/Qwen3.5-4B-Base-ACC-128k-SFT`](https://huggingface.co/Xnhyacinth/Qwen3.5-4B-Base-ACC-128k-SFT) | `swift_ext_acc_base` ckpt-680 | 0.437 / 0.375 |
| Base LongTrace | [`Xnhyacinth/Qwen3.5-4B-Base-LongTrace-128k-SFT`](https://huggingface.co/Xnhyacinth/Qwen3.5-4B-Base-LongTrace-128k-SFT) | `swift_ext_longtrace_base` ckpt-680 | — / 0.177 |
| Instruct ACC | [`Xnhyacinth/Qwen3.5-4B-ACC-128k-SFT`](https://huggingface.co/Xnhyacinth/Qwen3.5-4B-ACC-128k-SFT) | `swift_ext_acc` ckpt-680 | 0.365 / 0.375 |

Hub commits: Base ACC `2b4dae0e6c3cddeaf6dea4e07d9ec1b31a250ddb`, Base
LongTrace `734d5a13211cdcf7a524d283e2fc982592cb09d9`, Instruct ACC
`ca665f6df393b31414b1ff2c286a3dcb8997bd9f`. Base recipe is GBS 16, SP 4 DP 1
micro 1 accum 16, lr 1e-5, max_length 133120, 680 steps. Instruct ACC used 8
GPU SP=4 DP=2. LongMIT is data only; it was not trained on 4B-Base this
round. Instruct LongTrace stopped at ckpt-200 and is not published. These
artifacts are related-work baselines, not LongWorld product rows;
`production_eligible` is unchanged.

Private collection (do not merge the repos):
[`Xnhyacinth/longworld`](https://huggingface.co/collections/Xnhyacinth/longworld-6a9eb196a0c8cd67190ea7fd).

## 2026-09-07 P57 local-probe products and synthesis delta

Three independently signed local-probe products were appended under
`local-probe-train-ready/` on `Xnhyacinth/LongWorld-Real-Workflows` (Hub
commit `cf28fcf07501768e54ecaee2ef132c11951ce192`). The P6 542-row payload
and its attested `release_inventory.json` were not rewritten.

| Product | Rows | Exact tokens | Bands |
| --- | ---: | ---: | --- |
| `p57-ietf-tls13-handshake-succession-probe-1-v1-promoted-v1` | 6 | 581,651 | 3×64K + 3×128K |
| `p57-finance-nvidia-market-segment-probe-1-v1-promoted-v1` | 12 | 728,085 | 3 each 16/32/64/128K |
| `p57-finance-micron-asset-trajectory-probe-1-v1-promoted-v1` | 12 | 733,444 | 3 each 16/32/64/128K |

Content-gated SFT inventory is now **ten** independently signed local-probe
products: the previous seven plus these three. Together they contain
**183 train rows / 10,045,500 exact Qwen context tokens** and **18 eval rows /
682,032 tokens**. Total local inventory is **201 rows / 10,727,532 tokens**
across 20 unique world IDs. Train length distribution is 45×16K, 48×32K,
60×64K, and **30×128K**. `production_eligible` remains false.

P57 TLS uses profile `p57-ietf-tls13-64k-128k-extension-probe-1-v1` (one-world
standards gate, length-view pair exemption). NVIDIA and Micron reuse
`p17-finance-128k-extension-probe-1-v1` on new issuers and separate probe
trust roots. HTTP/3 QUIC remains a candidate track, not a product.

Synthesis intermediates (candidates, signed source inventories, retained
filing/RFC bytes, and the three product trees) were appended to private
`Xnhyacinth/LongWorld-Synthesis-Workspace` (Hub commit
`4bc492923e26f30aff93ef7064992a46c15eea6e`; 368 P57 files / ~383MB). HMAC
keys stay off Hub and Git. Optimizer/RNG training states were not uploaded.

## 2026-09-06 Alphabet conversion and GovInfo six-relation hold

P55 Alphabet passed the final quality gate and signed B5 export validation.
The current content-gated SFT inventory is therefore **seven** independently
signed local-probe products: the previous six plus
`p55-finance-alphabet-asset-breakdown-probe-1-v1-promoted-v1`. Together they
contain **153 train rows / 8,002,320 exact Qwen context tokens** and
**18 eval rows / 682,032 tokens**. Total local inventory is
**171 rows / 8,684,352 tokens** across 17 unique world IDs and seven domains.
Train length distribution is 39×16K, 42×32K, 51×64K, and **21×128K**. The
separately signed B5 samples contain **92 examples / 5,757,426 estimated
tokens**. Product rows and B5 samples remain different bound sets.
`production_eligible` is still false; production/KMS inventory is still zero.

Alphabet is one finance world (`finance_alphabet_asset_breakdown_2021_2024_v3`)
with 12 train rows / 727,725 exact tokens (3×16K, 3×32K, 3×64K, 3×128K). It
reuses `finance.multi_filing_asset_trajectory.v1` on a new issuer and a
separate scaleout-20260906 probe trust root. B5 validation returned
`ok=true` for 12 source rows and four bound outputs under
`longworld-llamafactory-sharegpt-v4`. The signed product is on private
`Xnhyacinth/LongWorld-Real-Workflows` under
`local-probe-train-ready/p55-finance-alphabet-asset-breakdown-probe-1-v1-promoted-v1/`
(Hub commit `02b4885f40b83495dcc4bf7dfb3ad4c9569b33ea`). Details are in
`reports/p55_finance_alphabet_training_conversion_closeout_20260906.md`.

P56 GovInfo mixed-01 still has nine row-level promoted rows and **3**
authentic relation-set identities, so the immutable
`min_real_source_relations=6` gate does not issue a release receipt. Mixed-03
passed shared dense audit and a shared-world regeneration produced 18 audited
rows with all six identities, but selection fail-closed on `duplicate_cells`:
one world may occupy each band/view cell only once, so two 3-band schedules
cannot satisfy both the 6-identity gate and `expected_promoted_worlds=1`.
Hashes and thresholds were not changed. See
`reports/p56_govinfo_six_relation_union_path_20260906.md`. P54 EUR-Lex PMS
remains a 32K-only research projection (three views, one relation-set ID) and
is not inventory. P46 Ofgem remains a short-window rejection.

## 2026-09-04 train-ready local-probe inventory

The current content-gated SFT inventory consists of six immutable signed
products: P14 six-domain, P15 CodeForge/Cyber 128K, P16 Macro 128K, P17
Microsoft Finance 128K, P17 Transformers CodeForge 128K, and P40 IETF OAuth
semantic growth. Together they contain **141 train rows / 7,274,595 exact Qwen
context tokens** and **18 eval rows / 682,032 tokens**. Total local inventory is
**159 rows / 7,956,627 tokens** across 16 unique world IDs and seven domains.

Train length distribution is 36×16K, 39×32K, 48×64K, and **18×128K**. P40 adds
9 train rows / 682,458 exact tokens: three each at 32K, 64K, and 128K, with
full/CF/ordered views. Its release gate and signed B5 manifest pass with zero
duplicate drops or contract rejects. These products use separate local-probe
trust roots and must not be represented as a single signed release. They are
valid for local diagnostic training, but remain `production_eligible=false`;
no new HF upload was performed.

P17 also has a second BEA entity in strict replay and an executable six-domain
design queue. Pending candidates do not count in the numbers above. Details and
the diversity accounting are in
`reports/p17_training_conversion_closeout_20260903.md` and
`reports/p17_domain_design_matrix_20260903.md`.

## 2026-09-05 authentic-world scale-out checkpoint

This scale-out added no train-ready row. The current immutable-product inventory
therefore remains **141 train rows / 7,274,595 exact tokens** with 36×16K,
39×32K, 48×64K, and 18×128K. The separately signed B5
training-condition samples contain **80 examples / 5,021,465 estimated tokens**:
15×16K, 19×32K, 28×64K, and 18×128K. Product rows and B5 samples are
different bound sets and must not be added together. Production/KMS inventory
is still zero.

Three independent source-first tracks were resolved without relaxing any gate:

- P46 Ofgem has 469,328 template-deduplicated Qwen tokens across three official
  workbooks, but zero persisted cell closures, output receipts, or replayable
  cross-workbook answer changes. It emitted zero candidates.
- P52 GovInfo rebuilt three authentic full parents and persisted nine projected
  candidate-stage rows across 32K/64K/128K. Projection, preflight, and ranking
  passed 9/9, but the first shared 32K ordered audit found the complete answer
  inside one 8K artifact window. Its sole approved structural-key alternative
  emitted zero rows because `full=32621`, `cf=31000`, and `ordered=32621` cannot
  all satisfy the 32K exact band under the current deterministic packer. Thus
  shared dense-audited, selected, promoted, B5, and inventory counts are zero.
- P54 selected a new EUR-Lex `public_law` source set covering proposal,
  first-reading position, adopted regulation, corrigendum, metadata, and a
  reuse decision. Its six frozen official representations retain 224,955
  near-deduplicated authentic-source tokens;
  whole-artifact aggregate packs reach 32,277 / 64,285 / 128,392 tokens without
  padding, cloning, splitting, or truncation. They are not candidate exact-band
  receipts because prompt/serialization costs, exact metadata spans, registered
  replay, and exhaustive raw-token windows remain outstanding. Only the
  corrected-by edge is structurally parsed; target-answer reconstruction is
  `UNVERIFIED`, topology is `PARTIAL`, and the Commission-scoped reuse Decision
  leaves selected-document coverage at `NEEDS_CANDIDATE_REVIEW`. P54 therefore
  also has zero candidates and zero inventory delta.

The next permitted P54 work is to bind exact relation spans and selected-
document reuse coverage, then build a fresh source-span parent and registered
task-replay adapter before candidate-level exact-band and raw-window checks.
Parallel discovery should change entity and answer program:
a naturally dispersed GovInfo bill transition and an explicitly reusable Ofgem
public cap-level revision table are preferred. The rejected P46/P52 layouts
must not be repaired through padding, background movement, cloning, truncation,
or gate relaxation. Detailed evidence is in
`reports/p46_ofgem_formula_dependency_gate_closeout_20260905.md`,
`reports/p52_govinfo_bill_disposition_registered_parent_closeout_20260905.md`,
and `reports/p54_eurlex_legislative_chain_preflight_closeout_20260905.md`.

## 2026-09-04 IETF semantic-growth closure

P40 v14 is the first IETF task to complete the full local-probe chain. It uses
official RFC bytes and Datatracker relations, and adds a late RFC 9700 reverse-
proxy header-sanitization dependency after the bearer-token evidence. This
fixes the earlier minimal-counterfactual and first-16K ordered-window shortcuts
without changing near-duplicate, exact-band, derived-view, raw-window, or
truncation gates.

All nine 32K/64K/128K full/CF/ordered candidates passed exact replay, preflight,
dense ranking, exhaustive 4K/8K/16K raw-window checks, selection, source-bound
promotion, the release quality gate, B5 export, and deterministic manifest
validation. The signed product is
`data/releases/p40-ietf-oauth-semantic-growth-probe-1-v1-promoted-v14`.
Its B5 export contains nine examples / 683,635 estimated tokens and preserves
all three views at all three bands. The formal evidence and hashes are recorded
in `reports/p40_ietf_oauth_semantic_growth_conversion_20260904.md`.

The prior P33/P43 attempts remain negative controls: the 64K-only construction
lacked a valid lower band, and the 16K/64K construction lacked semantic growth.
P50 Treasury remains capacity-blocked at sub-4K gold chains. P52 GovInfo now
has a registered source-bound replay adapter, but its old nine candidates are
not promotable and must be rebuilt from three authentic full parents before
shared proof. P53 OSV remains an uncommitted geometry diagnostic: it reused a
P51 authorization that prohibited generation, and its custom essential-ID and
span checks are not exhaustive shared remove-one/raw-window proofs. None of
these partial tracks contributes inventory rows.

## Published private dataset

- Repository: `Xnhyacinth/LongWorld-Real-Workflows` (private)
- Hub commit: `32b5dcd274c301300826be20f7a698b4d9b09f7d` (latest seen product
  path SHA `02b4885f40b83495dcc4bf7dfb3ad4c9569b33ea` for P55 Alphabet)
- Release profile: `p6-source-dependent-probe-12-v1`
- Scope: local engineering only; not production-approved
- Rows: 542 total (428 train, 114 eval)
- Length labels: 164 at 16K, 222 at 32K, 156 at exact-tokenizer 64K

The remote contains 19 release payload files plus the Hub-managed
`.gitattributes`; the payload matches the local immutable P6 v4 staging package.
No P6 payload is missing; no P12 production upload is authorized.

Related-work 128k SFT baselines live in a **separate** private dataset,
`Xnhyacinth/longworld-128k-sft-baselines` (parquet). Do not overwrite
`LongWorld-Real-Workflows` with ACC/LongTrace/LongMIT.

## 2026-09-01 task replay and baseline checkpoint

LongWorld model accuracy remains **unmeasured**: no LongWorld-trained checkpoint
has yet been evaluated under a fixed baseline protocol. The current local
inventory is 3,047 content-audited CPT rows / 288,004,845 exact Qwen tokens and
12 promotion-v2 SFT rows / 2 worlds / 454,846 tokens. Production/KMS-qualified
rows remain zero.

Cyber and Finance now emit closed, source-role-signed task replay sidecars,
portable v2 replay registries, and signed per-world/per-band candidate content
commitments. Fresh Cyber v7 and Finance v4 16/32/64K candidates were
dense-ranked and produced 6/6 local-probe diagnostic auditor receipts. Those
receipts recompute source/body/relation bindings, full/minimal/CF,
remove-one/single/empty replay, artifact-aligned 4K/8K/16K checks, BM25/TF-IDF,
dense top-3 insufficiency, and full-pool strict replay. Final review established
that artifact-boundary enumeration is not an exhaustive tokenizer-offset
sliding-window proof. The receipt now labels that scope explicitly and leaves
the generic local/contiguous-window and no-shortcut fields false. Consequently
all six candidates remain `train_ready=false`; task promotion is blocked before
the later 12-world and independent production-trust gates. Candidate-declared
proof fields are now forbidden rather than ignored. Training-message identity
is recomputed from `context+answer` at preflight, audit, selection, promotion,
report, quality-gate, and export boundaries; metadata-only clones and prompt
conflicts fail closed. Strict support counts come from adapter replay, and
candidate relabeling cannot create causal-supporting growth. The final scoped-v5
audit digests are `edd1c9cb2c566dce45707da23342baa47c7b83f4ea96a9f09e493302d94fefbd`
for Cyber and `899f846e374f7092c4b68484aecff7fc4be5ff00857cb0bdb96e99c692247b2a`
for Finance; both record `global_proof_green=false`. Deno's pinned rerun
retained only three 64K views at 65,496 tokens each; 16/32K were correctly
rejected for `strict_support_overflow`.

Task audit now signs a v2 semantic commitment covering replay/growth plus every
stable field consumed by diversity and real-source quotas: motif, base task,
answer program, executable proof, semantic task, source family/workflow/token
ratio, and source relation identity. World selection binds each candidate to
that commitment; task promotion requires the signed selection; the train-ready
report recomputes the commitment from the actual row. Promotion-role re-signing
therefore cannot manufacture semantic or source diversity.

The signed LLaMA-Factory training path now consumes a post-validation, private
content-addressed snapshot rather than mutable export paths. Snapshot
materialization streams and rehashes every manifest-bound output, preserves
relative paths, publishes only after all outputs pass, and rejects replaceable
or foreign-owned temporary parents. Swift snapshot wiring is present, but Swift
v2 intentionally remains fail-closed until it has its own executable
deterministic transform validator. A signed tokenizer digest is checked before
and after a fresh load, but production still requires the resolved tokenizer
asset snapshot itself to be mounted read-only or isolated from the audit
process; local write access is not a production trust root.

The final post-fix repository regression is **1,558 passed / 1 expected
xfail**; focused tests, Ruff, MyPy, compileall, Bash syntax, and
`git diff --check` also pass.

The evidence, exact hashes, baseline-scale comparison, and evaluation matrix are
recorded in `reports/p12_baseline_promotion_scaleout_20260831.md`. This progress
does not authorize a new HF training release; 48/210 remain blocked behind the
12 complete source-bound world gate and independent production trust.

## 2026-09-01 eligibility closure and Macro scale-out

The two eligibility flags are now treated as outputs of different closed
contracts rather than mutable release metadata. A row can become `train_ready`
only through source-bound selection, strict replay, signed promotion, report,
quality gate, and deterministic export. A release can become
`production_eligible` only after a current production profile, nonempty ready
unseen splits, independent KMS approval, an immutable inventory, and an
atomically finalized `COMMITTED` package all verify.

Four official-BEA Macro revision worlds now contribute 12 strict-audited
16/32/64K candidates. Their contexts are restricted to the target economic
series; all four 64K artifact sets are pairwise disjoint. Construction,
verified packing-cache replay, and independent audit reject cross-series
prefixes using the serialized observation bodies. All 12 adapter audits pass,
including CF/remove-one, raw-token windows, dense top-3 insufficiency, and
full-pool strict replay. They remain candidates because Macro still needs the
standard full/CF/ordered training projections and a unified 12-world release
selection.

Production packaging also now recomputes the canonical SFT row contract instead
of trusting top-level eligibility mirrors. Production unseen evaluation rejects
nonissuable profiles, blocked requested axes, and empty train/eval outputs. Its
input row set and original train/eval row sets are now bound to the auditor-
signed release gate, and any production failure removes partial split outputs.
The full status, immutable hashes, and exact closure sequence are recorded in
`reports/p13_eligibility_scaleout_20260901.md`. The complete code regression
passes **1,563 tests / 1 expected xfail**; six subsequently added attack/failure
regressions pass in the current 18-test Macro and 189-test production-focused
suites.

## Godot raw-proof capacity and BEA source inventory

The complete Godot allowlisted history scan retained **41 standalone audited
CPT rows / 1,370,439 exact Qwen tokens**: 23×16K, 11×32K, 4×64K, 3×128K,
and 0×256K. Targets were capacity ceilings, so no row was copied or padded.
The independent report replayed all 40 source manifests, 39,499 commits in the
contiguous approved-license suffix, raw commit/tree/blob path proofs, exact
token counts, source-event/span/truncation gates, and the reconstructed
training export. It found zero exact context/source-body duplicates and zero
cross-band source-record/event overlap. The retained data contains 965 unique
commits, 2,232 records, 41 windows, and 24 accepted base workflows.

The first audit correctly rejected a manifest that counted one repository as
one workflow instead of counting row `base_workflow_id` values. The producer
was fixed and the release rebuilt from 40 authenticated extraction checkpoints;
the final signed audit passes. See
`reports/p12_godot_raw_replay_bea_20260831.md`.

The historical local-probe verification root was recovered outside the
repository with mode `0600` and independently replayed the five-release pandas
closure. Godot was then freshly re-imported with the current v6 extractor rather
than reusing the unverifiable v7 signatures. The fresh 40 source manifests bind
remote request/receipt identity, commit/tree/blob proofs, the approved-license
boundary, and the complete 39,499-commit approved suffix. Recursive subtraction
against the five-release closure retained all 41 rows; the final report replayed
five references and found zero context, source-body, or source-event overlap.
The Godot dedup release is now the local content-audited closure anchor, bringing
the inventory to **3,047 rows / 288,004,845 tokens**. It remains
`train_ready=false`, `production_eligible=false`, and local-probe only.

A live fixed-URL BEA GDP/GDI workbook fetch bound 74,180 raw bytes to 3,959
cell-provenanced observations, 3,567 temporal relations, and 384 trajectories
of depth 3–16. Of those relations, 2,935 are answer-changing revisions and 632
are unchanged-value supersessions. Macro is now an explicit third task adapter
beside Cyber and Finance. Its real 2005Q4 GDP path produces nested 16/32/64K
contexts with 5/10/15 vintages, 9/19/29 essential artifacts, proof depth
5/10/15, and 3,672/7,815/11,968 event-bearing tokens. Exact document lengths are
16,000/32,000/64,016 tokens; all three rows pass fresh source/state/answer, CF,
remove-one, window, lexical, dense top-k, and strict replay checks. Source,
packing, and reference caches are separately signed by source/promotion/report
roles and accelerate a repeated local materialization from about 60.5 to 14.6
seconds without restoring any pass conclusion. The combined-key executable is
hard-limited to local-probe diagnostics; production KMS and the 12-world gate
remain unmet, so no HF upload is authorized.

## Longitudinal 64K/128K CPT candidate

The new longitudinal successor contains **1,000 64K rows and 1,000 128K rows**
with **192,731,120 exact Qwen context tokens**. Every row now contains multiple
distinct real commits: observed minima are 10 at 64K and 23 at 128K, with
71,294 non-reused commit events and 280,183 non-reused source records across
eight public repositories. The independent audit verified 99 signed source
manifests, recomputed all lengths, reconstructed all 2,000 training rows, and
found zero context, used-source-body, source-record, event, or cross-band
overlap. See
`reports/p12_cpt_git_history_longitudinal_1000x2_20260830.md`.

This remains a local-probe **CPT candidate**, not executable SFT or 2,000
worlds. It is `train_ready=false` and `production_eligible=false`. The event
gate removes the earlier single-large-commit failure mode, but it does not yet
require a release boundary or answer-changing dependency; median calendar spans
are 2.17 days at 64K and 5.04 days at 128K, and one 64K row has zero elapsed
calendar time because distinct commits share a timestamp. A later full review
found two such 64K rows. No HF publication is
authorized from this candidate.

## Multiband Git-history CPT capacity candidate

The shared bulk pipeline now supports registered exact 16K, 32K, 64K, 128K,
and 256K bands instead of hard-coding only 64K/128K. A capacity scan over the
previously unused 5,748-commit tail of the Transformers first-parent history
naturally retained 31×16K, 31×32K, and 29×256K rows before cross-release
subtraction. Three 256K rows contained source bodies already used by the prior
64K/128K candidate and were removed as whole rows.

The final disjoint increment is
`p12-cpt-git-history-multiband-transformers-tail-capacity-v3-dedup`: **31 16K,
31 32K, and 26 256K rows**, totaling **8,168,552 exact Qwen context tokens**,
4,345 non-reused real commits, and 12,609 non-reused source records. The
independent audit recomputed lengths and exports, verified source/CPT/report
attestations, reloaded the referenced old release, and found zero cross-release
source-body, event, or context overlap. No band hit its 1,000-row safety cap, so
these non-round counts are observed source capacity rather than balanced quotas.

This is still a one-repository local-probe CPT candidate with no executable
answer program and no production trust. It remains `train_ready=false` and
`production_eligible=false`; it is recorded as a real, independently audited
increment, not published training data.

The next five-band capacity wave over Flask, scikit-learn, and DuckDB is now
materialized, union-subtracted, and independently audited. Its raw 547 rows lost
ten complete rows to signed-source-body overlap with earlier releases. The final
`p12-cpt-git-history-multiband-diverse-wave1-capacity-v2-dedup` increment retains
102/105/111/109/110 rows at 16/32/64/128/256K respectively: **537 rows and
54,383,671 exact Qwen tokens** from 26,113 non-reused commits and 82,423 source
records. The audit recursively replays two pinned prior releases and finds zero
remaining source-body, event, or context overlap.

Across the six mutually disjoint current longitudinal/multiband CPT releases,
the local candidate inventory is now **3,047 rows and 288,004,845 exact context
tokens**: 323×16K, 264×32K, 1,115×64K, 1,112×128K, and 233×256K. The earlier
1,000-row 64K/128K groups are configured quotas; the remaining counts are filtered
source capacity. The newest Bitcoin/pandas 381-row increment alone has the explicit
minimum-span and truncation-quality replay gates; older rows must not be described
as having passed those two later gates.

This does not yet establish broad semantic diversity. The new wave is 51.4%
scikit-learn, 45.1% DuckDB, and 3.5% Flask; 84.9% of its rows span less than 30
days, and only 1.1% span at least one year. It proves real chronological,
multi-commit, non-copied CPT contexts, not answer-changing long-range dependence.
All five releases remain `train_ready=false` and `production_eligible=false`.
Godot remains allowlisted future capacity; Bitcoin and pandas are retained in the
newest 381-row audited increment.

## Earlier source-native long-document CPT candidate

The v2 Git-history CPT batch now contains **1,000 64K rows and 1,000 128K
rows**, totaling **192,776,946 exact Qwen context tokens**. Its independent
local-probe audit recomputed every token count, verified 17 signed source
manifests and 259,861 used source records, reconstructed all 2,000 training
rows, and found zero cross-band record reuse, zero exact context duplicates,
and zero exact source-body duplicates. Construction rejected 11,153 repeated
source bodies, 7 credential-shaped commits, and 90 QA/chat-contaminated
windows. The old v1 batch is explicitly invalidated because its distinct IDs
hid 11,146 repeated source bodies. See
`reports/p12_cpt_git_history_1000x2_20260830.md`.

This is a real-source long-document **CPT candidate**, not 2,000 worlds or an
executable SFT set. It remains `train_ready=false` and
`production_eligible=false`: it uses two code repositories and combined local
probe trust, lacks per-historical-commit license verification and semantic
near-duplicate review, and has no answer/CF program. More importantly, 569 64K
rows and 518 128K rows are dominated by one large authentic commit. They are
coherent source documents but do not establish long-time workflow evolution.
The next longitudinal batch must require multiple distinct commits/release
cycles per window before it can support the stronger LongWorld claim. No HF
publication is authorized from this candidate.

## Current stricter-gate result

The current promotion-v2 local-probe corpus contains **12 rows across 2 unique
source-bound worlds**, with **454,846 exact Qwen context tokens**. The MLRC and
Attention revision workflows each contribute two views at 16K, 32K, and 64K,
so the combined band distribution is 4/4/4. Fresh dense ranking, strict replay,
world selection, promotion-v2 reporting, and the unchanged one-world quality
gate accepted every row. The exact union inventory is
`data/releases/p12-current-v2-source-bound-union-v1.json`; it records 6 unique
executable proofs, 6 answer programs, 18 exercised program operations, 12
distinct content hashes, and 454,846 exact context tokens.

This is still a narrow ResearchLab/arXiv local-probe subset, not a complete P12
release. Its union inventory explicitly records `production_eligible=false`,
`target_gate_evaluated=false`, and `target_gate_passed=false`; the 12-world
multidomain boundary and independent production trust are both unmet.

The previous local content-gate corpus covered **35 rows across 5 worlds** and
1,347,609 exact tokens, but those rows predate the mandatory
`train-ready-promotion-v2` replay-growth contract. They remain historical
regeneration inputs rather than current qualified rows.

The historical schema-v2 neutral inventory is
`data/releases/p12-current-five-source-bound-union-v1.json`. It verifies five
distinct world IDs, 35 distinct canonical content hashes, source-workflow
ownership, tokenizer/bucket metadata, release-file hashes, and the union row-set
digest `a1b0abd5c2f478c63a17b7ed4fb4b55bc8b9f5d5da18c9786ed3b3ca5b4de680`.
It reports `inventory_integrity_ok=true`, but deliberately reports
`target_gate_evaluated=false`, `target_gate_passed=false`, and
`production_eligible=false`: five worlds do not satisfy the 12-world profile.
The six-world inventory and Jefferson/Newton v5 receipts are retained as
diagnostic evidence but are excluded from current-state accounting after
independent semantic review found no value-level dependency on revision deltas.

The local-probe root supports role-separated engineering replay, while every
combined-role artifact is cryptographically labeled
`non_independent_local_diagnostic`. The corpus is current-code content evidence,
not independent production trust. Production/KMS-qualified P12 rows therefore
remain zero, and no raw promoted JSONL is authorized for HF publication.

Wave 3 expanded real-source diagnostics without producing a new complete world. Ruff
produced only a valid 16K group, Deno only a valid 64K group, and the four-revision
Megatron-LM workflow reached roughly 50K rather than the exact 64K band; all
failed world-atomic preflight before dense promotion. RFC 9421 now has 20 draft
revisions, a signed 22-record/20-relation bundle, and a standalone byte-bound
state→answer→CF/remove-one replay contract. Its real 18→19 diff does not contain
a unique single-keyword normative replacement, so actual task retention remains
zero and it is not a training world. NVD+CISA, Federal Register+Regulations.gov,
and ClinicalTrials+openFDA now produce three source-attested executable task
candidates with real answer/CF/remove-one replay. Together they bind 9 official
records, 8 relations, and 55,640 source-body Qwen tokens, but they are explicitly
ignored non-world candidates with zero training rows. See
`reports/p12_executable_domains_strict_growth_20260830.md`; none of these
diagnostics is qualified or uploadable training data.

The first cumulative-domain materialization added one real CISA KEV catalog
task at 16,201/32,125/64,125 exact tokens. The three strict prefixes contain
82/159/314 unique records, 81/158/313 authentic catalog-membership relations,
and 80/157/312 verified-derived chronological edges. All per-row replay, actual
CF, remove-one, single-evidence, corruption, digest, exact-band, and cumulative
growth checks pass. These are deliberately unsigned candidate histories with
`real_source_verified=false`, `complete_world=false`, and `train_ready=false`;
they add one executable task, not three worlds or training rows. This historical
candidate-history snapshot is superseded by the source-signed v7 pipeline and
its 3/3 task audits summarized above. Clinical and
Regulation remain capacity-rejected at 5,167 and 748 source-body tokens. See
`reports/p12_domain_history_wave1_20260830.md`.

A new dprint diagnostic binds nine public episodes, 502 records, and 546,115
source-body characters. It produced two 16K and three 64K candidates, but the
32K attempt reached only 30,361 tokens with insufficient evidence distance and
local-window shortcuts. Complete-world retention is therefore zero and dense
promotion was not run. See
`reports/p12_codeforge_dprint_patch_early_20260830.md`.

The candidate preflight now enforces cumulative source-bound 16/32/64K history
before dense ranking. Bands must share one semantic growth identity and grow in
event-bearing/internal content, strict support, essential events, proof depth,
and nested authentic relations. `internal` is the inclusive workflow-owned
total and `event_bearing` is its subset; the gate does not add them together.
Real-source-derived causal evidence counts;
an unrelated real hard negative does not. This is a structural check over
signed candidates, not a substitute for independent strict replay.

No invalidated P7/P8/P9 row is currently qualified for upload. SEC exact-single v8 and its
derived Apple/Amazon slices were invalidated after exact raw-span replay found
a 4K shortcut and after readable-text normalization showed that more than 97%
of the former statement views were HTML/iXBRL markup. Historical green receipts
for those slices prove only that the older gate ran; they are not current
release authorization.

The readable SEC source layer now derives state from normalized visible filing
text while retaining reversible raw-source coordinates. This makes the existing
single-filing answer programs real and replayable, but their proof-bearing text
is naturally too short for the advertised long buckets. Longer SEC samples must
therefore add real filings, amendments, release cycles, and cross-record
relations instead of markup or background.

Strict SEC semantic replay now regenerates every section event from the trusted
workflow record and requires the complete canonical params to match. Consistent
event/artifact tampering of raw coordinates and quotes, XBRL fact identity, or
parent provenance therefore fails even when the modified artifact is re-signed.
The regeneration cache is keyed by the actual source hash and all source metadata;
it stores and returns deep copies rather than aliases to mutable event params.

Amazon's current/prior geography table is year-interleaved. The readable-state
adapter now prevents prior-period roles from leaking into the current-period
stage and fails closed by omitting the unsupported 64K/128K programs. Those
bands remain disabled until a row/period-aware view supplies genuine new
evidence.

Wikipedia/KB now uses visible source sections and authentic signed relations in
state, factual/CF answers, remove-one checks, raw-token windows, dense replay,
and signed promotion. The earlier Thatcher and Newton diagnostic slices were
rejected because Thatcher had no exact 64K stage and Newton had no valid lower
band. The replacement Newton multiband workflow described below supersedes that
diagnostic result; Thatcher remains unqualified.

The Pulumi GitHub workflow has been regenerated under the current local-probe
identity and passes the cumulative-answer and cross-band content gates.
Production required-check policy binding and independent trust remain absent.
OpenReview remains diagnostic. One arXiv revision-chain
world passes locally. The issuer-owned Amazon four-filing workflow now supplies
the first Company content-qualified world; accession-pinned SEC multi-filing
retention remains absent.

P10 has one content-gate-passed Wikimedia world under retired local-probe trust in
`p10-wiki-jefferson-semantic-v16-promoted`. The earlier Jefferson v4 receipt and
the intermediate v6-v15 runs are revoked. Review found a 4K natural-language
shortcut and two synthetic copy rungs in v4; v6/v7 predated the final window
enumeration and serialized answer-program fixes. V10 also serialized the two
authentic Wikimedia relation directions backwards; the strict source-metadata
replay now rejects all 12 of those rows instead of silently overwriting them.
V12 was rejected because enumerating fields in the prompt made the 16K support
set overflow. V13 removed length/control-stage language and answer-disclosure
markers but left the response grammar implicit; V14 put the grammar in the
prompt and again overflowed 16K. V15 places the value-free response schema in
the existing evidence-review event, making the output format executable without
adding a background block. V16 resolves multiple accumulated reviews by asking
for the most inclusive schema. It splits one authentic
Jefferson revision body into non-overlapping chronological sections and makes
the 16K answer depend on four real spans: birth, early career, the revolutionary
committee, and the diplomatic transition. It then adds the commemoration,
entity relation, and popular-culture evidence at 32K and 64K. No copy event is
part of the proof, and this is not revision-history gold.

The current replacement candidate→dense ranking→strict audit→world
selection→promotion→quality chain retains 16 rows (4/6/6 by band) with zero
generation rejects, clones, exact duplicates, or prompt conflicts. Proof depth
grows 2→3→4, essential events grow 5→10→14, and authentic relations grow
0→2→3. At 64K the third relation is the exact current→prior `revision_of`
edge; its prior endpoint is an exact API revision-ID span rather than a repeated
copy of an unchanged article section. Exact 4K/8K/16K source-span
windows are replayed with the pinned tokenizer: 16K requires 4K/8K
insufficiency, while 32K/64K require all three windows to be insufficient. The
serialized answer programs now include all body-fact roles and the 32K/64K
source-relation verification operation. The signed one-world gate receipt is
green under gate revision v6. Exact Qwen counts are 16,367, 32,644, and 64,232
tokens per view, totaling 646,724 context tokens across the 16 rows. This is a
content-gate-passed current local-probe slice, not authorization for the
12-world release or an HF publication.

P11 adds two independently identified worlds without reusing Jefferson's world
identity. `p12-wiki-newton-current-probe-v5-promoted` uses a stable
Wikidata-derived seed (`14627`) and retains 12 rows (4/4/4 by band), with exact
counts 16,096, 32,680, and 64,885 tokens and 454,644 total context tokens. Its semantic answer
program grows from five early-life/scientific source spans to the optics,
Wikidata-entity, Royal Mint, and prior-revision lineage evidence. Authentic
relations grow 0→2→3. All dense, CF, remove-one, source
relation, raw-window, semantic-growth, and gate-v6 checks pass; generic
background is zero.

`p11-paper-mlrc-multiband-v13-promoted` retains 6 rows (2/2/2 by band) from
three public arXiv source revisions, with exact counts 16,352, 32,556, and
65,498 tokens and 228,812 total context tokens. The executable proof grows
4→5→6 essential events and 0→1→2 authentic revision relations. At 16K, both
v1 and v2 contain the same authentic `main.tex` source family, and the answer
requires proving that only v2 adds the acknowledgements include before reading
the exact disclosure. Actual selected source paths and byte spans are bound into
the v2 provenance: `main.tex` must exist in both revisions, the marker must occur
in v2 `main.tex` only, and the answer span must occur in v2
`acknowledgements.tex`. Moving the marker to another selected file, omitting the
shared main file, or corrupting the marker makes admission/replay fail. The 32K
and 64K programs add the signed v2→v1 and v3→v2 revision edges. The former paper
v10/v11/v12b receipts and Newton v5 receipt are revoked because they reused a
world identity or predated these path-bound/multi-workflow isolation checks;
paper v11 also had an asymmetric 16K file-family shortcut.

The CodeForge UV v3 run produced 9 real 64K candidates and strict audit accepted
all 9, but the final quality gate correctly rejected every semantic group with
`real_64k_missing_lower_band`. Those rows are diagnostic only. Relabelling the
roughly 43K shorter views as 32K or weakening the growth gate is prohibited; the
next CodeForge run must add a genuinely shorter real release-cycle task at 32K
and extend it with real CI/history at 64K.

Independent CodeForge review also found and fixed a monorepo release-lineage
bug: prefixed tags now retain a strict normalized family, so `crates_v*` and
`napi_v*` cannot be joined as one supersession chain. This correctness fix does
not promote Deno, Ruff, or Oxc; their incomplete-band and retrieval failures
remain unchanged. A later dprint patch-history run does qualify independently
below.

Four earlier Wikimedia API probes produced 8 page revisions and 4 Wikidata
revisions, but only two page revisions were new relative to the existing local
archive. Jefferson/Newton v5 consumed only a self-contained prior revision ID
as the `revision_of` endpoint: the old body/delta did not enter state or the
answer, and coordinated relation-content forgery could preserve the gold answer.
Those structurally green receipts are therefore revoked and excluded. A future
revision world must make an independently parsed cross-version fact or delta
change the answer and must bind both relation endpoints to source bytes.
Thatcher, MLK, and the
pre-retiering Jefferson/Newton runs remain diagnostic failures.

The SEC multi-filing probe identified AMD 10-K
`0000002488-26-000018` and 10-K/A `0000002488-26-000021` for the same report
date, but all three bounded official-download paths returned SEC 403 responses.
No filing bytes were materialized, signed, synthesized, or counted. Online SEC
acquisition still needs an accession-pinned receipt before this workflow can
run.

The P9 implementation now preserves record identity when two workflow events
carry identical text, while the context selector rejects duplicate protected
source bodies by global content digest, including across repositories, instead
of counting them twice. Counterfactual rewrites of SEC or repository text are
classified as synthetic children with parent lineage and do not contribute
real-source tokens or authentic endpoint relations. Exact-token bands also use
the pinned tokenizer and explicit query boundary for every emitted view's
length, position, evidence distance, local span, and dependency class during
generation and independent promotion replay.

Exact token-coordinate replay now recounts only proof-essential artifact
boundaries plus the complete rendered context. This removes the former
two-tokenizer-calls-per-document scaling path while preserving the same exact
coordinates and independent full-context recount. It reduces filtering cost,
but it does not relax any release gate or unblock 48/210 before the 12-world
source-dependent receipt is green.

Strict bands now bind the byte-level tokenizer snapshot manifest, not only a
model name and revision. Generation, independent replay, and the final gate
freshly hash before and after tokenizer loading, so a shared-cache mutation
fails closed. The current Qwen manifest covers tokenizer config, vocabulary,
merges, tokenizer JSON, chat template, and model config; the same manifest
format supports Llama SentencePiece layouts. The production package still must
bind the tokenizer implementation class/backend and dependency-lock digest;
asset binding alone does not claim that a Llama release was run or that the
production trust upgrade is complete.

Production packaging must also carry allowlisted source replay sidecars (or an
immutable digest-addressed provenance archive) and bind the dense model
snapshot, implementation backend, and dependency lock. Until that contract is
implemented, the production-packaging-ready allowlist is empty and package
creation fails closed even after approval/gate verification. The local v16
source bundle is replayable, but it is not yet enclosed in a production package.

## P12 expansion checkpoint

P12 adds deterministic source-materialization caching, digest-addressed mixed
bundle lookup, and world-parallel strict audit/promotion with deterministic
output ordering. Tests compare cached/parallel decisions and bytes with the
uncached/serial path; these optimizations do not skip replay or weaken a gate.
Production packaging now verifies a separate package approval that binds the
release inventory, training manifest, and exact `COMMITTED` bytes. Final
production issuance remains disabled until the two-phase stage, independent KMS
signature, and finalization flow also admits and re-verifies that sidecar.

The Amazon issuer-IR v4 workflow binds issuer-linked annual-report PDF, XBRL
ZIP, and rendered-XBRL HTML artifacts for 2021--2024. The exporter verifies
artifact roles and actual XBRL issuer/CIK/form/report-period identity. Company
state is reconstructed from 57 exact facts per year across 23 non-overlapping
rendered-XBRL sections and three authentic adjacent-filing relations.

The v10 candidate→pinned dense ranking→strict audit→world
selection→promotion→quality chain retains **9 rows** (3/3/3 at 16K/32K/64K)
with zero generation or audit rejects. Exact Qwen counts are 16,183, 32,658,
and 64,964 per view, totaling **341,415 context tokens**. Necessary events grow
23→43→83, proof depth grows 3→4→5, authentic filing relations grow 1→2→3,
and event-bearing source content grows 9,081→19,229→50,228 tokens with zero
generic background. The answer-role program is cumulative: a two-year common
program plus a latest-year revenue extension becomes a three-year program,
then a four-year program with an earliest-year accounting-policy extension.
Those extensions are declared in the question schema and bound into the
executable program identity; CF revenue replay changes every applicable answer.
The maximum near-duplicate ratio is 0.1803, below the unchanged 0.25 ceiling.
All 9 external dense receipts report top-k insufficiency, and the signed
one-world gate-v6 receipt is green. These are **content-gate-passed rows under
retired local-probe trust**, not
a production package or HF publication authorization. The local promoted
`train.jsonl` byte digest is
`e530737b4b6869488807d025adb52c60dfa97efd8d7f792886f837d924dd08f9`;
the signed gate-receipt byte digest is
`34ae90bdbe10516fce772b8bf7790bf7714c652e411b53aeb4a4f7d7611600eb`.

The current P12 inventory therefore does not yet reach 12 qualified worlds.
ResearchLab has 42 strict-audited candidate rows across four worlds
(1,589,428 exact Qwen context tokens). The former Pulumi six-row local result is
superseded because its 32K answer named only one release while carrying the
prior release closure as hidden evidence. The corrected CodeForge program uses
a single-cycle 16K answer, a cumulative two-cycle 32K trace, and a cumulative
three-cycle 64K trace. It preserves every real GitHub workflow record and
causal link, while executable release closure binds only direct final pre-merge
CI gates and real release supersession; PR/review/merge history remains source
context instead of being mislabeled as an answer prerequisite.

The historical v13 candidate retained **8 rows**: full/CF at 16K and
full/CF/ordered views
at 32K and 64K. The omitted 16K ordered view truthfully failed the 8K evidence
distance requirement at 3,964 tokens. Exact Qwen counts are 16,375, 32,665, and
65,301 per retained view, totaling **326,648 context tokens**. Essential events
grow 3→128→253. Authentic source relations grow 10→262→514 on the full path
(8→260→512 on CF); total context relations, including hybrid world relations,
grow 10→263→516. External dense
ranking and strict replay accepted 8/8 with embedding top-k insufficiency, and
the final gate-v6 receipt is green with zero duplicates, prompt conflicts, or
promotion-contract errors. The promoted `train.jsonl` digest is
`d5b3bc700d53ee9faaf2bf38032c535f3c9c0e4fa1418fbaf0f5f4de7d026adf`;
the gate receipt digest is
`20acd2654698f8fed1f58bed4c120604c842f0f5a6fb3d69044fdd1c6094bf49`.

After the question was corrected to describe observed selected final pre-merge
checks rather than a verified historical branch-protection policy, fresh
GitHub exports exposed a real parser bug: `status=completed` shadowed the
body-visible terminal `conclusion=success`. A fail-first regression now gives
terminal conclusion precedence. The replacement v17 content-diagnostic chain
then retained **8 rows** (2/3/3 at 16K/32K/64K), accepted 8/8 pinned dense
audits, promoted 8/8 strict replays, and passed the unchanged one-world release
profile. Event-bearing tokens grow 10,344→20,346→43,532, source relations grow
10→263→516, and generic background remains zero. Its 324,378 exact Qwen
context tokens restore the CodeForge share of the current per-world
content-gated baseline. The train-row digest is
`72ffa2ff0343e2df905394631cfa6e7c0b61b8430a301fb1246f3a7ce97f366b`;
the gate-receipt digest is
`3d45285f4b11840766f0053b5f9068706c555423669ea040caa3402bcb916156`.
Every promoted row, report, and gate explicitly records
`content_gate_eligible=true`, `trust_valid_for_production=false`, and
`production_eligible=false`. Combined-role candidate/audit/promotion/report/gate
artifacts carry the signed isolation marker; role-separated dense rankings carry
the probe ranker identity without claiming combined-role isolation. The
superseded v16 release lacks these explicit trust-boundary fields and must not be
used. The retained `verification.production_mode=true` field names strict replay
strength only; publication/export code must require
`trust_valid_for_production is true` and must not infer trust from
`data_stage=train_ready`, gate `ok`, or replay mode.

The second paper world re-fetches and source-signs the authentic Attention Is
All You Need arXiv v1/v2/v3 bodies and two revision relations. Its final5 chain
uses a unique stable world identity and retains six exact rows (two each at
16K/32K/64K; 226,034 tokens). Authentic revision relations grow 0→1→2,
graph-replayed proof depth grows 2→3→4, necessary events grow 4→7→10, and
event-bearing tokens grow 15,944→32,398→64,175. All candidate/dense/audit/
promotion rows pass; the one-world release gate is green and an independent
review found no unresolved must-fix. Candidate and strict replay now share one
fail-closed arXiv chain validator, so a missing prior edge, arbitrary third
input, wrong relation type, or disconnected chain cannot inflate growth. The
final train digest is
`3f445d6cf95e8c7b27e9a321e073eacd587f163ada5d0faef87fb12f68dcee15`;
the gate-receipt digest is
`54bb66a05fdfea852d7caeff3ec4dc5c2d1392df8b8825e48e838e62ebddd667`.
OpenReview API v1/v2 and the forum page returned access challenges, so no review
record was scraped or simulated as authentic source; the fetch observation is
an unsigned operator ledger, not source evidence.

The replacement dprint patch-history world binds five authentic release/PR/CI
episodes (0.53.1, 0.53.2, 0.54.0, 0.55.1, and 0.55.2): 233 records, 317 links,
and 119,812 exact Qwen source-record tokens. It contributes six promoted rows,
two per 16K/32K/64K band and 226,970 total exact context tokens. Strict support
events grow 14→28→42, graph-essential events grow 12→24→36, authentic
source-relation edges grow 24→48→72, total context relations grow 24→49→74,
replayed proof depth grows 2→3→4, and event-bearing tokens grow
15,855→32,212→63,930 with zero generic background. All six rows passed dense
audit, strict replay,
counterfactual, remove-one, text-corruption, window, BM25, embedding top-k,
promotion, and the unchanged one-world release gate. This remains a
`local_probe` content result, not production trust. NVIDIA
FY2022–FY2025 issuer acquisition likewise remains source discovery only because
the issuer detail page returned a challenge and the downloader failed closed.
Microsoft FY2025 now has issuer-owned GCS bytes, a signed source manifest/bundle,
12 parsed sections, 27 exact XBRL roles, and four certification components. The
GCS outer-page component envelope, raw/sanitized sidecar replay, parser/status
trust binding, interleaved current/prior fact projection, real filing timestamps,
and declared/active bucket fail-closed checks are implemented and independently
replayed. A fresh three-band candidate run materializes 16/32/64K but retains
**0/21 attempts**: 16K evidence distance is short, 32K has exact/window/pack
failures, and 64K contains only 25,233 real tokens with 9,887-token ordered
distance. Microsoft is therefore validated source capacity and a 0-row
diagnostic, not training data. The current Amazon rows are unaffected because
they use four real annual issuer filings rather than the single-filing facet
timeline.

A follow-up Microsoft adjacent-annual probe binds the FY2023 and FY2024 issuer
filings, their exact revenue facts, and one grounded `prior_annual_filing`
relation into the answer. Six distinct 32K rows passed dense and strict semantic
audit, CF, remove-one, corruption, window, BM25, and embedding checks. The world
still has **zero qualified rows** because exact 16K, 64K, and 128K are absent.
Two three-revision Wikimedia probes likewise remain excluded: Churchill emitted
only four 64K rows, while Jefferson emitted four 16K and four 64K rows but no
32K. Jefferson's historical one-world profile receipt is invalid for Wave 2
because that profile did not require all bands. The new immutable
`p12-wiki-source-slice-1-v1` and `p12-current-source-probe-12-v2` profiles
require exact 16K/32K/64K coverage for every world at selection, train-ready
reporting, and final quality-gate layers. The source-free diagnostic record is
`reports/p12_wave2_rejected_source_scaleout_v1.md`.

Current code supersedes that 32K diagnostic with a canonical nested annual
program: two, three, four, and five filings map to 16K, 32K, 64K, and 128K,
respectively, and each higher tier consumes one additional exact XBRL revenue,
one grounded adjacent-filing relation, and the preceding answer. The available
two-filing Microsoft inventory now emits six unique 16K candidates at 16,339 or
16,344 exact Qwen tokens. Structural preflight rejects them before dense ranking
because 32K/64K/128K are absent. Thus the rerun saves replay work but still adds
zero qualified rows; three, four, and five real annual filings are required for
the higher tiers.

Candidate-only histories and failed/superseded rows are not a new `COMMITTED`
production package and are not eligible for HF upload. The current-code
promotion-v2 baseline is **12 rows, 2 unique worlds, and 454,846 exact Qwen
context tokens**. It has not passed the 12-world union profile, covers only one
domain/source family, and its local-probe receipts are not a production KMS
chain. Trust-valid P12 publication therefore remains zero; 48/210 remain
blocked.

The finance cumulative-history adapter now materializes one additional
candidate-only Amazon 2021--2024 annual-report task at 16,082 / 32,482 / 64,207
exact Qwen tokens. The bands add 2/3/4 filings, 18/27/36 essential fact rows,
and 19/29/39 answer-bearing source relations. Executed CF, per-essential remove-one,
digest-consistent semantic corruption, exact-span replay, and cumulative growth
all pass. Shared dense ranking produces 3/3 signed local-probe diagnostic audits,
including explicitly scoped artifact-aligned windows and auditor-recomputed
growth metrics. These are not generic token-offset window proofs, so task
promotion is blocked. The later 12-world release selection and independent
production-trust gates also have not run.

Replay data, source bytes, releases, and pinned model caches remain under
`/workspace/wynckeliao`. The active role-separated local-probe trust root is now
persisted outside Git at `/workspace/wynckeliao/.longworld-private/` with
0700/0600 permissions and inherited ACLs removed; it is execution authority only,
not production KMS trust. Reproduction still requires a locally
resolvable model snapshot whose fresh manifest equals the signed digest. No new
HF dataset upload is part of this cycle; code and release-status synchronization
use GitHub only after final review.

## Current expansion matrix

| Evidence stage                         | Complete worlds | Rows/candidates | Exact context tokens | Meaning                                                                         |
| -------------------------------------- | --------------: | --------------: | -------------------: | ------------------------------------------------------------------------------- |
| Current promotion-v2 local-probe union |               2 |              12 |              454,846 | Strict current-code rows; arXiv/ResearchLab only; 12-world target not evaluated |
| Longitudinal Git-history CPT candidate |               0 |           2,000 |          192,731,120 | Eight repos; 71,294 non-reused commit events; local CPT candidate only          |
| Historical pre-v2 union                |               5 |              35 |            1,347,609 | Regeneration inputs only; obsolete replay-growth schema                         |
| CISA KEV task-audited candidates       |               0 |               3 |              112,895 | One source-signed local-probe task × three bands; 3/3 audit, not selected       |
| Amazon finance task-audited candidates |               0 |               3 |              112,771 | Four issuer-owned filings; 3/3 local-probe audit, not selected                  |
| dprint incomplete candidate history    |               0 |               5 |              229,180 | 16K/64K only; rejected because the complete 32K band is absent                  |
| P12 production/KMS release             |               0 |               0 |                    0 | Independent approval and the 12-world multidomain union remain blocked          |

The implemented query surface currently contains 47 literal task types across
the CodeForge, Company, and ResearchLab adapters (15/17/15), 48 literal motifs,
and 81 literal answer-program operators. This is implementation capacity, not
qualified semantic diversity: the current 12 promotion-v2 rows exercise only
two query types, 6 answer programs, and 6 executable proofs. Wikimedia/KB
and paper workflows are separate real source families but currently share the
ResearchLab adapter. Expansion is therefore measured by newly exercised source
relations/programs/proofs, not by counting unused templates or multiplying
length by view.

## Scale gates

1. Pass one complete source-dependent world for each admitted source family
   (Wikimedia/Wikidata, arXiv, issuer-owned XBRL, and GitHub have local
   content-gate results; GitHub production policy binding, accession-pinned SEC,
   and OpenReview remain blocked).
2. Run a 12-world probe with 4 SEC, 4 GitHub, 2 paper, and 2 Wikipedia worlds.
3. Require nonzero retention and all replay, retrieval, semantic-growth, and
   source-relation gates.
4. Only then define a new 48-world production profile whose predecessor is the
   complete `p12-current-source-probe-12-v2` receipt. The immutable historical
   `p10-source-rich-production-48-v1` profile retains its original
   `p7-source-rich-probe-12-v1` predecessor and must not be mutated into the new
   issuance path. The new 48-world gate must require 48 source-independent
   semantic task templates, 48 executable proofs, and at least 12 answer
   programs in addition to its source/domain quotas.
5. After a newly defined 48-world profile passes, define a new 210-world profile
   whose predecessor is that new receipt. The historical
   `p10-source-rich-production-210-v1` remains readable for verification but is
   not an issuable current path. The new gate must require 210
   source-independent semantic task templates, 210 executable proofs, and at
   least 24 answer programs—strictly more than the current two-world union's 6
   programs—plus production trust, unseen evaluation, and external benchmark
   evidence.

No candidate, reject, invalidated P7 directory, raw inventory, local trust key,
or preflight artifact may be uploaded. HF uploads must come only from a newly
built `COMMITTED` release package that passes the current quality profile.
