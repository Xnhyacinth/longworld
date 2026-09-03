# P16.2 CodeForge failure-recovery closeout

## Outcome

**BLOCKED** for the P16.2 success criterion (64K **and** 128K three-view).

`pulumi/pulumi` `failure_recovery_release_trace` landed **7** signed candidate
rows at seed 160. Dense rank and strict episode-bundle audit accepted **7/7**.
64K is a complete three-view cell. 128K full packed to **114833** tokens, below
`[128000, 131072]`. Ordered 16K/32K missed on view-distance, not overflow.

This is not a P15 uv `patch_review_test_ancestry` clone. Each cycle is
failed CI → repair commit → approved review / same-name passing CI → merge →
cited release.

## Exact length × view counts

| length | full | cf | ordered_artifact_view |
|---|---:|---:|---:|
| 16k `[16000,16384]` | 1 (16368) | 1 (16368) | 0 |
| 32k `[32000,32768]` | 1 (32671) | 1 (32671) | 0 |
| 64k `[64000,65536]` | 1 (65533) | 1 (65533) | 1 (65533) |
| 128k `[128000,131072]` | 0 | 0 | 0 |

n_rows=7, n_rejects=3, n_attempts=8, mean_near_dup_sentence_ratio=0.0033.
Audit: 7 accepted, 0 rejects.

## Generate rejects (fail-closed)

- 16k ordered: `view_distance_shortfall:7359<8000`
- 32k ordered: `view_distance_shortfall:14152<16000`
- 128k full: `exact_128k_out_of_range:114833`

No leftover-PR stuffing was used to chase 128K.

## Authentic pulumi cycles (8 distinct tags)

| Tag | PR | Repair head | Recovered topology |
|---|---:|---|---|
| v3.235.0 | 22812 | `0…` exported | failed CI → later same-name pass, approved review, merge, cited tag |
| v3.237.0 | 22862 | | same |
| v3.238.0 | 22864 | | same |
| v3.241.0 | 23092 | | same |
| v3.242.0 | 23245 | | same |
| v3.245.0 | 23425 | | same |
| v3.246.0 | 23450 | | same |
| v3.252.0 | 23831 | | same |

Nested suffix: 16k=latest 1, 32k=latest 2, 64k=latest 4, 128k=all 8.
64k suffix is the four smallest latest episodes so 64k stays in band (65533).
128k unique-token ceiling on this 8-tag mix is 114833. A full-matrix extra
unique-tag episode (~220 HEAD checks) jumps ~170k and `strict_support_overflow`s.
Pulumi CI is bimodal (neo ~13–15 checks vs matrix ~119–230). No unused unique
tag sits in the ~13k-token gap. DuckDB had only 5 unique-tag exportable cycles;
an 8-PR same-tag bundle failed `duplicate source bodies`.

## SHAs

- yaml `4483bc2314d6da542186fa183666cf33ad1992c591a0b70de3cc78b5d52a6ffa`
- bundle `d23382ce6922a1415ee6dec685c8796632a71c93c55eb3137c69f68a97803418`
- export request `30b7c1acbc8bc4ee47bb4f29350578890cbb41d0ab1b9a4ad38d3eb40eb9da53`
- allowlist `dd39cfef6803b01b08cc476acef805265a7f10d62378889e1d56cb8603cb4c60`
- candidate train.jsonl `92164f017b7f16a4944a0eda38c7accd5312b20492704b18396ff2fdc2c73130`
- quality_report `5399953bdc060b21858ba7d034658e589f6a64b7333fd55c5da131c99687ed0c`

Episodes (sha256, path):

- `29ba9ac88dda40b94070685ed6bbbe4aaad13220702dc9008571a1b141f6cc90` pulumi_pr22812_v3.235.0.json
- `7b6a7e4600d046f6b835904be52ec4f583f52930aca6853f8488d899bf1a2e00` pulumi_pr22862_v3.237.0.json
- `45092015a98c3cf85b29e7b97a14179fb794278010aa4c64addabb4a9a619b00` pulumi_pr22864_v3.238.0.json
- `f3378bf2954893fdcb4d3e444ee937f187cda082cd512509321b0337c5649ef3` pulumi_pr23092_v3.241.0.json
- `815d457816a62340a9d16f53c3657311aa3f2b17d2aec9ccf1c229846d8c6e02` pulumi_pr23245_v3.242.0.json
- `af5d8b9665267b3e227f4e0e9cf0c9120d1b3ddbd020717c415e8c560b49a90b` pulumi_pr23425_v3.245.0.json
- `1382bb5ac2b0c2bba9b5d7f7b481143c453767954e2c15b85301b9bf91779018` pulumi_pr23450_v3.246.0.json
- `9977fcb88d75b751c3f31f85f4c22f143c55416b1d1b1064bdb9260913a0f014` pulumi_pr23831_v3.252.0.json

derived_view_gate / exact-pack / replay were not weakened.
`astral-sh/uv` was not counted as this world.
