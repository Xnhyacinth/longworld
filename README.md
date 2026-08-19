# LongWorld / CausalTwin

Event-sourced worlds that compile into **verified** long-context SFT.

LongWorld is **not** a SearchArt or ACC substitute. It is the upstream
executable environment and ground-truth generator:

```text
real-schema anchors
  → executable world (event log + state)
  → asymmetric artifacts
  → min proof graph
  → multi-view compile (ACC-style)
  → causal / counterfactual / shortcut filters
  → SFT (B5 / B5w)
```

Scale unit is **dependency topology**, not QA count.

Status snapshot (p1.1, 2026-08-18): code + tests + generation reports in this
repo. The 6GB jsonl is **not** checked in (`data/` is gitignored).

## 中文进展

- 三域可执行世界：company / researchlab / codeforge（真实 schema + 合成实例）。
- 保留样本全部过 11 项程序门：充分性、remove-one 必要性、反事实改答案、单文档/局部窗口不可解、无 kv 捷径、无 clone padding。
- 长程有两层：proof 上至少两份必要证据；packing 把它们在 token 轴上拉开（64k 桶均距 ≈ 62.9k）。
- 未做：真实 Web 爬取、真实 Agent rollout、GRPO。训练等 GPU 空闲后用官方 LLaMA-Factory。

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

## Data distribution (p1.1, `data/p0`)

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

**SFT export** (equal-token, ~93.4M tokens/condition, spread 0.07%): B1 full-only; B2 full+min; B3 full+cf; B4 four-view query-first; **B5** four-view + trajectory + memory + query-late; **B5w** = B5 + distance upsample on full/trajectory/cf only.

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

Packing additionally rejects clone padding and unique-token shortfall. Quality gate: 0 clones, retention in [0.15, 0.55], mean 64k evidence distance ≥ 8k (observed **62,874**).

This is **programmatic** verification (event replay), not an LLM judge.

## Is the long-range dependence real?

**Yes, for two independently enforced notions.** Do not collapse them.

**Causal necessity (proof graph).** Each kept question requires ≥ 2 artifacts. Authoritative channels (release note / git tag) **do not reprint** the gold numeral or hash; the reader must join an earlier private artifact. Remove-one is 100% on kept deterministic items. Counterfactual twins change the answer; irrelevant edits do not. Mean proof depth ≈ 2.38.

**Token-axis span (packing).** Essential docs are separated by **unique** distractors (parallel worlds, cross-domain filler, public anchors). Archive clones are forbidden. Observed mean evidence distance:

| bucket | mean distance (est. tokens) |
| ------ | --------------------------: |
| 8k     |                       6,873 |
| 32k    |                      30,878 |
| 64k    |                      62,875 |
| 128k   |                     126,866 |
| 256k   |                     254,879 |

Filler prose lengthens the span; it is **not** extra causal hops. The hops are the essential events. That is the intended CausalTwin claim, not “longer text is more multi-hop.”

## Generate / train

```bash
uv sync
python -m pytest tests/ -q

# smoke
python scripts/generate.py --config configs/smoke.yaml --out-dir data/smoke --workers 4
python scripts/quality_gate.py --data data/smoke

# full
python scripts/generate.py --config configs/p0.yaml --out-dir data/p0 --workers 8
python scripts/quality_gate.py --data data/p0
python scripts/stats.py --data data/p0
python scripts/export_llamafactory.py --data data/p0
```

Train with **official LLaMA-Factory** (not a fork), when GPUs are free:

```bash
bash scripts/setup_llamafactory.sh
bash scripts/train_llamafactory.sh B5
```

Main table: same backbone, same tokens; B5/B5w CFR ≫ B1/B2; Acc_full not down; closed-book not up.

## Layout

```text
longworld/          engine, verifier, packer, domains
  domains/company | researchlab | codeforge
configs/            p0 / smoke / LlamaFactory
scripts/            generate, quality_gate, export, train
tests/
reports/            frozen quality + stats snapshots
```
