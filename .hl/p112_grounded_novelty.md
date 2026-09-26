# P112 grounded RFC novelty against the shared bank

The P112 official-rule generator emits 24 independently audited state tasks
from two frozen RFC clauses. Two seed-zero rows are byte-equivalent to P109
samples already in the global bank: same sample/semantic IDs, source group,
split, operation, context hash, answer hash and final token counts. The
final reader messages are also identical. The sharded index correctly
rejected their duplicate sample IDs.

`p112_unified_novelty.py` binds the full prior index and producer manifest,
compares the final reader for every colliding sample ID, drops only exact
prior identities, and raises on any conflicting collision. Its
immutable ledger records the two drops. The novel shard therefore has **22
net independent tasks**, 11 below 32K and 11 at 64–128K, all train. Separate
all-row final-reader/mask audit passed 22/22. The original 24-row RFC shard
remains intact as a producer receipt; only the net shard is in the new
global candidate index. These still represent two real rule sources and one
numeric-minimum operation, with simulated private-state diversity.

```bash
cat data/candidates/p112_grounded_rfc_novel_v1/manifest.json
less -R data/candidates/p112_grounded_rfc_novel_v1/rejected.jsonl
cat data/candidates/p112_grounded_rfc_novel_mask_v1/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/p112_unified_novelty.py --base-index data/candidates/p112_quality_base_refs_v1 --source-dir data/candidates/p112_world_grounded_rules_v2 --output data/candidates/p112_grounded_rfc_novel_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py data/candidates/p112_grounded_rfc_novel_v1 --all --output data/candidates/p112_grounded_rfc_novel_mask_v1 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p112_unified_novelty.py
```
