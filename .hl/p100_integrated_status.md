# P100 integrated candidate index and reader selection

The current research candidate index is `data/candidates/p100_candidate_refs_v2`.
It combines the previous P96 index with three new, separately audited shards:
P97 real Wiki vocabulary, P99 real code content, and P100 real Wiki categorical
complete-set scans. Full reader-file hash verification passed. The index has
**11,912 reader views / 11,387 global independent semantic tasks**, 9,162 train
and 2,750 eval views, across 1,370 typed source/world groups. Of those groups,
369 expose more than one operation string. Every candidate remains
`train_ready=false`.

| Source kind | `<32K` | `32–64K` | `64–128K` | `128–256K` | Views |
| --- | ---: | ---: | ---: | ---: | ---: |
| Controlled simulation | 709 | 2,549 | 2,034 | 824 | 6,116 |
| Grounded simulation | 8 | 16 | 0 | 0 | 24 |
| Real code workflow | 13 | 152 | 327 | 389 | 881 |
| Real annual reports | 274 | 52 | 1,362 | 626 | 2,314 |
| Real paper revisions | 0 | 7 | 0 | 0 | 7 |
| Real paper sources | 0 | 3 | 5 | 0 | 8 |
| Real Wiki | 1,589 | 664 | 191 | 118 | 2,562 |

The index exposes 28 domain labels, 104 topic labels and 123 operation
strings. The operation count includes selector→target program signatures, so
it is not a count of independent cognitive abilities. The total physical
length bins are 2,593 `<32K`, 3,443 `32–64K`, 3,919 `64–128K`, and 1,957
`128–256K`. Physical context length is not minimum necessary evidence span.

The P97 acquisition genuinely scales source vocabulary: 144 queries in four
parallel shards led to 114 globally novel frozen source groups, then 741
candidate tasks from 45 productive worlds. Their final reader masks passed
741/741. But 716/741 are single-cell lookups, 25 are dense table scans, and
zero are cross-page pair tasks. This is source/topic scaling with a visible
capability bottleneck. See [P97 execution](p97_wiki_intake_shards.md).

The P99 code shard contributes 21 distinct cross-PR complete-set tasks from
four repositories. All have final reader-visible added-code witnesses,
filename-preserving added-code removal sensitivity, a two-witness token span
above 16K, and 21/21 exact masks. This is a scoped content certificate, not
an unrestricted long-dependency proof. The older P65/P94 filename-proof
profile does not certify this different operation; the P100 selector uses a
separate SHA-pinned P99 native audit, unified manifest and mask receipt. See
[code execution](p99_code_content.md).

The P100 Wiki categorical shard contributes 43 complete-set scans across
four real worlds and four domains, all train. It replays every candidate row
from a complete visible table and checks answer-changing and control cell
edits; 43/43 final masks passed. Its evidence-token extent is only 245–4,680,
so it fills an L2 aggregation/completeness cell without proving far-distance
use. Earlier numeric-column versions were rejected after a header-alignment
counterexample. See [categorical execution](p100_wiki_categorical_scan.md).

`data/candidates/p100_balanced_selection_v2` is a source-aware candidate
subsample from this same index: **841 distinct tasks / 221 typed source groups**,
with 28 domain labels, 97 topic labels, 106 operation strings and 25 groups
represented by multiple operations. It retains 413 real Wiki, 192 real
finance, 110 real code workflow, 103 controlled simulation, eight grounded
simulation, eight real-paper-source and seven paper-revision views. Its final
length bins are 226 `<32K`, 223 `32–64K`, 266 `64–128K`, and 126
`128–256K`. Among new shards it selects 87 P97 Wiki, all 21 certified P99
code, and 39/43 P100 categorical tasks. Source/group/answer budgets mean a
selection count is not net corpus growth. The selection and its deterministic
verify-only replay passed, including the old P65/P94 gate and the new P99
content-proof gate.

The 841 selected readers have also been materialized byte-for-byte at
`data/candidates/p100_balanced_materialized_v1`: 593 train and 248 eval,
63,584,415 complete chat tokens and 81,634 assistant-supervised tokens. The
materializer recomputed all 841 assistant-only masks, checked selected shard
pointers and reader/answer hashes, and kept its audit separate from the
model-visible messages. A second full deterministic materialization replay
completed with exit code 0 and matched the frozen manifest and all five
output-file SHA-256 values.
Although the selection grew from P96's 756 to 841 tasks, its supervised token
total fell from 88,380 to 81,634 under the same assistant-only masking
contract. The changed source/length mix replaces some longer answers with
short complete-set and lookup answers. Candidate count and input-token volume
therefore cannot stand in for the amount of answer supervision; a future
training recipe must track sample, input-token and supervised-token weights
separately.

The selected set is still a **candidate**. It is not a training approval or
evidence of model improvement. No GPU training was run. Source groups,
programs, readers, token offsets, masks and dependency interventions have
different proof scopes; the positive content gates apply only to their named
operations. The index remains dominated by controlled simulation and short
Wiki lookup views. Real books, narrative reports, and agent action/feedback
trajectories are not represented at comparable scale. The P98 annual-report
Note-reference probe admitted zero tasks because its visible target mapping
was unreliable; its [rejection ledger](p98_report_note_feasibility.md) is
retained rather than converted into spurious data.

Inspect the final source and selection receipts:

```bash
cat data/candidates/p100_candidate_refs_v2/manifest.json
cat data/candidates/p100_balanced_selection_v2/manifest.json
cat data/candidates/p100_balanced_materialized_v1/manifest.json
cat data/candidates/p97_wiki_unified_full_mask_v1/manifest.json
cat data/candidates/p99_code_unified_mask_v2/manifest.json
cat data/candidates/p100_wiki_categorical_full_mask_v1/manifest.json
less -R data/candidates/p100_balanced_selection_v2/selected_refs.jsonl
less -R data/candidates/p100_balanced_materialized_v1/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p100_candidate_refs_v2 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p100_balanced_selection_v2.json \
  --output data/candidates/p100_balanced_selection_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py \
  --index data/candidates/p100_candidate_refs_v2 \
  --selection data/candidates/p100_balanced_selection_v2 \
  --selection-config configs/p100_balanced_selection_v2.json \
  --output data/candidates/p100_balanced_materialized_v1 --verify-only
```
