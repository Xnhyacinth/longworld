# Amazon v3 / Meta v1 128k 8k-zipper window class — 2026-09-08

Status: diagnostic only. `production_eligible=false`. `train_ready=false`.
No promotion. No retrieval-profile admit. No new packing, padding, or clones.
Tokenizer: Qwen/Qwen3.5-4B `a7b0d22b993d71000cf2eadfb37222a67cee521e`.

The previously reported Meta dense-audit error
`contiguous window 8k retrieves the gold answer: 0:16` is reproduced exactly
on **Meta 16k full/cf**, not on Meta 128k. Amazon’s matching class is the
**16k full/cf zipper**, not a 128k intersecting-artifact failure.

## What was classified

Existing projected dumps only (gitignored/untracked). Signed IR was parsed
in place; Tesla was not fetched; Alphabet/Microsoft/NVIDIA were not
rematerialized; `longworld/core/*.py` was not edited.

| Dump | World | Views on disk |
| --- | --- | ---: |
| `reports/p57_finance_amazon_asset_trajectory_v3/projected/` | `finance_amazon_asset_trajectory_fy2021_2024_v3` | 12 |
| `.../v3/projected_latest_leftover/` | same v3 world, latest-year leftover layout | 12 |
| `.../v3/projected_banded_leftover/` | same v3 world, banded leftover layout | 12 |
| `.../v3/pipeline/` | v3 parents only (`full`) | 4 |
| `reports/p57_finance_amazon_asset_trajectory_v2/projections/` | `finance_amazon_asset_trajectory_fy2021_2024_v1` | 12 |
| `reports/p57_finance_meta_asset_trajectory_v1/projected/` | `finance_meta_asset_trajectory_fy2022_2025_v1` | 12 |

Amazon packed worlds bind
`p57_finance_amazon_ir_fy2021_2024_v1` (CIK `0001018724`). The sibling
inventory `p57_finance_amazon_ir_fy2022_2025_v1` is on disk and was not
packed into these dumps. Meta binds
`p57_finance_meta_ir_fy2022_2025_v1` (CIK `0001326801`). Isolated probe
trust: `/workspace/wynckeliao/.longworld-scaleout-20260906-private/amazon/`
and `.../meta/`.

## Source correctness

Full-view gold matches signed rendered-XBRL core facts (USD millions).
CF views differ by **exactly +1 revenue on the newest year in that band**
(declared CF, not a wrong source fact).

| Issuer | Report date | Revenue | Assets = L+E | CFO |
| --- | --- | ---: | ---: | ---: |
| Amazon | 2021-12-31 | 469,822 | 420,549 | 46,327 |
| Amazon | 2022-12-31 | 513,983 | 462,675 | 46,752 |
| Amazon | 2023-12-31 | 574,785 | 527,854 | 84,946 |
| Amazon | 2024-12-31 | 637,959 | 624,894 | 115,877 |
| Meta | 2022-12-31 | 116,609 | 185,727 | 50,475 |
| Meta | 2023-12-31 | 134,902 | 229,623 | 71,113 |
| Meta | 2024-12-31 | 164,501 | 276,054 | 91,328 |
| Meta | 2025-12-31 | 200,966 | 366,021 | 115,800 |

CF mutations: Amazon 16k `513983→513984`; 32k `574785→574786`; 64k/128k
`637959→637960`. Meta 16k `134902→134903`; 32k `164501→164502`; 64k/128k
`200966→200967`. Do not reject CF rows as wrong facts.

Verdict: **source-correct**. Wrong-fact reject does not apply.

## Window class (exact contiguous gate)

Shared `_contiguous_window_proof` was rerun on every **16k** view. 4k never
retrieves gold. 16k is `full_control` on 16k views. The failing class is
**contiguous 8k zipper** (leftover tinies clustered with gold at chronology
start). This is **not** TLS-v1 `raw token window intersecting-artifact` and
**not** a 4k failure. Raw/intersecting proofs are not reached: contiguous 8k
raises first.

| Issuer / layout | 16k full | 16k cf | 16k ordered |
| --- | --- | --- | --- |
| Meta `projected` | **8k zipper `0:16`** (6,949 tok) | **8k zipper `0:16`** (6,945 tok) | 8k **pass** |
| Amazon v3 `projected` | 8k **pass** (needed span 8,781 > 8,192) | 8k **pass** (8,784) | 8k **pass** |
| Amazon v3 `projected_banded_leftover` | 8k **pass** (8,781) | 8k **pass** (8,784) | 8k **pass** |
| Amazon v3 `projected_latest_leftover` | **8k zipper `0:17`** (7,677) | **8k zipper `0:17`** (7,680) | 8k **pass** |
| Amazon v3 `pipeline` parents | **8k zipper `0:11`** (4,624) | n/a (full only) | n/a |
| Amazon v2 `projections` | **8k zipper `0:18`** (7,933) | **8k zipper `0:18`** (7,936) | 8k **pass** |

That Meta `0:16` line is the 2026-09-07 scaleout receipt. Amazon’s “same
zipper class” is v2 / pipeline / latest-leftover **16k full/cf**, not v3
projected 16k (those already have leftover thick enough that 8k cannot
cover the 11 essentials).

### 32k / 64k / 128k are not this zipper

Necessary gold (essentials + `extra_table_facts` record ids) is
non-contiguous-minimal and still reconstructs the stored answer, but the
**contiguous** span covering those ids is far above 8k/16k:

