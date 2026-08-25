# CausalCore v2 long-span mix

Date: 2026-08-20

## What this is

`data/v2_p28` is the 12-world mix with **128k/256k on the train split**,
using every on-disk public source (longest first). It does not replace
the mid-length freeze in `reports/causalcore_v2/FREEZE.md` (`data/v2_p27`).

| Piece           | Path                                 |
| --------------- | ------------------------------------ |
| Raw worlds      | `data/v2_p28/`                       |
| Quality         | `reports/v2_p28_quality.json`        |
| ShareGPT B5/B5w | `data/sft/causalcore_v2_long/`       |
| ms-swift        | `data/sft/causalcore_v2_long_swift/` |
| Train recipe    | `configs/swift/B5_v2_long.yaml`      |

Pulse/prose fill is still off. 48/210 still blocked.

## Generator (full view)

- N_eff **50.47**, join **0.220**, pulse **0**, clones **0**, rejects **0**
- unique answers **134**; deep **340/1640**; retrieval **860**; local **440**
- Length (all views): **16/32/64/128/256k = 1968 each**
- Train split also has 128k/256k (**1404 rows each**, all views)
- RFC share by train-full bucket: 16k **0.43**, 32k **0.31**, 64k **0.42**, 128k **0.71**, 256k **0.85**
- Evidence distance tracks the cap (16k ~15k tok … 256k ~252k tok)
- `rfc9110` appears in train-full contexts (no longer dropped by A–Z leftover)

## SFT export (B5)

4530 rows, ~333M tokens; 16k 1170 / 32k 840 / 64k 840 / 128k **840** / 256k **840**.
memory **0**; long-bucket local_or_mixed **0**. Token spread vs B5w **0.01%**.

## Claim boundary

- 256k is unique public text filling a cap after necessary evidence, not 5-hop reasoning.
- External ACC / LongTrace / LongMIT / DocQA are baseline SFT in `data/external/`, not CausalCore gold.
- `ordered_artifact_view` ≠ ACC.
