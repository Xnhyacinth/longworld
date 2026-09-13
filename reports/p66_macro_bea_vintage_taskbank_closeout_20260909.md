# P66 Macro BEA vintage taskbank closeout

Date: 2026-09-09

Outcome: **verified local candidate batch; strict/release/production NO-GO**.
The P66 builder derives 324 local SFT views from the already authenticated BEA
GDP/GDI vintage workbook. It does not fetch, clone, pad, promote, or mutate any
earlier phase. A four-process pool constructs the independent source worlds;
the pinned Qwen tokenizer performs exact serial packing because the tokenizer
is not shared across worker processes.

## Inventory and isolation

- 36 independent `(series_id, period)` worlds: four source series across the
  nine periods from 2003Q1 through 2005Q1.
- 108 semantic tasks and 324 length variants: 108 each at exact 64K, 128K, and
  256K numeric ranges.
- 243 train views from three complete series groups and 81 eval views from the
  held-out `BEA_REAL_GDI_PERCENT_CHANGE` series. No source group crosses the
  split boundary.
- Three mechanically executable families, 108 views each:
  `full_revision_path`, `revision_change_summary`, and
  `largest_absolute_revision`.
- 36 unique contexts per length band, 108 total; 202 to 872 unique visible
  source records per context. No capacity or full-chat rows were rejected.

| Measure | Minimum | Median | Maximum |
| --- | ---: | ---: | ---: |
| Context tokens | 65,145 | 130,925.5 | 260,000 |
| Full HF chat tokens | 65,280 | 131,159 | 261,050 |
| Compact target-only tokens | 7,121 | 9,006 | 9,819 |
| Target observations | 12 | 15 | 16 |

The 256K attempt passed authentic unique-record capacity and full-chat bounds:
all 108 views use 256,000 to 260,000 context tokens and remain at or below
262,144 complete-chat tokens. This establishes a usable local capacity slice,
not strict 256K dependency.

## Dependency controls

- Latest-only failed on 324/324 views.
- Every 4K, 8K, and 16K complete-record window failed on 215/324 views. Window
  hits were 73, 97, and 109 respectively; these 109 views remain retrieval
  candidates.
- Remove-one changed every target observation for 216/324 views. The largest
  revision family has irrelevant nonmaximal transitions, so its 108 views do
  not receive an all-record necessity claim.
- The oracle target-only compact representation reproduced 324/324 answers in
  7,121 to 9,819 tokens. Therefore `strict_long_dependency_verified=0` and the
  entire batch remains `training_release_eligible=false` and
  `production_eligible=false`.

The compact result is the controlling scientific limit: these are useful
long-input curriculum views with real revision-history integration, but they
do not increase the strict long-dependency inventory. There are 215 integration
candidates after the short-window controls and 109 retrieval candidates.

## Provenance and replay

The source manifest and workbook are pinned at SHA-256
`d7b699557fb6ff63fdeabb2fd3e1a28502b8335f88fa279041d6c4b621ae7b27`
and `c6b10cc799e213974cb73fb7083221e71b8298e98cb5c3b36153a42cd09af3fe`.
The derivative retains authorization record `local-bea-probe-20260831`, the
BEA public-domain license statement, and all existing nonproduction flags.
Derivative generation record: the user's explicit 2026-09-09 instruction in
the active session authorizes local multiprocess synthesis and filtering for
this P66 batch. This local derivative record does not alter the earlier fetch
authorization or authorize promotion, publication, or release.

Native build followed by native `--validate` replay produced byte-identical
members and returned `PASS` for 324 views and 36 worlds. Focused Ruff passed,
three program tests passed, and explicit inventory assertions confirmed unique
sample IDs, semantic variant grouping, length bounds, full-chat bounds, and
source-group split isolation.

Artifact hashes:

- config: `7071ad6000c665ca4764dcdf03c91b6eb05814c780bd062c9b7c14aabeac308a`
- core: `36815c2d1450369bdd0b6d98782468c2ae1cb6f85bcacb7c58c06767444ed87c`
- builder: `255dd8f6f93ffdfbd4257174aeab48c35790a46ee82c2da623d20d7e539be49c`
- receipt: `abb0d9f6ea6597ba5a3745b0b3e1baddecd44a1b7349d7ccd09ee1d4126c9b66`
- tasks: `6193c05cb60b3f76e20e8718d98dcafc30febb9a6260bc9279f28607e0371703`
- SFT views: `2a8cbec4ccd780153f1d6edc72714207e49f19fadaa85d4201e65e52735f63f1`

The output is `data/candidates/p66_macro_bea_vintage_taskbank_v1`.
