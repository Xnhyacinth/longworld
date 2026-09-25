# P94 real Wiki table-scan train length views

This lane lengthens 12 **existing** train `dense_table_interval_scan` tasks
from the frozen school source group in
`data/candidates/p94_wiki_delta_pool_v1/merged`. The question names exactly
one source title (`List of schools in Patna`); other intact frozen Wiki pages
are appended after the original source context. Those pages do not enter the
title-scoped candidate universe. No synthetic filler or repeated page text is
used, and the answer and semantic task ID are unchanged.

## Input and replay

`configs/p94_real_scan_length_v1.json` pins the source pool, native merged
manifest, source group and expected count of 12 scan tasks. The output pins
the composer and its shared P94 page-selection helpers by code hash, together
with all source reader/index/audit file hashes and tokenizer revision. Each
selected filler page carries its snapshot pin, document ID, title and text
hash in the row audit.

```bash
UV_LINK_MODE=copy uv run --offline python scripts/compose_p94_real_scan_length.py \
  --config configs/p94_real_scan_length_v1.json \
  --output data/candidates/p94_real_scan_length_v1 --verify-only
cat data/candidates/p94_real_scan_length_v1/manifest.json
less -R data/candidates/p94_real_scan_length_v1/sample_index.jsonl
less -R data/candidates/p94_real_scan_length_v1/audit.jsonl
```

The bounded pilot produced 36 train views of 12 original semantic tasks:
12 views each in the 32K, 64K and 128K length bins. Actual full chat lengths
are 34,676–34,763, 90,079–90,166 and 134,393–134,480 tokens respectively.
The named table's 15 year cells span 2,670 tokenizer tokens in all variants;
the **last necessary table cell to query** gap grows from 22,646 to 78,049
to 122,363 tokens. This is long-range title-scoped retrieval plus a dense
within-page scan, not a claim that evidence cells themselves span 128K.
The views add zero independent semantic tasks. The same frozen pages are
reused across views and are recorded in each sample audit.

For every final reader, the compiler verifies that the original four source
pages still match the frozen snapshot, parses the complete eligible row set
from the named table, and replays both a valid-hit insertion and a same-schema
near miss in the final reader bytes. It maps all 15 year cells and the query
through the pinned chat tokenizer and checks the exact assistant-only loss
mask using the training helper. The original native reader is mask-checked
again before each set of length views. Complete document-title split checks
reject cross-split or repeated filler pages.

The certificate covers the named table's plain four-digit-year rows. It does
not certify unrestricted natural-language evidence equivalence or an actual
model improvement. The output is a research candidate with
`train_ready=false`; no GPU training was launched.
