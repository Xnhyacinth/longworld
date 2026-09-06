# P14 ResearchLab Adam substantive-revision closeout

## Final result

The authentic Adam revision program reaches **6/9**, so it is not eligible for
promotion and does not change the release inventory or `train_ready=false`.
No dense ranking or strict candidate audit was run after the incomplete matrix.

| Band | Exact prompt tokens | full | CF | ordered | Retained |
| --- | ---: | ---: | ---: | ---: | ---: |
| 16K | 14,531 | reject | reject | reject | 0/3 |
| 32K | 32,446 | pass | pass | pass | 3/3 |
| 64K | 64,880 | pass | pass | pass | 3/3 |

The sole reject is `exact_16k_out_of_range:14531`; the immutable exact 16K
band is 16,000--16,384. Near-duplicate, exact-band, derived-view, truncation,
and source-lineage gates were not relaxed. The candidate is at
`data/releases/p14-researchlab-adam-substantive-revision-v1-candidate/`.

## Authentic source and executable dependency

The source is the bounded official arXiv export in
`data/source_inventory/p13_paper_adam_revision_history_v1/`: nine consecutive
public revisions, eight signed adjacent `revision_of` relations, and exact
LaTeX bodies bound by SHA-256. The program reads the v2 semantic addition,
verifies absence from v1, compares the two largest genuine non-delta section
changes (`Algorithm` and `Convergence analysis`), follows the required adjacent
revision chain, and reads the terminal revision date. The answer names the two
changed sections; replay reparses their per-file spans and checks the v1/v2
section hashes. Removing an essential artifact or corrupting a required section
therefore makes the answer unknown. The CF changes the bound v2 semantic span
and changes the answer from minibatch 128 to 129.

This is a real revision-review task, not concatenation: every selected section
is a complete source section in archive order, every selected file has an exact
span and hash, and missing required sections fail closed.

## Capacity and hard upper bound

Pinned tokenizer: `Qwen/Qwen3.5-4B` revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`.

| Revision | Exact semantic-body tokens |
| --- | ---: |
| v1 | 10,065 |
| v2 | 15,864 |
| v3 | 16,051 |
| v4 | 16,075 |
| v5 | 16,075 |
| v6 | 17,173 |
| v7 | 16,942 |
| v8 | 16,942 |
| v9 | 141 |
| **Revision-weighted total** | **125,328** |

Byte-identical file deduplication leaves 43 payloads and 70,674 tokens.
Adjacent insert/replace novelty is 18,864 tokens. Cross-file section views fixed
the old one-file recognizer blocker and naturally materialized all three bands.
For 16K, adding the answer-required complete `Convergence analysis` sections to
v1/v2 raised the exact prompt from 11,909 to 14,531, but the next complete
answer-relevant section cannot place the prompt inside 16,000--16,384 without
changing the task shape. Per instruction this route is closed rather than
padding, copying, adding unchanged context, or relaxing admission.

## Implementation and tests

The reusable ResearchLab adapter now:

- recognizes required sections across separate LaTeX files;
- normalizes trailing heading layout commands such as `\\vspace*`;
- emits one exact span/hash per selected source file in archive order;
- validates two bounded, genuine section changes with a linear length-delta
  selector and signed before/after section hashes; and
- fails closed when a required file, section, hash, or revision endpoint is
  missing.

The fail-first test initially observed only `[['64k']]` instead of the required
`[['16k'], ['32k'], ['64k']]`. After the minimal cross-file implementation:

```text
uv run python -m pytest tests/test_researchlab_source_workflow.py -q
16 passed in 2.63s

uv run ruff check longworld/domains/researchlab/simulate.py \
  longworld/domains/researchlab/events.py \
  longworld/domains/researchlab/queries.py \
  tests/test_researchlab_source_workflow.py
All checks passed!
```

An initial change-score implementation used `SequenceMatcher` on full LaTeX
sections and was stopped after a read-only CPU check showed an unbounded hot
path. The final selector is linear; its dedicated fail-closed test passes in
1.38 seconds.

## Reproduction

- Config: `configs/p14_researchlab_adam_substantive_revision_v1.yaml`
- Capacity diagnostic: `configs/p14_researchlab_adam_64k_capacity_v1.yaml`
- Candidate: `data/releases/p14-researchlab-adam-substantive-revision-v1-candidate/`

```bash
uv run python scripts/run_with_local_probe_trust.py \
  --trust-file /workspace/wynckeliao/.longworld-agent-probe/p12-probe-12-v2/local_probe_trust.json \
  --role source --role candidate --role report --allow-combined-roles -- \
  /workspace/wynckeliao/longworld-worlds/.venv/bin/python \
  /workspace/wynckeliao/longworld-worlds/scripts/generate.py \
  --config /workspace/wynckeliao/longworld-worlds/configs/p14_researchlab_adam_substantive_revision_v1.yaml \
  --seed-start 141269800 \
  --out-dir /workspace/wynckeliao/longworld-worlds/data/releases/p14-researchlab-adam-substantive-revision-v1-candidate \
  --workers 1
```

The next ResearchLab attempt must use a different paper/entity with natural
capacity above 64K and at least three to four substantive public revisions.
Run the existing official arXiv exporter and bounded source preflight first;
do not write new core until revision metadata and 16K/32K/64K section capacity
are demonstrated.
