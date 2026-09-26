# P125 same-context multi-operation compiler

The frozen candidate shard is `data/candidates/p125_joint_task_candidates_v3`, compiled from the audited 1,566-reader P120 materialization. This is an additive research candidate lane: **23 new joint-answer tasks reuse 46 distinct component tasks and 23 existing source worlds**. The original single-question readers remain separate. No source document, fact, ontology or serial dependency was synthesized by pairing.

The compiler keys each possible pair by `source_kind × source_group × split × context_sha256`. It extracts and re-hashes the exact model-visible context, including question-first code readers, then requires two different semantic task IDs, different operation families, different answer hashes and non-null component dependency statuses. It caps output at one pair per world, preserves the frozen train/eval split, checks each component answer against its source index, composes a two-field JSON gold, and verifies the final chat-template tokens and assistant-only loss mask. It streams the pinned source readers and retains only chosen component rows. No per-domain question function or topic word list is needed. The component question texts appear as tasks A and B with surrounding whitespace trimmed; solver programs and proof graphs remain in the audit lineage, outside the reader prompt.

| Stage | Actual result |
| --- | ---: |
| Pinned selected source readers / source groups | 1,566 / 409 |
| Source groups with only one operation | 336 |
| Different operations without byte-identical context | 26 |
| Multi-operation groups without two dependency-status-bearing tasks | 12 |
| Same operation family after parameter normalization | 11 |
| Pairs sent to final reader gate | 24 |
| Topic/context/split mismatch at final gate | 1 |
| New candidate views / independent joint tasks | 23 / 23 |
| Reused component task IDs | 46 |
| Train / eval | 16 / 7 |
| Full-chat tokens / supervised tokens | 1,942,570 / 1,527 |

Admitted kinds: real Wiki eight, finance seven, books five, code workflows two, paper source one. Controlled and grounded simulation admitted zero under the current component dependency-status gate. The one final-gate rejection pairs identical Amazon source bytes with topic labels `amazon` and `Amazon.com, Inc.`; the source and split match, but this wave conservatively requires identical topic labels. A future generic source-identity layer can resolve that metadata mismatch without treating either spelling as a new domain. Physical final-chat bins: six `<32K`, five `32–64K`, eight `64–128K`, four `128–256K` (2,045–259,351 actual tokens). The exact per-kind support matrix, operation-family pair counts, all rejected groups and every component identity are in the manifest and JSONL ledgers.

This is **parallel multi-operation supervision**, not a dependent chain: task B does not consume task A's answer. The existing component certificates remain bounded to their own source grammars. The combined sample has no new text-deletion or shortest-proof certificate; physical length does not establish remote evidence necessity. Source quality and model benefit have not been measured afresh, so `train_ready=false`.

`v1` was the first output; `v2` changed reader loading to retain only selected components; `v3` added source-kind support and operation-pair accounting. Only `v3` is the reviewed pinned candidate. Its manifest SHA-256 is `dc9159249e5dcef3566b13c5c761846d022214a2a779c3ebe28153de10f4c556`, its compiler SHA-256 is `1109916f469461d559ead7605136cef63297ba8ddf8c0be98801cebdcd5dab60`, and the source materialization manifest SHA-256 is `66e0c5ca1536fae58a2a114b68e3eb11c06e1b21cc504644efa962d2d67a53ef`.

Inspect and replay:

```bash
cat data/candidates/p125_joint_task_candidates_v3/manifest.json
less -R data/candidates/p125_joint_task_candidates_v3/decision_ledger.jsonl
less -R data/candidates/p125_joint_task_candidates_v3/pair_lineage.jsonl
less -R data/candidates/p125_joint_task_candidates_v3/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/p125_joint_task_compiler.py --config configs/p125_joint_task_compiler_v1.json --output data/candidates/p125_joint_task_candidates_v3 --verify-only
UV_LINK_MODE=copy uv run --offline python -m pytest -q tests/test_p125_joint_task_compiler.py
UV_LINK_MODE=copy uv run --offline ruff check scripts/p125_joint_task_compiler.py tests/test_p125_joint_task_compiler.py
```
