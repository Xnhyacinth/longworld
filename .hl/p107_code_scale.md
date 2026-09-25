# P107 frozen CodeForge capacity and added-line content expansion

P107 expands only the remaining independent tasks in existing, pinned real
CodeForge worlds. It leaves the P99 compiler and all P99 outputs unchanged.
The P99 bank loader rehashes the native bank receipt, its files and every
source binding before compiling. A new question is admitted only if two
different merged PR heads supply separate identifier witnesses on added code
lines, the final reader answer changes when added lines are removed while
filenames remain, the selected witnesses span over 16,384 tokenizer tokens,
and the final assistant-only mask is valid. This is a bounded content proof,
not a global minimum-proof or model-learning result.

The frozen bank inventory has 16 `world.json` snapshots but only nine unique
`(source_group, split, world_id)` worlds. Seven P77/P78 snapshots duplicate
their P64 world IDs. P99 already included all nine source groups. The one
unused capacity was caused by `max_tasks_per_repository=12` on Oxc: its
20 episodes allow 190 PR pairs, but P99 stopped after 12 acceptances and six
rejections, leaving 172 pairs unexamined. Every other bank had exhausted its
available pair combinations. The [capacity and four-process replay
receipt](../data/capability_records/p107_code_parallel_replay_v1/manifest.json)
has SHA-256 `2d7a866916b8c02df1acaea42f7b943d544c1524e5e911ce0bc2ccc994c5b9c7`.

The reader's user message starts with the **question**, followed by
`Source records:` and the long source context. The 16K gate and the
17,191–75,856 token range below measure the distance **between the two
selected added-code witnesses inside the source records**. They do not
measure a last-evidence-to-trailing-query gap: there is no trailing query in
this reader layout. Query placement remains the frozen P99 contract.

P107 removed that per-repository cap for the **same frozen Oxc world** and
ran the unchanged P99 compiler over all 190 PR pairs. It yielded 82 raw
content-proven tasks and 108 rejected pairs: 99 lacked two distinct
content-only added-code identifiers and nine had selected evidence spans at
or below 16K. Twelve of the 82 were precisely the P99 tasks already exposed
to the model. The P107 curator requires both the semantic task ID and
`(source_group, unordered PR pair)` to agree with prior exposure, verifies
the raw native→unified→all-mask chain and retains **70 new independent
tasks**. The [curated manifest](../data/candidates/p107_code_curated_v1/manifest.json)
has SHA-256 `55abbb13d73cb0c65a75c8c53f4e4c2d150c4b657addba413fad996e53a1997d`;
the [independent final all-mask
receipt](../data/candidates/p107_code_curated_mask_v1/manifest.json) has
SHA-256 `159d2ec0e6cbb8f555e76b5e7416e54c0382f8034ec507876de072e6ba9ee218`.

| Measure | P107 final |
| --- | ---: |
| New source groups / source worlds | 0 / 0 |
| Reused source group | `https://github.com/oxc-project/oxc` |
| Domain / topic / operation | `codeforge` / `oxc` / `added_line_cross_pr_complete_set` |
| Raw → prior overlap → new tasks | 82 → 12 → **70** |
| Train / eval | 70 / 0; Oxc's pinned repo split is train |
| Exact full-chat lengths | 35,821–110,904 tokens |
| 32–64K / 64–128K | 48 / 22 |
| Selected two-witness span | 17,191–75,856 tokens |
| Full-chat / supervised tokens | 4,235,989 / 3,776 |
| Independent final assistant mask | **70/70** |

To verify that the same world and task can be scaled without changing its
answer, P107 partitioned the 190 PR pairs across four processes (48, 48, 47,
47). Each process loaded the pinned Oxc bank and pinned tokenizer, called the
unchanged P99 `compile_bank` on its assigned two-episode scopes, and returned
only reader/index/audit hashes or a rejection hash. All **190/190** pair
outcomes exactly matched the serial P99 native output; no reader bodies were
copied into the orchestration receipt. The final 70 readers were separately
retokenized and all assistant masks passed. No GPU job was run and
`train_ready=false` remains on all outputs.

This adds supervised examples inside **one** existing code world, not repo,
domain, source-style or operation diversity. The content certificate only
covers the specified whole-identifier grammar, two selected witnesses and
one contiguous 16K raw-context window. The early question means this is
distributed code-evidence integration rather than late-query retrieval. It
does not exclude an arbitrary semantic shortcut, multi-window retrieval or
pretraining knowledge, and it does not prove model improvement. A separate
Deno source bundle exists in
the older frozen inventory, but current source-role trust replay rejects its
attestation; no Deno bank was constructed by bypassing that validation.
Broader code-world scaling requires newly acquired, source-attested repo/PR
histories under the project's approved GitHub export and source-role trust
flow, followed by this same added-line content and final-reader mask gate.

Reproduce from the project root:

```bash
cat data/capability_records/p107_code_parallel_replay_v1/manifest.json
cat data/candidates/p107_code_curated_v1/manifest.json
less -R data/candidates/p107_code_curated_v1/quality_ledger.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/p99_code_content_tasks.py \
  --config configs/p107_code_oxc_expansion_v1.json \
  --output data/candidates/p107_code_oxc_raw_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p99_code_to_unified.py \
  --config configs/p107_code_oxc_expansion_v1.json \
  --native-dir data/candidates/p107_code_oxc_raw_v1 \
  --output data/candidates/p107_code_oxc_unified_raw_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p107_code_curate.py \
  --config configs/p107_code_curate_v1.json \
  --output-dir data/candidates/p107_code_curated_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py \
  data/candidates/p107_code_curated_v1 --all \
  --output data/candidates/p107_code_curated_mask_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p107_code_parallel_replay.py \
  --config configs/p107_code_parallel_replay_v1.json \
  --output-dir data/capability_records/p107_code_parallel_replay_v1 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p107_code_scale.py
```
