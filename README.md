# LongWorld / CausalTwin

Event-sourced worlds that compile into **verified** long-context SFT.

LongWorld is **not** a SearchArt or ACC substitute. It is the upstream
executable environment and ground-truth generator:

```text
real-schema anchors
  → executable world (event log + state)
  → asymmetric artifacts
  → min proof graph
  → multi-view compile (ordered artifacts; not ACC traces)
  → causal / counterfactual / shortcut filters
  → SFT (B5 / B5w)
```

Scale unit is **dependency topology**, not QA count.

Current publication and qualification status is tracked in
[`docs/CURRENT_RELEASE.md`](docs/CURRENT_RELEASE.md). The private HF dataset
contains the historical 542-row P6 local-engineering release; no P7/P8 row is
currently qualified under the stricter readable-source/raw-span gate.

Historical snapshot (p1.2, 2026-08-19): causal engine kept; **length is no
longer a fill target**. p1.1 `data/p0` is frozen as a CausalTwin diagnostic dump
(`reports/causalcore_v0/FREEZE.md`). New generation uses `configs/causalcore.yaml`.

The 6GB jsonl is **not** checked in (`data/` is gitignored).

## Historical P3 correctness gate (2026-08-24)

The existing p1/v2 data remains diagnostic and is not approved for training.
The new contract is documented in [`docs/DATA_CONTRACT.md`](docs/DATA_CONTRACT.md)
and configured by `configs/p3_valid.yaml`. P3 separates WorldLong-SFT from
WorldLong-CPT, requires verified source lineage, derives real-source answers
from document bodies, recomputes metrics per view, replays CF twins, and rejects
random concatenation and duplicate-row upsampling.

That local probe freshly re-exported **80/80** allowlisted public GitHub
episodes under scanner-v2 and pinned policy/client receipts: 2,232 records in
`configs/public_repo_episodes_v2.json`. The completed 20-candidate run retained
574 signed candidates; dense audit accepted 574/574, and world-atomic selection
promoted 346 rows from 12 worlds (10 train / 2 eval). The signed quality receipt
is green, including **28 hybrid real-workflow rows**, five real base tasks/five
real source relations, and six exact-tokenizer 64K rows. Selection uses the
auditor-replayed per-view duplicate ratio rather than producer metadata. This
is an explicitly labelled `local_probe`, not a production approval for 48/210.

