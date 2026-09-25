# P95 frozen Wiki table sweep

The table sweep is a parameterized batch over pinned source pools. It reads the
P92 router's consolidated pool and the P94 structural merge v2 pool, deduplicates
frozen snapshots, checks train/eval page-title conflicts, discovers complete
year tables with the existing strict parser, and compares deterministic task IDs
with the complete P94 sharded candidate bank before materializing readers. It
also loads prior interval program audits to catch older tasks with different IDs.
Independent table compilation runs in a process pool. The only accepted answer
shape is a complete named-entry set plus count for a closed numeric interval.
The current compiler also rejects repeated reader-visible `(section heading,
column header)` keys on one page, including a malformed second table; the
question cannot distinguish those tables. The authoritative output is v2.
Historical v1 reader bytes happen to match, but its older compiler pin lacks
this ambiguity gate and must not be used for new batches.

Run and replay:

```bash
UV_LINK_MODE=copy uv run python scripts/run_p95_wiki_table_sweep.py \
  --config configs/p95_wiki_table_sweep_v1.json \
  --output-dir data/candidates/p95_wiki_table_sweep_v2 --workers 4
UV_LINK_MODE=copy uv run python scripts/run_p95_wiki_table_sweep.py \
  --config configs/p95_wiki_table_sweep_v1.json \
  --output-dir data/candidates/p95_wiki_table_sweep_v2 --workers 4 --verify-only
```

Actual output: [manifest](../data/candidates/p95_wiki_table_sweep_v2/manifest.json),
[index](../data/candidates/p95_wiki_table_sweep_v2/sample_index.jsonl),
[native audits](../data/candidates/p95_wiki_table_sweep_v2/audit.jsonl),
[page-level parser audits](../data/candidates/p95_wiki_table_sweep_v2/page_audit.jsonl),
[rejections](../data/candidates/p95_wiki_table_sweep_v2/rejected.jsonl), and
[final mask audit](../data/candidates/p95_wiki_table_sweep_v2/mask_audit.json).
Inspect a final reader with
`less -R data/candidates/p95_wiki_table_sweep_v2/eval.jsonl`.

The two pinned pools contain 57 snapshot groups and 200 page occurrences (190
distinct page titles/IDs). The strict parser admitted 2 complete year tables:
35 Japan national parks and 14 Norwegian university colleges. The other 66
potential table headers failed safely: 41 row-width mismatches, 15 non-plain
years, 7 ambiguous subjects, and 3 tables with fewer than 8 rows. Sixteen
interval programs were planned. Eleven were existing tasks, one failed the
non-degenerate interval criterion during the final reader replay, and four
new independent tasks were accepted. All four are **eval-only**, one education
world/topic, 22,806–22,873 full-chat tokens, with 265-token local evidence
extent and a 20,857-token first-evidence-to-query distance. The final Qwen
chat-template assistant mask was independently checked
for all 4/4 rows; output remains `train_ready=false`.

This sweep establishes that simple domain/topic substitution over the current
frozen Wiki pools does not itself scale dense numeric supervision: only 2/190
distinct pages expose complete tables under this strict schema, and the novel
yield is concentrated in one existing world. The certificate is bounded to a
visible year-cell hit/near-miss and complete parser scan. It does not prove
absence of all paraphrased prose support or model-level 22K-token dependence.
Increasing task quotas on these same two tables would mostly repeat answers;
the next source intake should target closed, typed tables or use the native
lookup/pair recipes on other supported source structures.