| Issuer / layout | 32k needed span tok | 64k | 128k |
| --- | ---: | ---: | ---: |
| Meta `projected` full | 17,281 | 63,602 | 130,366 |
| Amazon v3 `projected` full | 29,373 | 59,845 | 54,016 |
| Amazon v3 `latest_leftover` full | 32,571 | 65,022 | 90,233 |
| Amazon v3 `banded_leftover` full | 29,373 | 65,048 | 92,299 |
| Amazon v3 `pipeline` full | 18,538 | 35,017 | 74,981 |
| Amazon v2 `projections` full | 28,543 | 59,639 | 54,130 |

Ordered 128k spans are the whole pack (~128.7k–130.7k) because chronology
interleaves leftover through the gold ids. 8k prefixes of 128k views do
**not** replay gold (Meta 128k full prefix `0:19` = 8,024 tokens,
insufficient). So 128k is **not** an 8k zipper. It is also **not**
classified as raw/intersecting here: those upper bounds were not exhaustively
rerun on 128k (contiguous 16k already cannot cover the needed span).

World-atomic consequence: Meta 16k full/cf 8k zipper fails the 12-view
dense audit before 128k is a promotable sibling. Same for Amazon v2 and
Amazon v3 latest-leftover / pipeline.

## One-shot RAG

Existing MiniLM rankings (`sentence-transformers/all-MiniLM-L6-v2`) were
replayed at k=1,2,3,8,16 on every dump/view that has rankings. **None**
retrieve gold. Parent Amazon v3 pipeline audits already recorded
`embedding_topk_insufficient=true` with prefix answers `unknown`.

Oracle retrieval of the essential id set *would* reconstruct gold
(`needed_only_retrieves_gold=true`). That is why 16k zipper rows are
**retrieval-profile candidates after re-validation**, not because MiniLM
already solves them. Do not invent a retrieval profile in this slice.

## Exact view tokens

### Meta v1 `projected` (`finance_meta_asset_trajectory_fy2022_2025_v1`)

| Band | cf | full | ordered |
| --- | ---: | ---: | ---: |
| 16k | 16,345 | 16,349 | 16,349 |
| 32k | 32,597 | 32,598 | 32,598 |
| 64k | 65,165 | 65,159 | 65,159 |
| 128k | 130,439 | 130,433 | 130,433 |

### Amazon v3 `projected`

| Band | cf | full | ordered |
| --- | ---: | ---: | ---: |
| 16k | 16,156 | 16,153 | 16,153 |
| 32k | 32,655 | 32,664 | 32,664 |
| 64k | 65,374 | 65,373 | 65,373 |
| 128k | 130,766 | 130,765 | 130,765 |

### Amazon v3 `projected_latest_leftover`

| Band | cf | full | ordered |
| --- | ---: | ---: | ---: |
| 16k | 16,356 | 16,353 | 16,353 |
| 32k | 32,629 | 32,638 | 32,638 |
| 64k | 65,090 | 65,089 | 65,089 |
| 128k | 130,707 | 130,706 | 130,706 |

### Amazon v3 `projected_banded_leftover`

| Band | cf | full | ordered |
| --- | ---: | ---: | ---: |
| 16k | 16,156 | 16,153 | 16,153 |
| 32k | 32,655 | 32,664 | 32,664 |
| 64k | 65,116 | 65,115 | 65,115 |
| 128k | 130,733 | 130,732 | 130,732 |

### Amazon v3 `pipeline` parents (full only)

| Band | full |
| --- | ---: |
| 16k | 16,142 |
| 32k | 32,509 |
| 64k | 64,700 |
| 128k | 129,809 |

### Amazon v2 `projections` (`..._fy2021_2024_v1`)

| Band | cf | full | ordered |
| --- | ---: | ---: | ---: |
| 16k | 16,284 | 16,281 | 16,281 |
| 32k | 32,282 | 32,291 | 32,291 |
| 64k | 64,556 | 64,555 | 64,555 |
| 128k | 128,845 | 128,844 | 128,844 |

All of these rows already carry `production_eligible=false`.

## Recommended routing

Do not lower 4k/8k. Do not auto-admit to a retrieval product. Do not pack
new 16/32/64/128 clones in this slice.

| Rows | Window class | Route |
| --- | --- | --- |
| Meta 16k full/cf; Amazon v2 16k full/cf; Amazon v3 latest-leftover 16k full/cf; Amazon pipeline 16k full | **8k contiguous zipper** (not 4k, not raw, not intersecting-artifact). Source-correct. MiniLM top-16 insufficient. | **retrieval candidate AFTER re-validation**, not auto-admit. Keep fail-closed on Canonical/strict. |
| Amazon v3 `projected` and `banded_leftover` 16k (all three views) | 8k contiguous **pass** (needed span 8,781–8,784). | Still **not strict-admit**: no completed 12/12 dense audit on this layout; leftover repair is local to 16k. |
| All 16k ordered views | 8k **pass** (chronology spreads gold). | Cannot rescue world-atomic while full/cf zipper siblings exist. |
| All 32k / 64k / 128k views | Not 8k zipper. Needed contiguous span 17k–131k. | **strict hold** until a layout exists whose **16k full/cf also pass 8k** and a fresh 12-view dense audit is green. Not retrieval (answer is not short-window). Not reject-for-wrong-facts. |
| CF views | Source-correct +1 revenue. | Same window routing as the matching full view. |

World-level: Meta v1 and Amazon v2 / v3-latest-leftover / pipeline stay
**reject-from-strict**. Amazon v3 projected/banded 16k leftover is the
Micron-class *direction* for the 16k zipper only; it is not a promotion
receipt.

## Boundary

- Diagnostic. `production_eligible=false`. Packed parents are not inventory.
- Do not promote. Do not invent a retrieval profile.
- Do not rematerialize Alphabet/Microsoft/NVIDIA. Do not fetch Tesla.
- Next admissible 16k zipper repair is leftover thickness / chronology
  (Micron-class), not a gate drop.
