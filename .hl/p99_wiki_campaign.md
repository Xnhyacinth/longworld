# P99 Wiki campaign: shard acquisition, global source gate, native compilation

`scripts/run_p99_wiki_campaign.py` coordinates an arbitrary selected set of
P97-planned P93 shard configs. It does not invent source text or task recipes.
The catalog and P97 manifest determine domain/topic/query substitutions; P93
freezes source revisions; the P97 overlap and gate code excludes a whole source
bundle if **any** page title or URL repeats the P92 historical Wiki router,
the P95 accepted pool, or an earlier shard. Both split conflicts and same-split
duplicates are recorded. The gate passes only pinned snapshot references to
`scripts/run_source_pool_batch.py`, which decides legal native operations and
records unsupported cells and rejected reader rows.

The acquisition base and global novelty universe are deliberately different:
P97 shards were acquired against the P94 pool, whereas the P99 gate checks
P92 router **plus** the later P95 merged pool. The coordinator verifies the
P97 plan and every frozen P93 manifest against the exact shard config hash.
`--acquire` is the only switch permitting missing shards to make HTTP requests;
it is bounded by `intake_workers` 1–4. Existing complete attempts are reused;
an interrupted, manifest-less attempt is retained and a new numbered attempt
is made on retry. Native jobs use the existing batch runner's separate 1–4
worker limit and resume lock. `--mode verify` requires every native receipt
to exist before invoking the runner's read-only completed-batch replay.

The 3-shard no-network gate pilot is
`data/capability_records/p99_wiki_campaign_p97_partial_pilot_v2/`. It saw
101 frozen groups / 317 pages / 12,857 facts and admitted 80 groups / 235 pages
/ 9,367 facts after rejecting 21 groups, 11 with cross-split overlap. Its
native probes predicted 33 jobs and recorded 181 unsupported cells. This is a
source gate pilot; no P99 native batch was run for it.

The full four-shard no-network gate is
`data/capability_records/p99_wiki_campaign_p97_full_v3/`. It saw 156 frozen
groups / 502 pages / 17,999 facts and admitted 114 groups / 339 pages /
11,609 facts after rejecting 42 groups, 19 with cross-split overlap. The
accepted groups span 18 domain labels and 90 `(domain, topic)` pairs: 91 train
and 23 eval groups. Their frozen structural probes predicted 48 native jobs
(46 lookup groups, 2 scan groups, zero pair groups) and reported 272
unsupported cells. The authoritative
identities and rejections are in `overlap.json`, `gate/source_pool.json`,
`native_probes.json`, and `gate_result.json`. These numbers are source and
structure counts, **not** independent reader tasks or training rows.

The P99 v3 gated `source_pool.json` and the separately executed P97 gated
`source_pool.json` have the same SHA-256
`094353d647ae888821ede5683fd912d125f336018317ffc9364b40b7cd840268`.
The P97 native batch at `data/candidates/p97_wiki_vocab_native_v1/result.json`
completed 48 jobs (47 with candidates), 741 independent native tasks/views,
519 unsupported cells and 219 rejected rows. P99 did **not** run or adopt
that batch in its own `result.json`; this equality only establishes that the
source pool bytes are identical. P97 owns the downstream native and unified
mask checks.

```bash
cat data/capability_records/p99_wiki_campaign_p97_full_v3/gate_result.json
less -R data/capability_records/p99_wiki_campaign_p97_full_v3/overlap.json
UV_LINK_MODE=copy uv run --offline python scripts/run_p99_wiki_campaign.py \
  --config configs/p99_wiki_campaign_p97_full_v1.json \
  --output-dir data/capability_records/p99_wiki_campaign_p97_full_v3 \
  --mode gate
```

After any separate P97 native batch has finished and resource use is clear,
the same frozen-receipt campaign can compile/resume its own native batch:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/run_p99_wiki_campaign.py \
  --config configs/p99_wiki_campaign_p97_full_v1.json \
  --output-dir data/capability_records/p99_wiki_campaign_p97_full_v3 \
  --mode run
UV_LINK_MODE=copy uv run --offline python scripts/run_p99_wiki_campaign.py \
  --config configs/p99_wiki_campaign_p97_full_v1.json \
  --output-dir data/capability_records/p99_wiki_campaign_p97_full_v3 \
  --mode verify
```

P99 stops at native source-batch candidates. It does not assert final chat
token offsets, loss-mask correctness, reader-text necessity, licensing,
training readiness, or model improvement. Normalization, final reader checks
and sample selection remain separate gates. The current batch planner exposes
Wiki lookup, scan and pair operations; replacing domain and topic vocabulary
alone cannot create report, book, code, or agentic task mechanisms.
