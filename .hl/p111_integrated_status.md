# P111 bounded mixed-source candidate campaign

P111 extends the P110 candidate index with **31 independent semantic tasks**:
24 new CodeForge filename/content tasks, two newly curated Deno added-code
pair tasks, and five real Wiki list-to-linked-article tasks. The CodeForge
tasks reuse Bitcoin and Deno repository worlds; the Wiki tasks come from
three linked-list worlds, two of which add typed groups to the global bank.
The new shards passed their own independent final-reader/mask checks and the
combined sharded index passed full-reader SHA replay. The P110 paper
category campaign is excluded: seven frozen two-revision papers produced
zero tasks past the semantic quality gate.

The full P111 candidate bank holds **12,652 reader views / 12,127 independent
semantic tasks** from **1,428 typed source/world groups**, 9,737 train and
2,915 eval. Source-kind view counts are 6,116 controlled simulation, 28
grounded simulation, 3,189 real Wiki, 2,314 finance, 981 code, 17 paper
source and seven paper revision. It has 29 domain labels, 146 topic labels,
126 operation strings and 377 multi-operation groups. Physical length bins
are 3,171 below 32K, 3,551 at 32–64K, 3,966 at 64–128K and 1,964 at
128–256K. No extra length view is counted as a new task.

The fixed balanced selection contains **1,151 distinct tasks** (822 train,
329 eval) from 292 typed groups. It includes all 31 new tasks. Source kinds
are 680 real Wiki, 192 finance, 140 code, 103 controlled simulation, 12
grounded simulation, 17 paper source and seven paper revision. The selected
length bins are 417 below 32K, 307 at 32–64K, 292 at 64–128K and 135 at
128–256K. Its domain/topic/operation label counts are 29/142/109; only 33
of 292 selected groups exercise more than one operation. Table-cell lookup
remains the largest operation (318 tasks). The materialized reader pack has
74,061,756 full-chat tokens and 84,125 assistant-supervised tokens.
Initial materialization and a complete independent `--verify-only` replay
passed 1,151/1,151 final readers and masks.
The 109 operation strings collapse to 52 prefixes before colon-encoded
finance metric parameters; neither count is a certified mechanism count.
Controlled simulation supplies 39,998 of the 84,125 supervised tokens from
only 103 tasks; real Wiki supplies 15,858 from 680 tasks. Thus task-count
balance and supervised-token balance differ sharply. Any future model arm
needs an explicit loss/replay budget before this pack can support a learning
claim.
Relative to the P110 selected pack, the selector adds all 31 new shard tasks,
also selects one previously unselected P107 code task, and drops one older
selected task because a source-group cap changes the ranking. This is a
net gain of 31 tasks, not an assertion that every older selection row persists.

Proof scopes remain distinct. The 24 filename tasks pass a finite alias and
content-removal certificate, but 22 come from Bitcoin and many reuse PR
records. The two new added-code tasks pass two-witness and prior-pair curator
checks with 45,132 and 76,101-token witness separation. The five Wiki tasks
pass list selector → linked target field, alternate answer and text-deletion
checks, but lie below 32K in this selected arm. A separate 39,250-token
same-world Wiki view exists for one already-counted task and is not in the
index. These cases do not establish model improvement, broad natural prose
coverage or unrestricted shortest-proof distance. `train_ready=false`.

**Post-integration quality correction:** four of the five Wiki tasks have a
list row label equal to or contained in the target article title. A reader
may bind the target by title even after the link is deleted, while the formal
solver requires a link token and reports failure. They therefore do **not**
have established reader-text link necessity. The subsequent pinned
`p112_wiki_link_strict_unified_v1` gate rejects those four and retains one
28,289-token task (independent mask 1/1). The P111 pack remains a historical
candidate diagnostic containing four known shortcut risks; it must not be
promoted unchanged. The stricter shard will replace the P111 Wiki shard in
the next integrated index.

The P111 source and selector code prevents a new P108 proof receipt from
counting old CodeForge tasks again: a pinned unified shard limits which
positive records the newer receipt contributes, with exact sample identity.
The new added-code receipt separately requires curated PR-pair membership
against P99/P107 history. The P110 selection still replays unchanged.

Remaining distribution gaps are substantial: books and general narrative
reports are absent at scale, agentic action/feedback is not an admitted L5
lane, and the selected SFT supervision is about 0.114% of full-chat tokens.
No GPU/model training has been launched for these candidates. The next
campaigns route existing report, book and controlled-world sources through
legal source-shape × operation support checks; output counts must distinguish
source acquisition, final task admission, and length views.
Question naturalness remains a separate review item: for example the Wiki
`built` field currently renders as “What built is given ...?”, which is
grammatically rough despite the answer and evidence checks passing. This
candidate pack is not a final human-edited instruction set.

Inspect and replay:

```bash
cat data/candidates/p111_candidate_refs_v1/manifest.json
cat data/candidates/p111_balanced_selection_v1/manifest.json
cat data/candidates/p111_balanced_materialized_v1/manifest.json
less -R data/candidates/p111_balanced_materialized_v1/sample_index.jsonl
cat data/candidates/p111_code_curated_v1/manifest.json
cat data/candidates/p111_wiki_linked_unified_v2/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/build_p86_sharded_bank.py verify --index data/candidates/p111_candidate_refs_v1 --full-readers
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py --config configs/p111_balanced_selection_v1.json --output data/candidates/p111_balanced_selection_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/materialize_p95_balanced_selection.py --index data/candidates/p111_candidate_refs_v1 --selection data/candidates/p111_balanced_selection_v1 --selection-config configs/p111_balanced_selection_v1.json --output data/candidates/p111_balanced_materialized_v1 --verify-only
```