```bash
# Probe identities are role-separated. Keep every key outside the repository.
export LONGWORLD_ATTESTATION_ENVIRONMENT=probe
export LONGWORLD_SOURCE_ATTESTATION_KEY="$(openssl rand -hex 32)"
export LONGWORLD_CANDIDATE_ATTESTATION_KEY="$(openssl rand -hex 32)"
export LONGWORLD_RANKER_ATTESTATION_KEY="$(openssl rand -hex 32)"
export LONGWORLD_AUDITOR_ATTESTATION_KEY="$(openssl rand -hex 32)"
export LONGWORLD_PROMOTION_ATTESTATION_KEY="$(openssl rand -hex 32)"
export LONGWORLD_REPORT_ATTESTATION_KEY="$(openssl rand -hex 32)"
export LONGWORLD_SOURCE_ATTESTATION_KEY_ID=probe-source-20260823
export LONGWORLD_CANDIDATE_ATTESTATION_KEY_ID=probe-candidate-20260823
export LONGWORLD_RANKER_ATTESTATION_KEY_ID=probe-ranker-20260823
export LONGWORLD_AUDITOR_ATTESTATION_KEY_ID=probe-auditor-20260823
export LONGWORLD_PROMOTION_ATTESTATION_KEY_ID=probe-promotion-20260823
export LONGWORLD_REPORT_ATTESTATION_KEY_ID=probe-report-20260823
# These values must come from a protected approval record, not from hashing the
# mutable local files immediately before export.
export LONGWORLD_PUBLIC_POLICY_SHA256="<approved-out-of-band-policy-sha256>"
export LONGWORLD_GH_BINARY_SHA256="<approved-out-of-band-gh-binary-sha256>"

# Export each allowlisted public workflow, then create one signed replay bundle.
# Repeat --episode for every independently exported issue/PR/CI/release history.
uv run python scripts/export_github_workflow.py \
  --allowlist configs/public_repo_allowlist.yaml \
  --repo OWNER/REPO --pull PR_NUMBER --release RELEASE_TAG \
  --out data/workflows_v2/OWNER_REPO_PR.json
uv run python scripts/extend_episode_bundle.py \
  --bundle configs/public_repo_episodes_v2.json \
  --episode data/workflows_v2/OWNER_REPO_PR.json --create

# The configured v2 bundle must contain enough independent release cycles for
# the selected profile; a missing or invalid bundle makes generation fail closed.
uv run python scripts/generate.py --config configs/p3_valid.yaml \
  --out-dir data/p3_probe --workers 4

# Candidate rows are intentionally not train-ready. Rank and audit the complete
# candidate set; selection keeps only complete audited worlds and assigns the
# signed 10/2 split while protecting real workflow coverage in train.
uv run python scripts/rank_candidates_dense.py \
  --candidates data/p3_probe/train.jsonl \
  --output data/p3_audit/train_rankings.jsonl \
  --model-id sentence-transformers/all-MiniLM-L6-v2 \
  --revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41
uv run python scripts/rank_candidates_dense.py \
  --candidates data/p3_probe/eval.jsonl \
  --output data/p3_audit/eval_rankings.jsonl \
  --model-id sentence-transformers/all-MiniLM-L6-v2 \
  --revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41
uv run python scripts/promote_candidates.py audit \
  --candidates data/p3_probe/train.jsonl \
  --rankings data/p3_audit/train_rankings.jsonl \
  --output data/p3_audit/train_audits.jsonl \
  --accepted-candidates data/p3_audit/train_accepted.jsonl \
  --rejects data/p3_audit/train_rejects.jsonl --top-k 3 \
  --release-profile p3-probe-12-v1 \
  --episode-bundle configs/public_repo_episodes_v2.json \
  --workers 4
uv run python scripts/promote_candidates.py audit \
  --candidates data/p3_probe/eval.jsonl \
  --rankings data/p3_audit/eval_rankings.jsonl \
  --output data/p3_audit/eval_audits.jsonl \
  --accepted-candidates data/p3_audit/eval_accepted.jsonl \
  --rejects data/p3_audit/eval_rejects.jsonl --top-k 3 \
  --release-profile p3-probe-12-v1 \
  --episode-bundle configs/public_repo_episodes_v2.json \
  --workers 4
uv run python scripts/promote_candidates.py select \
  --candidates data/p3_probe/train.jsonl data/p3_probe/eval.jsonl \
  --audits data/p3_audit/train_audits.jsonl data/p3_audit/eval_audits.jsonl \
  --release-profile p3-probe-12-v1 \
  --train-candidates data/p3_selected/train_candidates.jsonl \
  --eval-candidates data/p3_selected/eval_candidates.jsonl \
  --train-audits data/p3_selected/train_audits.jsonl \
  --eval-audits data/p3_selected/eval_audits.jsonl \
  --receipt data/p3_selected/release_selection.jsonl

# The 48- and 210-world profiles additionally require the post-gate receipt for
# 12 and 48 worlds respectively. The selector verifies its pinned original
# environment/key identity rather than trying to re-sign a predecessor report.
uv run python scripts/promote_candidates.py promote \
  --candidates data/p3_selected/train_candidates.jsonl \
  --audits data/p3_selected/train_audits.jsonl \
  --output data/p3_promoted/train.jsonl --expected-split train \
  --release-selection data/p3_selected/release_selection.jsonl \
  --episode-bundle configs/public_repo_episodes_v2.json \
  --workers 4
uv run python scripts/promote_candidates.py promote \
  --candidates data/p3_selected/eval_candidates.jsonl \
  --audits data/p3_selected/eval_audits.jsonl \
  --output data/p3_promoted/eval.jsonl --expected-split eval \
  --release-selection data/p3_selected/release_selection.jsonl \
  --episode-bundle configs/public_repo_episodes_v2.json \
  --workers 4
uv run python scripts/promote_candidates.py report \
  --candidate-report data/p3_probe/quality_report.json \
  --candidates data/p3_probe/train.jsonl data/p3_probe/eval.jsonl \
  --rows data/p3_promoted/train.jsonl data/p3_promoted/eval.jsonl \
  --output data/p3_promoted/quality_report.json \
  --release-selection data/p3_selected/release_selection.jsonl
uv run python scripts/quality_gate.py --data data/p3_promoted \
  --release-profile p3-probe-12-v1 \
  --gate-receipt data/p3_promoted/release_gate_pass.json

# Signed release export. Conditions/buckets/seed are fixed by the profile:
# B1=full, B3=full+CF, B5=full+CF+ordered timeline, B5w=B5+sampler weight.
uv run python scripts/export_llamafactory.py --data data/p3_promoted \
  --out-dir data/sft/llamafactory \
  --release-profile p3-probe-12-v1
uv run python scripts/validate_training_export.py \
  --manifest data/sft/llamafactory/training_export_manifest.json \
  --release-profile p3-probe-12-v1 \
  --expected-transform-revision longworld-llamafactory-sharegpt-v4 \
  --required-output B5w.json \
  --expected-output-path data/sft/llamafactory/B5w.json \
  --dataset-info-key causaltwin_b5w --dataset-file B5w.json \
  --weighted-dataset-index B5w.datasets.yaml

# Train only after validating the signed export manifest. B2/B4 are rejected;
# B5w requires the pinned LLaMA-Factory v1 weighted sampler.
LONGWORLD_RELEASE_PROFILE=p3-probe-12-v1 \
  GPUS=0,1,2,3 bash scripts/train_llamafactory.sh B5

# Only for a separately generated, explicitly labelled CPT product.
uv run python scripts/export_cpt.py --data data/p3_cpt/train.jsonl \
  --output data/cpt/train.jsonl
```

