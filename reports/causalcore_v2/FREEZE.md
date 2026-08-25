# CausalCore v2 freeze

Date: 2026-08-20

## What is frozen

The 12-world generator mix in `data/v2_p27` is the CausalCore v2
**reference dump**. It is not a 210-world train set and not a 128k–256k
reasoning claim.

| Piece             | Path                                          |
| ----------------- | --------------------------------------------- |
| Raw worlds        | `data/v2_p27/` (`train.jsonl` / `eval.jsonl`) |
| Quality           | `data/v2_p27/quality_report.json` (copy here) |
| ShareGPT export   | `data/sft/causalcore_v2/`                     |
| ms-swift messages | `data/sft/causalcore_v2_swift/`               |
| Train recipe      | `configs/swift/B5_v2.yaml`                    |

Do not delete `data/p0` (p1.1 length-forced diagnostic). Do not regenerate
`v2_p27` with pulse/prose fill. Do not run `configs/causalcore.yaml` (48)
or 210 unless the mix itself changes.

## Mix (full view, generator)

From `quality_report.json`:

- N_eff **50.581**, join **0.215**, pulse **0**, clones **0**, rejects **0**
- unique answers **134**; deep **256/1172** (21.8%); retrieval **608**; local **308**
- RFC token share **0.450** (16/32/64k native-majority; 128/256k still public-source span)
- 3-hop revisitation + 4-hop ratification (`LT-*`) + docket_control (`DK-*`) + source_choice (`adopted||unused`)
- 4 workplace registers; `ordered_artifact_view` ≠ ACC

## SFT boundary (this slice)

Raw dump still contains `memory` rows whose `length_bucket` is copied from
the packed slot. Those cards are not long documents. The freeze **export**
drops them. Memory gold in the engine is `unanswerable`.

Default export buckets are `8k,16k,32k,64k`. This freeze used **16k,32k,64k**
(the v2 train mix). `local_or_mixed` is dropped on 32k+.

B5 export (equal-token vs B1, ~9.8M tokens): 345 rows; 16k **144** / 32k 93 /
64k 108; views full, minimal, cf, distractor_only, ordered_artifact_view;
**memory 0**; long-bucket local_or_mixed **0**. Token spread **0.51%**.

That subsample is smoke-scale. 48 worlds of this mix would scale counts, not
topologies.

## Claim boundary

- Necessary evidence and cf twins hold on the generator mix.
- Length is a cap / sample attribute, not a fill KPI.
- Do not mix “we exported 64k rows” with “deep 5-hop long reasoning.”
- WorldAgent / WorldACC are not this freeze.

## Reproduce export

```bash
/usr/bin/python scripts/export_llamafactory.py \
  --data data/v2_p27 \
  --out-dir data/sft/causalcore_v2 \
  --train-buckets 16k,32k,64k \
  --conditions B1,B2,B3,B4,B5,B5w
/usr/bin/python scripts/export_swift.py --only causaltwin \
  --causaltwin-src data/sft/causalcore_v2 \
  --causaltwin-out data/sft/causalcore_v2_swift \
  --src-dir /tmp/unused --out-dir data/sft/causalcore_v2_swift
```

Train (when GPUs are free; wrap hold; 345 rows):

```bash
GPUS=6,7 bash scripts/train_swift.sh B5_v2
```
