# P52 GovInfo registered-parent conversion closeout

## Task Report

P52 now has a source-bound registered-parent builder, but the world remains
fail-closed and contributes **zero** train-ready rows. The builder refetched and
verified the five frozen official GovInfo inputs, retained only `real_public`
artifacts, emitted three signed `full/same_case_dossier` parents, and bound them
to one registered v1 task-replay sidecar. It did not modify or re-sign the prior
nine custom-view candidates.

The three parents are:

| Band | View | Exact tokens | Artifacts | Essential | Near-dup sentence ratio |
| --- | --- | ---: | ---: | ---: | ---: |
| 32K | full | 32,341 | 49 | 5 | 0.0417 |
| 64K | full | 64,012 | 106 | 9 | 0.0303 |
| 128K | full | 129,326 | 231 | 13 | 0.0298 |

Shared projection verified the parent attestations, frozen receipt, task digest,
content commitments, and exact tokenizer, then emitted 9 candidate rows. Shared
preflight accepted 9/9 with no rejects, and dense ranking signed 9/9 rows.

| Band | Full | CF | Ordered |
| --- | ---: | ---: | ---: |
| 32K | 32,341 | 32,476 | 32,341 |
| 64K | 64,012 | 64,147 | 64,012 |
| 128K | 129,326 | 129,461 | 129,326 |

No shared dense audit, selection, promotion, quality report, B5 export, or
inventory update was completed.

## Downstream Context

The original 32K task requests two authentic modified sections, F/II/219 and
D/II/207. In the shared chronological view their five essential artifacts occur
at indices 39, 40, 41, 45, and 46: the two enrolled endpoints dated 2024-03-08,
the authenticated Bill Status transition dated 2024-03-09, and the two Public
Law endpoints dated 2024-03-09. This source-time topology—not manual background
movement—places the complete proof inside artifact window 38:47.

The targeted shared audit failed exactly as follows:

```text
TaskProofError: contiguous window 8k retrieves the gold answer: 38:47
PromotionError: task upstream proof replay failed: contiguous window 8k retrieves the gold answer: 38:47
```

Because the contiguous artifact-window gate failed first, the shared raw-token
window audit was not reached. The older P52 prefix-only 4K/8K/16K checks are not
substituted for that missing shared evidence.

One pre-agreed data hypothesis was then tested without changing gates or moving
background: make the 32K task ask for structurally distant authentic modified
sections A/I/138 and F/II/219. Fresh source reconstruction failed before signing
any parent because the real counterfactual replacement changed length too much:

```text
32k exact natural pack unavailable:
{'full': 32621, 'cf': 31088, 'ordered_artifact_view': 32621}, band=32000-32768
```

This attempt emitted zero candidates. No padding, cloning, splitting,
truncation, threshold relaxation, or third key combination was attempted.

Key hashes:

- source bundle: `55c3a03cba520df604bf56938c6b608c4d165c6a6e3d13a7d1379c42bcff6b7f`;
- source receipt: `3b3f5c7287bca7789cbb4d36d0b717cf65dfa1450e85a531ed1092f236f1a368`;
- parent file: `36808521300f4c4685de33e9e8c88ee6e4d8c685a7efe5a9e54d2569c185eac6`;
- parent row set: `cdfd09e8c614027a06a4581bd99049820f5dce1de97fe385d860e68e534500c0`;
- parent sidecar: `65aefead25effb73d27100fa7324453c5e6865abb2a564267faaba2cbadda4ca`;
- projected candidates: `49c6ebc4ee993aac7f91779b16e2573c0bf78f9ca70bc93ae578f43cc4cfe49c`;
- projected v3 sidecar: `3294e746acf527d50c6b7f0379eacb5fd83d739885cceb8a76f5a3186d81a2fd`;
- dense rankings: `53f335c38b2fe2b3797564328da8bfbce1e4187ae4bd0d696d5f27bfca23975a`;
- structural-span blocker: `34b0dee4b2ed299d2712d69b88b9b9708790b0ca057a791f7b195ff9b950f064`.

## Blockers

P52 currently has a two-sided data-geometry blocker. The first authentic task
fits all exact bands but is answerable inside one 8K contiguous artifact window.
The agreed structurally wider task avoids relying on that clustered pair but
cannot place its factual and real-text counterfactual views in the same exact
32K band without prohibited padding, truncation, or background manipulation.

Therefore `train_ready=false`, promoted rows = 0, B5 rows = 0, 128K promoted
rows = 0, and inventory delta = 0. A future attempt needs a different authentic
bill transition or a predeclared task whose endpoint bodies are both
length-compatible and naturally dispersed; it must start from a new frozen
source-parent build rather than repair these signed rows.
