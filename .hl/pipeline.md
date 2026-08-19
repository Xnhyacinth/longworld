# CausalTwin / LongWorld pipeline state (p1.1)

Date: 2026-08-18

## Positioning

LongWorld is **not** a SearchArt/ACC substitute. It is the upstream executable
environment and ground-truth generator:

real anchors → executable world → verifiable tasks → (later) agent rollout →
ACC multi-view compile → causal / counterfactual filter → SFT.

Scale unit is **dependency topology** (`topology_id` = family + instance tokens),
not QA count. Motif families can repeat; instance topologies must not.

## Truth regime (this round)

`real_schema_synthetic_instance` across three domains:

| Domain      | Schema (real)                    | Instance (synthetic) | Distinct motifs                                |
| ----------- | -------------------------------- | -------------------- | ---------------------------------------------- |
| company     | contract / amendment / audit     | fictional firms      | supersession, fork_join, chain, counterfactual |
| researchlab | paper / eval / git / issue       | fictional labs       | + delayed_effect, contradiction, hidden_bridge |
| codeforge   | commit / CI / issue / tag / SPDX | fictional repos      | same motif set on git HEAD, not scores         |

Public search/code **anchors** are frozen distractor language, not gold facts.

Not in this round: live web crawl, real Agent traces, GRPO, constraint solver.

## Views

`full`, `minimal`, `cf`, `distractor_only`, `trajectory`, `memory`.

Method: B5 / B5w (includes trajectory + memory). Ablations B1–B4.

## Generate (p1.1, 2026-08-18)

Smoke (`data/smoke`): 9 worlds, 1344 rows, 0 clones, retention 31.1%, 3 domains, 7 motifs, 16 families.

Full (`data/p0`): 210 worlds, **45564 rows / 7594 question slots**, 0 clones, retention **31.8%**, 3 prepack rejects.

| slice                       |                                                                                      count |
| --------------------------- | -----------------------------------------------------------------------------------------: |
| domains                     |                                        company 11424 / codeforge 17136 / researchlab 17004 |
| motifs                      | 7 (supersession, fork_join, chain, cf-supersession, delayed, contradiction, hidden_bridge) |
| topology families           |                                                                                         16 |
| instance topologies         |                                                                                       1115 |
| unique full answers         |                                                                                       1012 |
| mean 64k evidence distance  |                                                                                      62874 |
| mean 256k evidence distance |                                                                                     254879 |

Equal-token LlamaFactory export ~93.4M tokens/condition, spread 0.07%. B5 includes trajectory + memory.

```bash
# smoke
uv run python scripts/generate.py --config configs/smoke.yaml --out-dir data/smoke --workers 4
uv run python scripts/quality_gate.py --data data/smoke

# full p1.1
uv run python scripts/generate.py --config configs/p0.yaml --out-dir data/p0 --workers 4
uv run python scripts/quality_gate.py --data data/p0
uv run python scripts/stats.py --data data/p0
uv run python scripts/export_llamafactory.py --data data/p0
```

Train (when GPUs are free; wrap hold):

```bash
bash scripts/setup_llamafactory.sh
bash /workspace/wynckeliao/ops/gpu/hold.sh wrap 0,1,2,3 -- bash scripts/train_llamafactory.sh B5
```