The HMAC keys are separate probe-stage producer identities and must not be
stored beside or published with the dataset. They are not a production-grade
trust boundary against a compromised producer. The 48/210 releases remain
blocked until asymmetric/KMS-backed signing, independent policy enforcement,
and the production scanner/supply-chain controls are implemented. P3 source,
candidate, ranking, audit, promotion, and report stages already fail closed
when their own role identity is absent or reused across roles.

The staged selector expects `--predecessor-gate-receipt` plus
`LONGWORLD_PREDECESSOR_GATE_ATTESTATION_KEY` and
`LONGWORLD_PREDECESSOR_GATE_ATTESTATION_KEY_ID` from a protected predecessor
trust store. Under the current HMAC probe implementation the verifier still
holds secret material, so this interface tests sequencing but does not remove
the asymmetric/KMS production blocker.

Private-repository export is currently disabled. “Repository allowlist,
authorization record, secret/PII scan, and auditable redaction” means naming the
specific repositories permitted for export, retaining who authorized which
fields and why, rejecting credentials and sensitive identifiers before
serialization, and recording every deterministic redaction. It is not blanket
permission to ingest an entire private organization.

## 中文进展与 world 覆盖

- P4 local-48 已完成完整的 72 candidates→dense ranking→strict audit→
  world-atomic selection→promotion→quality gate→SFT export：最终 48 worlds、
  1,784 train-ready rows，三域精确 16/16/16；报告见
  `reports/p4_multidomain_local48_v1.md`。这是扩大后的 local engineering
  release，仍不是 production approval。
- local-48 长度分布为 16K×510、32K×808、64K×466，固定 Qwen tokenizer
  验证的 exact-64K 按域为 Company 160、ResearchLab 300、CodeForge 6；
  0 duplicate、0 conflicting prompt、0 boilerplate/pulse。记录的 context-token
  估算总量为 66,806,787。
