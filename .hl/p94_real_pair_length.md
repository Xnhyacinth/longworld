# P94 real Wiki pair length views

This bounded lane composes **reader views**, not new independent questions. It
starts from the four frozen eval `table_pair_earlier_year` tasks in
`data/candidates/p94_wiki_pair_pilot_v1/merged`, and inserts complete frozen
Wiki pages from the same eval split between the two named source pages. The
source pages, question, answer, semantic task ID and source group are unchanged.

## Frozen inputs and execution

`configs/p94_real_pair_length_v1.json` pins the P94 structural source pool,
native pair manifest and its assistant-mask receipt. Every selected filler
also records its source snapshot pin, page title, document ID and exact text
hash. The output manifest pins the composer code and tokenizer revision.

```bash
UV_LINK_MODE=copy uv run --offline python scripts/compose_p94_real_pair_length.py \
  --config configs/p94_real_pair_length_v1.json \
  --output data/candidates/p94_real_pair_length_v2 --verify-only
cat data/candidates/p94_real_pair_length_v2/manifest.json
less -R data/candidates/p94_real_pair_length_v2/sample_index.jsonl
less -R data/candidates/p94_real_pair_length_v2/audit.jsonl
```

The final v2 output contains 12 eval reader views of four unchanged semantic
tasks, exactly four views per length bin: 32K, 64K and 128K. Measured full
chat length is 34,930–140,100 tokens. The two named year-cell evidence spans
have a measured tokenizer extent of 31,377–137,309 tokens; 32K views use two
filler pages, 64K views nine and 128K views twelve. No generated/padded page
text is used. There are zero new independent semantic tasks and zero accepted
train views in this lane.

For each final reader, the composer replays both named founding-year table
cells from the final visible source pages, independently masks each named year
cell, and checks that its scoped table parser no longer finds the masked side.
It rejects fillers with overlapping source titles, cross-split titles, duplicate
filler titles or any frozen target entity label/alias. It maps both evidence
spans and the question into the final chat tokenizer offsets, then checks the
exact assistant-only loss mask with the training tokenizer. The audit records
the full-reader SHA, filler layouts, evidence token spans and query position.

This proves a bounded table-cell reader dependency in the final bytes. It
does **not** prove that unrestricted prose elsewhere cannot support the same
year, or that a model actually uses the distant evidence. The four source
tasks are eval only; this lane is useful as a length/position diagnostic and
must remain deduplicated by `semantic_task_id` when joined to a larger bank.

`data/candidates/p94_real_pair_length_v1` was the unpinned exploratory run;
v2 is the code-bound replay target. The school dense-scan tasks were not
lengthened because inserting additional year tables can change the complete
set answer and requires a separate scoped global-scan composer.
