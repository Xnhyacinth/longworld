# P91 unified candidate integration

P91 adds three real single-table numeric complete-set tasks to the existing
shared candidate bank. The native source, parser, row-level audit and mask are
recorded in `.hl/p91_wiki_numeric_table.md`. The canonical adapter now checks
the native receipt, answer, all-row audit count, changed-answer intervention,
source group, operation and final reader bytes before writing a shard. Its
reader rows contain only `sample_id` and user/assistant messages.

The current reference index is
`data/candidates/p91_final_candidate_refs_v2/manifest.json`: **8,088 reader
views, 7,935 independent semantic tasks, 1,077 source/world groups**, with
6,278 train and 1,810 eval views. It has 18 domain labels, 40 topic labels,
37 operation labels and 176 occupied split × source kind × operation × actual
length cells. The three new tasks share the same frozen Wiki source group as
P89's L1 lookups; they do not increase the source-world count. All three are
32–64K final-chat examples. The P91 shard rebuilt byte for byte from native
receipts, the three-shard index passed full reader SHA-256 verification, and
the three final canonical readers passed pinned-Qwen assistant-only mask
replay: 110,909 full-chat tokens and 129 supervised tokens.

The source-aware selection over this new index has 1,169 independent tasks,
506 source groups and all 176 occupied cells, with 18 domains, 40 topics and
37 operations. Its parameters are an inspectable candidate selection, not a
training mix. All artifacts remain `train_ready=false`; no SWIFT/Megatron
loader step, GPU training or model-gain test was run. The P91 evidence spans
one table in about 1.6K tokens, even though the query is about 36.7K tokens
after the first row. This is long-distance retrieval plus full-table scan,
not cross-document proof length.

Inspect the receipts and final readers:

```bash
cat data/candidates/p91_wiki_numeric_table_v5/manifest.json
cat data/candidates/p91_unified_scale_numeric_shard_v2/manifest.json
cat data/candidates/p91_final_candidate_refs_v2/manifest.json
cat data/candidates/p91_numeric_final_mask_audit_v1/manifest.json
cat data/candidates/p91_balanced_selection_v1/manifest.json
less -R data/candidates/p91_unified_scale_numeric_shard_v2/sample_index.jsonl
```

Recheck the normalized shard, full reader references and selected masks:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/merge_p86_native_candidates.py \
  --paper-dir data/candidates/p86_frozen_paper_batch_v2 \
  --state-dir data/candidates/p86_state_shared_scale_v2 \
  --hybrid-dir data/candidates/p87_hybrid_rfc9114_pilot_v6 \
  --wiki-numeric-dir data/candidates/p91_wiki_numeric_table_v5 \
  --output data/candidates/p91_unified_scale_numeric_shard_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p91_final_candidate_refs_v2 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p91_balanced_selection_v1.json \
  --output data/candidates/p91_balanced_selection_v1 --verify-only
```