- local-48 的 1,784 rows 中只有 28 rows 是经验证的 GitHub hybrid；其余
  1,756 rows 是明确标注的 synthetic executable/schema 数据。规模增长没有
  复制同一真实 bundle 来虚增真实来源比例。
- P4 v5 三域 local probe 已完成 candidate→dense audit→world-atomic
  selection→strict replay promotion→quality gate→training export：12 worlds、
  450 train-ready rows，`company` / `researchlab` / `codeforge` 精确 4/4/4。
- `company` 使用 36 个连续 release/failure/recovery 周期；`researchlab`
  使用 88 个 revision/review/benchmark/reproduction workstreams。二者均为
  synthetic executable/schema 数据，没有伪装真实来源。真实 GitHub 正文只在
  已验证的 CodeForge hybrid world 中进入 state 和答案。
- v5 长度分布为 16K×134、32K×190、64K×126；固定 Qwen tokenizer 验证的
  exact-64K 按域为 Company 46、ResearchLab 74、CodeForge 6。0 exact
  duplicate、0 prompt-answer conflict、0 boilerplate/pulse。报告见
  `reports/p4_multidomain_probe_v5.md`。
- 450 rows 中只有 28 rows 是 `real_workflow_hybrid_executable`；其余 422
  rows 是明确标注的合成可执行/schema world。80 个真实 GitHub episode、
  2,232 条原始记录是 source pool，不等同于 80 条 SFT 样本。
- `data/p4_multidomain_promoted_v3` 已因 CF 正文未变化却产生冲突答案而失效，
  不可用于训练；v4 是修复过程中的诊断批次，未选择或发布。v5 才是当前有效批次。
- SEC/company 已有 fixture-only、integration-disabled 的可审计 exporter
  骨架，但尚未抓取真实 EDGAR filing，也未接入 world。KB/Wikipedia/实体、
  公司财报/PDF、论文 review-response/benchmark 的 release-ready 真实 source
  pipeline 仍未完成，不能计入当前真实来源规模。
- 因果门保留：充分性、remove-one、反事实、无关扰动不变、单文档/局部窗口不可解。
- 新增：真实 repo episode、版本选择 / CI 回归源头 / license 兼容任务、4k/8k/16k 窗口与 BM25/TF-IDF/dense top-k 负门禁、签名 strict replay promotion。
- **已停用** weekly pulse / 20 句 `unique_prose` 作为长度填充。不够长就保持自然长度。
- `trajectory` 已更名为 `ordered_artifact_view`（按时间排序的文档，不是 ACC 轨迹）。
- 未做：多 repository/source-family 扩展、真实论文 revision/review 链路、真实 Agent rollout、NaturalLong-CPT 与生产密钥重签。P4 是 local probe，不是 production-48 批准。

## Positioning vs SearchArt / ACC

| Method    | Strength here                                                            | Not claimed                              |
| --------- | ------------------------------------------------------------------------ | ---------------------------------------- |
| SearchArt | real web/entity distribution                                             | LongWorld does not replace this          |
| ACC       | real agent-decision traces compiled to QA                                | LongWorld does not replace this          |
| LongWorld | programmable state, versioning, counterfactual twins, necessary evidence | not automatically more natural or larger |

Best stack (later): SearchArt distribution + LongWorld state/causality + agent rollouts + ACC compile.

## Domains (truth regime: `real_schema_synthetic_instance`)

| Domain      | Real schema                         | Synthetic instance | Motifs                                         |
| ----------- | ----------------------------------- | ------------------ | ---------------------------------------------- |
| company     | contract / amendment / audit        | fictional firms    | supersession, fork_join, chain, counterfactual |
| researchlab | paper / eval / git / issue / SPDX   | fictional labs     | + delayed_effect, contradiction, hidden_bridge |
| codeforge   | commit / CI / issue / tag / LICENSE | fictional repos    | same motif set on HEAD, not scores             |

Public search/code **anchors** are frozen distractor language. They never hold the gold fact.

