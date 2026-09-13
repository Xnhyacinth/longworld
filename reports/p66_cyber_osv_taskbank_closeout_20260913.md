# P66 Cyber OSV local taskbank closeout

## Result

The bounded P66 build produced **36 local training candidates** over **6
source-disjoint worlds**: 18 train and 18 eval, with 12 tasks in each of the
exact 64K, 128K, and 256K bands. The source is the commit-pinned PyPA
advisory-database archive at `c4a1fde8cb41b3b5180fed3561596c9ce6de99ad`
(`4e40288b...8195a`), whose embedded CC-BY-4.0 license was independently
checked at `9ba9550a...9c11b`. Raw archives were processed in memory and were
not persisted.

The historical P51 preflight prohibition remains unchanged. The new P66
authorization record supersedes that hold only for this local candidate scope,
based on the project owner's explicit active-thread instruction. It prohibits
raw archive persistence, source-owner approval claims, public release,
promotion, and production use.

## Inventory and lengths

The archive contains 7,342 advisories. The existing fail-closed filter retained
1,851 advisories, exact dedup retained 1,851, and five-word-shingle Jaccard
filtering retained 1,849. The bounded batch used 1,748 unique advisories and
left 101 unused. The questions target 144 unique advisories (8.24% of the
visible records); the remaining 1,604 are authentic distractors and are not
claimed as task dependencies. No advisory appears in more than one world.

| Split | Band | Advisories | Context tokens | Tasks |
| --- | --- | ---: | ---: | ---: |
| train | 64K | 121 | 64,037 | 6 |
| train | 128K | 245 | 128,546 | 6 |
| train | 256K | 481 | 256,285 | 6 |
| eval | 64K | 129 | 64,402 | 6 |
| eval | 128K | 261 | 128,089 | 6 |
| eval | 256K | 511 | 256,105 | 6 |

Package-connected components were token-balanced before packing: 193 train
packages and 210 eval packages, with zero overlap. Full-chat tokenization used
`scripts.train_sft.tokenize_assistant_only` without truncation for all 36 rows;
lengths range from 64,236 to 257,136 tokens and sum to 5,401,562 tokens.

## Task and dependency controls

The taskbank contains 12 lifecycle-matrix tasks, 12 exact GIT-fixed to GitHub
FIX-reference joins, and 12 temporal-order tasks. Every task replays exactly.
Removing any queried advisory makes the task return `UNKNOWN`; single-record
and arbitrary last-packed-record controls solve 0/36. Packing is digest-based,
so this is not a temporal latest-record control. No contiguous 4K, 8K, or 16K window
contains the complete queried target set (0/36 at each window size).

All 36 tasks are solvable from a compact context containing only the four
queried advisory records. Therefore these are useful long-context retrieval and
multi-record integration candidates, but they are **not certified strict long
dependency**. The receipt reports `strict_long_dependency_verified_count=0`,
`train_ready=false`, `production_eligible=false`, and `promoted=false`.

Each world is reused for six independently queried rows. This 6x context reuse
is explicit in the receipt and must not be counted as six new worlds or six
times the unique source-token capacity.

## Reproduction

```bash
uv run pytest -q tests/test_p66_cyber_taskbank.py tests/test_train_sft_masking.py
uv run ruff check longworld/core/p66_cyber_taskbank.py \
  scripts/materialize_p66_cyber_taskbank.py tests/test_p66_cyber_taskbank.py
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run python \
  scripts/materialize_p66_cyber_taskbank.py \
  --config configs/p66_cyber_osv_taskbank_v1.json \
  --output data/candidates/p66_cyber_osv_taskbank_v1 --workers 4 --validate
```

The focused test set passed (10 tests including the shared assistant-only
masking suite). Ruff passed. Native source replay reproduced every emitted byte;
the validation record reports `native_replay_identical=true`. Build receipt
SHA-256: `82b496438abfeecf7ed9f9a928503d29bd86839964bccadc128b9b172ef885ba`.
