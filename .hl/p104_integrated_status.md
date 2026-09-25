# P104 current candidate index and selected reader pack

`data/candidates/p104_candidate_refs_v1` extends P103 only with the
quality-curated P104 real-paper reference shard. The raw P104 three-task
paper batch and the P104 Wiki grid audit are **not** indexed as admitted
tasks. The paper gate removed one unexpanded-TeX gold and one answer that
appeared elsewhere in the final reader; it kept one train task from a new
Sparks source world. Full reader-file hashes verify.

The bank now has **11,927 candidate views / 11,402 globally independent
semantic tasks**, with 9,169 train and 2,758 eval views across 1,374 typed
source/world groups. Source-kind views: 6,116 controlled simulation, 24
grounded simulation, 2,576 real Wiki, 2,314 real finance, 881 real code
workflow, nine real paper source and seven paper revision. The extra paper
view is `128–256K`, making bank length bins 2,606 `<32K`, 3,444 `32–64K`,
3,919 `64–128K` and 1,958 `128–256K`.

The P104 source-aware selection uses the same caps as P103 and includes the
new curated paper task: **856 independent tasks**, 600 train and 256 eval,
from 226 groups. Selected source kinds are 427 real Wiki, 192 real finance,
110 real code workflow, 103 controlled simulation, eight grounded
simulation, nine real paper source and seven paper revision. Physical lengths
are 239 `<32K`, 224 `32–64K`, 266 `64–128K`, 127 `128–256K`. The 856
readers contain 64,084,177 final-chat tokens and 81,990 assistant-supervised
tokens. The selected output remains `train_ready=false`; candidate admission
is not training or model-gain evidence.

The paper source route is now parameterized over a frozen inventory and uses
four workers, but the available inventory had only five previously unseen
works. Two yielded structural references, three raw questions passed the
old P96 source-level filters, and one passed the new final-reader quality
gate. Its two recorded evidence spans are 4,857 final-template tokens apart;
the later span ends 90,731 tokens before the query. This is bounded raw-TeX
source-navigation evidence, not an unrestricted minimum-proof claim. P104
Wiki raw-revision acquisition froze 30 pages and found 27 structural table
candidates, but admitted **zero** reader tasks; P105 is checking cell-level
visible evidence before any of them enters a new shard.

Inspect and replay:

```bash
cat data/candidates/p104_candidate_refs_v1/manifest.json
cat data/candidates/p104_balanced_selection_v1/manifest.json
cat data/candidates/p104_balanced_materialized_v1/manifest.json
cat data/candidates/p104_paper_reference_curated_v1/manifest.json
cat data/candidates/p104_paper_reference_curated_mask_v1/manifest.json
less -R data/candidates/p104_balanced_materialized_v1/sample_index.jsonl
less -R data/candidates/p104_paper_reference_curated_v1/quality_ledger.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p104_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p104_balanced_selection_v1.json \
  --output data/candidates/p104_balanced_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py \
  --index data/candidates/p104_candidate_refs_v1 \
  --selection data/candidates/p104_balanced_selection_v1 \
  --selection-config configs/p104_balanced_selection_v1.json \
  --output data/candidates/p104_balanced_materialized_v1 --verify-only
```