## Data distribution (p1.1 freeze, `data/p0` — diagnostic only)

Generated 2026-08-18. Reports: [`reports/quality_report.json`](reports/quality_report.json), [`reports/stats.json`](reports/stats.json).

|                      |                                         |
| -------------------- | --------------------------------------: |
| Worlds               |                                     210 |
| Rows                 |                                  45,564 |
| Question slots       |        7,594 (train 5,364 / eval 2,230) |
| Retention            | 31.8% (7,594 / 23,902 packing attempts) |
| Clones (`#pad`)      |                                   **0** |
| Unique full answers  |                                   1,012 |
| Instance topologies  |                                   1,115 |
| Topology families    |                                      16 |
| Motifs               |                                       7 |
| Gold CFR (kept rows) |                                     1.0 |

**Domain** (rows): company 11,424 (25.1%) · codeforge 17,136 (37.6%) · researchlab 17,004 (37.3%).

**View** (equal): full, cf, minimal, distractor_only, trajectory, memory — 7,594 each.

**Length**: train 8k / 32k / 64k = 13,404 each; eval 128k / 256k = 2,676 each.

**Query type** (rows): current_state 8,568 · counterfactual 8,568 · fork_join 5,616 · delayed_effect 5,676 · contradiction 5,712 · hidden_bridge 5,712 · multi_hop 2,856 · version_diff 2,856.

**Timing**: query-first = query-late = 22,782. **Position**: middle 42,708 (keep-one-position picks max evidence span) · back 2,856.

**Historical p1.1 SFT export** (diagnostic only): B1–B5 used the old six-view
dump. It is not compatible with the P3 release exporter. P3 publishes only B1,
B3, B5, and B5w; minimal contexts are a separate future short-context
curriculum and are never inherited from a long bucket.

## Strict filter (every kept sample)

Shallow / decoy queries never enter the jsonl (`historical_state`, `aggregation`, `decoy_*`).

A slot is kept only if `Verification.all_green()`:

1. **schema_ok** — program gold exists, essential artifacts present
2. **full_sufficient** — oracle replay on full context = gold
3. **minimal_sufficient** — the declared essential set is enough
4. **remove_one_fails** — dropping any essential artifact changes or blocks the answer
5. **counterfactual_changes_answer** — intervening on the cf event yields `y_cf ≠ y`
6. **distractor_invariance_gold** — intervening on a known-irrelevant event keeps `y`
7. **local_window_insufficient** — a local packed window ≠ gold
8. **closed_book_unsolved** — empty event set ≠ gold
9. **no_shortcut** — no `key=value` dumps; no single non-essential doc solves it; gold not copied into the question
10. **surface_match** — cf twin documents overlap the original ≥ 0.82
11. **min_complexity** — proof depth ≥ 2 and ≥ 2 essential artifacts (or explicit counterfactual)
12. **bm25_top1_insufficient** — lexical top-1 document does not replay to gold (≥2 essential docs)
13. **question_only_unsolved** — same as closed-book (program oracle; not a multi-model ensemble yet)

Packing grows from unique non-boilerplate documents up to a **max cap**. Archive
clones, weekly pulses, and the 20-sentence prose bank are forbidden as fill.
If the unique pool is short, the sample stays short. Quality gate: 0 clones,
boilerplate token ratio < 15%, pulse doc ratio < 10%. 64k is optional.

This is **programmatic** verification (event replay), not an LLM judge.

## Is the long-range dependence real?

**Causal necessity (proof graph) — yes, and this is the product.** Each kept
question requires ≥ 2 artifacts (except explicit counterfactuals). Authoritative
channels do not reprint the gold numeral or hash. Remove-one and counterfactual
twins are enforced.

**Token-axis span — only as far as real unique documents allow.** p1.1's 64k
mean distance ≈ 62.9k was produced by pulse/prose fill and is **not** a quality
claim for p1.2. p1.1 dumps remain a CausalTwin diagnostic freeze, not CPT.

## Generate / train

