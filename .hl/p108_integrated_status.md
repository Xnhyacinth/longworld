# P108 verified candidate index and selected reader arm

Historical snapshot: P109 adds 530 automatically sourced Wiki tasks and a
larger source-aware reader selection; see `.hl/p109_integrated_status.md`.

`data/candidates/p108_candidate_refs_v1` extends the P107 bank with 70
independent Oxc added-code complete-set tasks and two real-Wiki remote-selector
complete-set tasks. The former reuse one train CodeForge source world; the
latter reuse two eval Wiki source worlds and ask natural comparison questions.
The two Wiki tasks are the only newly compiled cross-document data-flow
programs in this extension, and only one has a long evidence gap (66,092
tokens between target and remote evidence). Each shard has its own source,
intervention and full assistant-mask receipt. Global `verify --full-readers`
passed.

The bank contains **12,078 candidate views / 11,553 globally independent
semantic tasks** from 1,389 typed source/world groups: 9,291 train and
2,787 eval views. Kinds are 6,116 controlled simulation, 24 grounded
simulation, 2,649 real Wiki, 2,314 real finance, 951 real code workflow,
17 real paper source and seven paper revision. It has 28 domain labels,
111 topic labels and 124 operation strings. A source group is a typed
provenance unit, not proof of an independently productive semantic world.
Of the 1,389 groups, 372 have more than one operation string; 332 are
controlled simulation and only 20 are real Wiki. The 72 new tasks add no
source group or domain label to this index. Physical length bins are 2,674
`<32K`, 3,498 `32–64K`, 3,948 `64–128K`, and 1,958 `128–256K` views.

`configs/p108_balanced_selection_v1.json` retains the P107 sample, source and
split caps, but pins independent P99 and P107 code-content proof packages.
The new 70 code tasks cannot inherit P99's 21-task proof: the selector verifies
their raw native/audit/unified/mask chain **and** their independently curated
membership and mask. All 91 code-content candidates pass their respective
input proof gates. The final selected arm contains **967 independent tasks**
(684 train, 283 eval) from 247 groups. It selects 16 of the new Oxc tasks
and both new Wiki tasks, replacing 18 older tasks under the fixed caps.
The Oxc source group remains capped at 24; its chosen mix moves from 12
added-line/12 merged-union tasks to 20/4. Selected multi-operation groups
rise from 25 to 27, while source-group breadth falls from 248 to 247.

The 967 selected physical bins are 319 `<32K`, 249 `32–64K`, 272
`64–128K`, and 127 `128–256K`; exact final-chat tokens are **67,080,848**
and assistant-supervised tokens **79,754**. That is 4,252 fewer supervised
tokens than P107 at the same selected task count. This is a candidate
coverage arm, not an established better training mixture. Its complete
materialized reader and assistant-only mask pack is
`data/candidates/p108_balanced_materialized_v1`; full `--verify-only` replay
matched all 967 reader bytes and frozen outputs. The P105/P106/P107 default
selections and materialized packs also replayed byte-for-byte after the
opt-in multi-proof change.

The batch is local research data (`train_ready=false`). No GPU training or
model gain is claimed. The Oxc content certificate is scoped to two selected
added-code witnesses; its question is at the start of the user message and
their 17K–76K span is not a late-query evidence distance. The two new Wiki
tasks are both `history/castles` eval, not broad L3 coverage. The P105
multi-category paper-source shard retains
`local_research_only_no_redistribution` because its Atom metadata provided no
license URI. A separate P108 automatic Wiki acquisition
has frozen additional source pages but is not included in this index.

Inspect and replay:

```bash
cat data/candidates/p108_candidate_refs_v1/manifest.json
cat data/candidates/p108_balanced_selection_v1/manifest.json
cat data/candidates/p108_balanced_materialized_v1/manifest.json
less -R data/candidates/p108_balanced_materialized_v1/sample_index.jsonl
less -R data/candidates/p107_code_curated_v1/quality_ledger.jsonl
cat data/candidates/p107_wiki_dependency_unified_all_mask_v4/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify \
  --index data/candidates/p108_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p108_balanced_selection_v1.json \
  --output data/candidates/p108_balanced_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py \
  --index data/candidates/p108_candidate_refs_v1 \
  --selection data/candidates/p108_balanced_selection_v1 \
  --selection-config configs/p108_balanced_selection_v1.json \
  --output data/candidates/p108_balanced_materialized_v1 --verify-only
```
