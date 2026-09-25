# P100 real-Wiki categorical table scan

The P97 gated pool is frozen at `data/capability_records/p97_wiki_gated_delta_v1/source_pool.json` (SHA-256 `094353d647ae888821ede5683fd912d125f336018317ffc9364b40b7cd840268`). The P100 compiler scans every source group with four worker processes; it does not add topics by hand or invent table rows. Its output remains a research candidate, not a training admission.

The operation is a complete-set L2 question: within a named visible table, return every row whose selected categorical column has an exact value and the total count. The parser requires a heading, unique visible heading/header key, at least eight contiguous full-width rows, unique plain row names, and a complete plain-text column with at least two values. It rejects numeric-only columns because malformed Wiki rows can shift numeric cells under the wrong header. A candidate needs at least two hits and two misses, an answer-changing edit of a visible miss cell, a distinct answer-preserving control edit, final reader-byte reparse, and no same-line duplicate support outside the selected table. The final tokenizer checks all candidate cells and the assistant-only loss mask.

Authoritative P100 artifacts are `data/candidates/p100_wiki_categorical_scan_v3/` and `data/candidates/p100_wiki_categorical_unified_v2/`. V1/V2 native outputs and unified V1 predate the numeric-column quality restriction and are historical, not eligible for the current index. The v3 native batch screened 114 groups from 18 domains and 339 frozen pages. Fifteen structurally eligible tables appeared; 14 offered at least one categorical option. It emitted 43 independent tasks/views in four actual source worlds: education/medical colleges 28, energy/hydroelectric plants 6, environment/glaciers 6, and transport/tunnels 3. The other 14 domains yielded none under this strict recipe. All 43 are train; this pool yielded no eval task. Physical final-chat lengths are 15 below 32K and 28 in 32–64K. Candidate tables have 9–64 rows; token spans covering candidate cells range 245–4,680. These are dense scans inside a table, not established far-apart or cross-document minimum dependencies. They do not expand world count in proportion to task count.

The page support ledger is `page_audit.jsonl`; task rejects are in `rejected.jsonl`; accepted rows, answer, final-reader evidence positions, and hit/control intervention hashes are in `sample_index.jsonl` and `audit.jsonl`. Independent native exact-mask and answer checks are in `mask_audit.json`. A separate all-reader normalized mask receipt is `data/candidates/p100_wiki_categorical_full_mask_v1/manifest.json` (43/43 readers, 1,654,971 full-chat tokens, 1,683 supervised tokens). The final normalized reader has only `sample_id` and two messages; audit metadata remains outside reader input. Known limit: same-line duplicate checks and bounded interventions do not rule out prose paraphrases or all alternate derivations. No model experiment has been run on P100.

Reproduce with the project environment:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p100_wiki_categorical_scan.py --config configs/p100_wiki_categorical_scan_v1.json --output-dir data/candidates/p100_wiki_categorical_scan_v3 --workers 4 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p100_wiki_categorical_audit.py --native-dir data/candidates/p100_wiki_categorical_scan_v3
UV_LINK_MODE=copy uv run --offline python scripts/p100_wiki_to_unified.py --config configs/p100_wiki_categorical_scan_v1.json --native-dir data/candidates/p100_wiki_categorical_scan_v3 --output data/candidates/p100_wiki_categorical_unified_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py data/candidates/p100_wiki_categorical_unified_v2 --all --output data/candidates/p100_wiki_categorical_full_mask_v1 --verify-only
cat data/candidates/p100_wiki_categorical_scan_v3/manifest.json
less -R data/candidates/p100_wiki_categorical_scan_v3/rejected.jsonl
less -R data/candidates/p100_wiki_categorical_scan_v3/audit.jsonl
```