```bash
uv sync
/usr/bin/python -m pytest tests/ -q

# CausalCore-v0 (natural length)
python scripts/generate.py --config configs/causalcore.yaml --out-dir data/causalcore --workers 4
python scripts/quality_gate.py --data data/causalcore

# smoke
python scripts/generate.py --config configs/smoke.yaml --out-dir data/smoke --workers 4
python scripts/quality_gate.py --data data/smoke
```

## Train (full-parameter SFT)

CausalTwin and the external baselines use **full-parameter SFT**, not LoRA.
Backbone is **Qwen/Qwen3.5-4B** (native 256K). Training uses `add_non_thinking_prefix` (no-think) and **Ulysses sequence parallel** (`sequence_parallel_size=4` on 8 GPUs, DP=2). `max_length` is a **cap**, not a fill; the immutable LongWorld release contains actual 16k/32k/64k samples with `packing: false`. External baselines retain their own separately reported length distributions. `padding_free` is FlashAttention varlen, not packing.
Aligned hyperparameters and wandb live in `configs/swift/recipe.env` (entity `wyncke`, project `longworld`, 128k group `longworld-128k-sft-8gpu`). Method yamls only change data / `run_name`; the launcher re-applies the recipe on the CLI so ACC, LongTrace, and LongMIT stay comparable.
Do not start 4B jobs on a box whose GPUs are already held. The signed LongWorld
release path starts from the profile-bound LLaMA-Factory export; Swift is an
optional second, manifest-bound conversion for B1/B3/B5.

```bash
source scripts/uv_project_env.sh
uv sync --extra train
bash scripts/setup_swift.sh
INSTALL_SWIFT=1 bash scripts/setup_swift.sh

bash scripts/download_external.sh
uv run --extra train python scripts/export_external_llamafactory.py
uv run --extra train python scripts/export_swift.py \
  --release-profile p3-probe-12-v1

# 128k related-work baselines (ACC → LongTraceRL → LongMIT), 8 GPU SP=4 DP=2.
GPUS=0,1,2,3,4,5,6,7 bash scripts/train_baselines_128k.sh
# 4B-Base ablation on 4 GPUs (still GBS 16: SP=4 DP=1 accum=16):
# GPUS=4,5,6,7 CONDS="ext_acc ext_longtrace" MODEL=data/models/Qwen3.5-4B-Base \
#   bash scripts/train_baselines_128k.sh

# cutoff 256k is the native cap; signed B5 samples are 16k/32k/64k (packing off).
LONGWORLD_RELEASE_PROFILE=p3-probe-12-v1 \
  GPUS=0,1,2,3 bash scripts/train_swift.sh B5
GPUS=0,1,2,3 bash scripts/train_swift.sh ext_docqa
GPUS=0,1,2,3 bash scripts/train_swift.sh ext_acc
```

In-repo full FT (single GPU diagnostic, still not LoRA):

```bash
uv run --extra train python scripts/train_sft.py --diagnostic-only \
  --release-profile p3-probe-12-v1 --condition B5 --data data/p3_promoted
uv run --extra train python scripts/eval_causal.py --data data/p3_promoted \
  --model-dir data/sft/ckpt_B5/final --out data/sft/eval_B5.json
```

This single-GPU path emits no signed training manifest and is never a release
artifact. Use it only for local diagnostics.

`hold.sh` wrap is used only when `/workspace/wynckeliao/ops/gpu/hold.sh` exists.
`attn_impl` prefers FA3, then FA2 (`flash_attn`), else SDPA. Full FT at a 256k
_cap_ still needs H100/H200-class cards when a long sample appears.

Planned main table: same backbone and effective token budget across B1/B3/B5;
B5w reports weighted exposure separately. Release requires long-context gains,
no general-capability regression, and no closed-book increase.

## Layout

```text
longworld/          engine, verifier, packer, domains
  domains/company | researchlab | codeforge
configs/            causalcore / p0 / smoke / LlamaFactory
scripts/            generate, quality_gate, export, train
tests/
reports/            frozen quality + stats snapshots
```
