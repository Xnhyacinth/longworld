# P63 reusable world-to-task compilation

The frozen six-source batch produced **1,252 distinct semantic task instances**
from **24 existing annual filings / 216 normalized source facts**. All six jobs
passed source verification, typed execution, controlled-question scope checks,
visible-evidence binding, exact context counting and persisted-export replay.
This completes the first measured same-world/many-task compiler cycle. It does
not establish public release readiness, strict long dependency or training gains.

| Source world | Split in this isolated bank | Accepted tasks | Task families |
| --- | --- | ---: | ---: |
| Amazon | train | 242 | 8 |
| Meta | train | 213 | 8 |
| Micron | train | 234 | 8 |
| Alphabet | train | 197 | 8 |
| NVIDIA | train | 181 | 8 |
| AMD | eval | 185 | 8 |
| Total | 1,067 train / 185 eval | 1,252 | 8 shared families |

Median retained yield is **205 tasks per source world**. These are source-bound
query instances, not statistically independent observations: tasks share source
facts and context. All rows have one full primary view; no CF, reordered or extra
length views contribute to the count. **Net new source entities: zero.**

The compiler separates source_collection_id, world_instance_id, semantic_task_id,
variant_family_id and sample_id. Source parsing still supports the existing
registered IR and inline-XBRL providers; it is not an arbitrary-issuer downloader.
Within that contract, no issuer-specific query function was added. AMD used the
same frozen compiler on the first post-freeze cross-provider run. AMD was already
in legacy training, so the new eval must not be merged with that legacy training
and described as held-out-source evaluation.

## Actual funnel and length

2,466 draft candidates → 8 unavailable metric combinations → 2,458 instantiated
programs → 1,206 type/nondegeneracy rejections → **1,252 retained tasks**. The
eight families are lookup (216), delta (324), aggregate (304), maximum (162),
filter (128), filtered aggregate (46), ratio (48) and cash reconciliation (24).
These eight families are not eight proven independent long-reasoning topologies.
The independent AST audit found 20 fact-abstracted shapes and at most two
non-lookup/non-collect operators per task; 46 filter-then-sum tasks are explicit
two-operation compositions. AMD introduces no AST shape absent from train.
This is the first reusable, relatively shallow query library, not evidence that
unseen rule combinations or deep temporal reasoning have been solved.

All instantiated valid tasks passed the subsequent surface/natural-cap checks.
The actual contexts range from **59,152 to 129,233 exact tokenizer tokens**;
52 rows fit the 65,536 cap and 1,200 fit the 131,072 cap. This batch does not add
16K/32K examples and does not claim the older narrow exact-band certificates.

The train rows expose **91,733,476 context tokens** and eval rows expose
**16,713,551**. Those are repeated task exposures, not new source-token mass.
There are **78 unique serialized contexts**, whose 6,730,871-token sum also
contains overlapping source material. The source-world count remains six.

The fixed context policy retained 424 rows with complete filings, 800 rows with
the newest complete filing plus earlier complete financial statements, and 28
complete audited-statement packets. Layout whitespace and hidden inline data
are not counted as source prose. All operand spans and visible signs are checked
again after final context selection; real summaries are not removed as evidence
of difficulty. This HTML normalization is not a universal browser/CSS renderer.

## Implementation and verification

Actual call chain:

`run_finance_taskbank_batch.py` → isolated per-source trust wrapper →
`load_finance_world` → `compile_finance_taskbank` → typed operator execution and
controlled-question scope recovery → `taskbank_context` source selection →
`materialize_finance_taskbank.py` → saved task/SFT/context files → a fresh process
running `--validate` with source replay and source-context reassembly.

The candidate compiler supports genuine predecessor consumption and rejects
same-fact double counting, nonadditive stock sums, empty/all-selected filters,
redundant singleton sums, invalid ratios, changed source worlds, and mismatched
questions/answers/evidence. It does not implement general causal, historical
as-of, restatement or exception reasoning.

**117 focused new/existing-finance tests passed** before the batch, and Ruff
passed for all new runtime modules and tests. Reproduced regression cases include
HTML entity offsets, word spacing, hidden/cross-cell signs, lost parentheses,
layout-entity inflation, wrong occurrences of duplicate numeric values, forged
readiness/counts, missing compiler bindings, symlink receipts, and source changes
during batch execution. Current code hashes match the pre-transfer freeze.
AI-assisted reading and independent arithmetic on 48 deterministic examples
(one per issuer/family, 29 complete tables) found no sampled numeric hold. A
fresh signed-fixture revenue change altered nine dependent answers while 255
controls remained unchanged; an unsigned change failed verification. These
sampled and fixture results do not certify all possible source/semantic errors.

Independent checks and exact scope are recorded separately:

- `p63_taskbank_inventory_20260909.json`: current-byte task/token/funnel counts.
- `p63_taskbank_review_20260909.md`: independent adversarial and batch audit.
- `p63_finance_numeric_review_20260909.*`: sampled source/answer arithmetic review.
- `p63_related_work_compiler_checks_20260909.md`: six original-paper checks;
  execution validation and source reuse were adopted, not unverified novelty or
  training-performance claims.

## Training and release status

The frozen per-world artifacts are **local candidates**. Their explicit
`training_release_eligible=false` and `production_eligible=false` remain intact.
The separate local training preparation is now **complete**: a role-signed
manifest binds 1,067 train and 185 eval rows, all six source revalidations, the
new transform/recipe code, and the exact output bytes. The longest full chat
sequence is **129,654 tokens**, measured without truncation. The readonly
snapshot files are mode 400 and its directory is mode 500. Root independently
revalidated the signature/inputs and snapshot bytes; the launcher
`TASKBANK --prepare-only` passed and reused the snapshot without requiring a
framework installation or starting a model/GPU.

This stage declares `local_training_eligible=true`, `production_eligible=false`
and `framework_preprocessing_verified=false`. The current checkout has no
default `.vendor/LLaMA-Factory` installation; actual framework preprocessing and
model training have not run. No matched training gain has been measured.
The source compiler remains frozen; this does not retroactively promote its
candidate receipts or the legacy strict profiles.

The final training/export regression group passed **149 tests**, in addition to
the earlier 117 finance checks. A real concurrent tokenizer-initialization
failure was reproduced and fixed with serialized construction and parallel
counting. The unsigned failed attempt is excluded; its partial JSONL files were
removed after diagnostic hashes were recorded. See
`p63_local_training_preparation_20260909.md` for the exact replay command and
`/workspace/wynckeliao/.longworld-taskbank-training-private/PREPARATION_CLOSEOUT.json`
for the durable preparation receipt.

Public/production release is **NO-GO**. The selected sources lack a demonstrated
open redistribution grant for this corpus; the current official AMD terms are
material evidence rather than a mere missing metadata field. The repository's
production profile/package registries and independently approved source/release
bindings also remain prerequisites. See `p63_public_source_rights_check_20260909.md`
and `p63_release_readiness_20260909.md` for the exact evidence and remaining scope.

Legacy products remain 246 train / 18 eval, with 57 train / 9 eval under existing
reading-family holds. Their unheld complement is not added to this verified
taskbank. CURRENT_RELEASE and Hugging Face were not changed.

Reproduction commands and configuration are in `docs/P63_TASKBANK.md`.
The actual batch receipt is
`data/candidates/p63_finance_taskbank_v1/BATCH_RECEIPT.json`.
