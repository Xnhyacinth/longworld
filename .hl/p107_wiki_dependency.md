# P107 real-Wiki remote-selector pilot

P107 is a bounded, source-supported test of a two-document data-flow operation: read a named row and field in one original Wiki document, then use that value to choose the category for a complete table scan in another original Wiki document. It adds **2 independent eval programs from 2 worlds**, **0 train programs**. Both source pages retain their P95 eval split, exact titles, URLs, revisions and source snapshot pins. P107 introduces no new pages and inherits the P106/P95 title-and-URL split gate. The final reader includes both separate source documents; the natural comparison question names the selector row/field and target table but omits the resolved category. The earlier v3 question explicitly said “read ... Then ...”; v3 remains historical and is not a separate task.

## Support and scope

The pinned support scan over P105/P106 accepted readers found 71 reader views, 70 unique target programs, 13 exact remote matching rows, and only 2 productive target programs in 2 worlds. The other 68 unique programs had no exact typed remote selector. Twelve matching remote rows in one Irish page yield only one program; they are not counted as 12 independent tasks. Both admitted programs are P106 eval; P105 train yielded zero. The support ledger is `data/candidates/p107_wiki_dependency_support_v2.json` (SHA-256 `e59524ee648a667cc8050d877572c5e7b7a8f2216af67cf64cc94519e8e24357`). The earlier v1–v3 native/unified attempts are historical, not admission inputs.

| Source group/world | Remote selector | Target table | Answer and counterfactual | Final chat / supervised tokens | Remote-to-target evidence gap | Last evidence-to-question gap |
| --- | --- | --- | --- | ---: | ---: | ---: |
| `p93_castles_01_01` / `snapshot_1b91312510e678901c47` | `Doune Castle`, `Date`, in *List of castles in Stirling (council area)* | *List of castles in Wales*, `Vale of Glamorgan`, `Date` | `14th century` → 2 names; replacing the remote value with `12th century` → 3 different names | 13,992 / 19 | 1,123 | 4,926 |
| `p93_castles_01_02` / `snapshot_fd0876f63bb2945725f3` | `Bagenal's Castle`, `Type`, in *List of castles in Ireland* | *List of castles in West Lothian*, `List of castles`, `Type` | `Tower house` → 8 names; replacing the remote value with `Manor house` → 2 different names | 86,621 / 44 | 66,092 | 19,330 |

Both examples use the actual final chat template and tokenizer. Evidence-token extents are 1,127 and 66,094. Only the second is a long-range cross-document dependency; the first is a two-document operation with nearby evidence. Both are in the `history/castles` domain/topic, so this pilot is **not evidence of broad-domain L3 scale**. Neither is marked train-ready.

The compiler and independent auditor both require the target table to occur before the remote document in the final reader. This is the layout of both admitted examples. A remote-first layout is rejected until its edit offsets and final spans are implemented and tested. The support check requires a unique exact selector row, the named typed field, no selector name in the target document, no same-line alternate selector support, a non-answer-changing remote control edit, an answer-changing remote field edit, a target hit edit, a target control edit, and an unresolved answer after deleting the remote field. This is bounded text-level evidence. Equivalent evidence expressed elsewhere in prose is not exhaustively ruled out.

## Final receipts and replay

- Native: `data/candidates/p107_wiki_dependency_native_v4/manifest.json`, SHA-256 `661dff4c203d81b548278fc66ef87571715a6b9c9ee608ea0f27db2e6c574440`; blind source/answer/mask audit: `data/candidates/p107_wiki_dependency_native_v4/mask_audit.json`.
- Unified shard: `data/candidates/p107_wiki_dependency_unified_v4/manifest.json`, SHA-256 `2472af53901af4e04fcfc4a2823263c68cc19cdcb1ee8ccafe5b59b4e42edc51`.
- Shared all-reader mask: `data/candidates/p107_wiki_dependency_unified_all_mask_v4/manifest.json`, SHA-256 `e11066fec6c7cffff131f3074a84304a8c09cfd061cf8c222ae31d6b85f7daad`; 2/2 eval readers, 100,613 final-chat tokens, 63 supervised tokens.

```bash
.venv/bin/python scripts/p107_wiki_dependency_batch.py --config configs/p107_wiki_dependency_batch_v1.json --output-dir data/candidates/p107_wiki_dependency_native_v4 --verify-only
.venv/bin/python scripts/p107_wiki_dependency_audit.py --config configs/p107_wiki_dependency_batch_v1.json --native-dir data/candidates/p107_wiki_dependency_native_v4 --verify-only
.venv/bin/python scripts/p107_wiki_dependency_to_unified.py --config configs/p107_wiki_dependency_batch_v1.json --native-dir data/candidates/p107_wiki_dependency_native_v4 --output data/candidates/p107_wiki_dependency_unified_v4 --verify-only
.venv/bin/python scripts/audit_unified_reader_mask.py data/candidates/p107_wiki_dependency_unified_v4 --all --output data/candidates/p107_wiki_dependency_unified_all_mask_v4 --verify-only
.venv/bin/python -m pytest -q tests/test_p107_wiki_dependency.py
```
