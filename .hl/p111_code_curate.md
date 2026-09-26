# P111 added-code curator for the P108 Deno PR pairs

The P108 added-code native and unified outputs already passed the two-witness
content intervention and final reader mask, but the P111 selector requires a
separate curator membership receipt for every new code-content native receipt.
P111 reuses the P107 curation rules and checks source group, sorted PR pair,
semantic task, split, reader answer, two witness positions and the raw all-mask
chain. It additionally pins and audits the P107 raw/curated/mask chain so the
new Deno pairs cannot repeat either the P99 or P107 prior pools.

The [curated manifest](../data/candidates/p111_code_curated_v1/manifest.json)
records gross 2 → net 2 independent train readers, with no prior PR-pair
rejections. The exact pairs are Deno `#36482/#36785` and `#36696/#36785`.
The P107 prior comparison covered 82 raw PR pairs and 82 semantic IDs; the
P99 prior is checked by the reused P107 curator. The
[quality ledger](../data/candidates/p111_code_curated_v1/quality_ledger.jsonl)
records both acceptances and their source/PR identities. The
[all-mask receipt](../data/candidates/p111_code_curated_mask_v1/manifest.json)
passed 2/2 final readers, 375,454 full-chat tokens and 98 supervised tokens.
The existing selector's `_curated_code_membership` returned exactly two
members for these pinned receipts.

The curated manifest is SHA
`2ea9f715ec479a0a4b7220c91f83f00597dff689668e7dce200ef97fc9746417`;
the all-mask manifest is SHA
`90a69051250c67c34354cbf121422a16f5132c540513d7b0d73fb4c92552c00e`.
No source bytes or reader text are rewritten by this curation. Its certificate
is limited to the P99 finite added-code grammar, filename-preserving added-line
removal and one 16K raw window. The result remains `train_ready=false` and no
training was launched.

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p111_code_curate.py \
  --config configs/p111_code_curate_v1.json \
  --output-dir data/candidates/p111_code_curated_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py \
  data/candidates/p111_code_curated_v1 --all \
  --output data/candidates/p111_code_curated_mask_v1 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p111_code_curate.py
cat data/candidates/p111_code_curated_v1/manifest.json
less -R data/candidates/p111_code_curated_v1/quality_ledger.jsonl
```
