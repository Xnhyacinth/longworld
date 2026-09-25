# P102 support-first real Wiki JOIN pilot

P102 reused `discover_connected_wiki_pairs.py` and `probe_wiki_row_binding.py`: no new JOIN generator or invented filler was introduced. The source router selected 14 typed-row anchor groups across 14 domain labels from the pinned P97 pool. It ran 42 Wikimedia search queries and previewed 52 pages. Only one pair passed strict selector→target compilation: Swedish royal residences joined to castles/palaces in Sweden, producing three distinct target-column answers for Stockholm Palace. A second bounded pass reused the **same 42 frozen search responses** and previewed 30 further topic-compatible pages with four workers. No extra search queries were made. Four source snapshots were actually frozen across both passes: heritage one, hydroelectric one (no strict JOIN), and sports two. Thus three snapshots were structurally productive before global admission.

The second-pass v1 script had a local path-serialization error after freezing the two sports sources: it applied `relative_to(ROOT)` to a relative output path and caught the resulting `ValueError` as if it were a source rejection. The historical `data/capability_records/p102_connected_recovery_v1/` receipt reports zero accepted groups and must **not** be used as a yield estimate. `scripts/p102_connected_recovery.py` now preserves relative paths and keeps local contract errors outside the network rejection handler; its regression test covers this case. The two already-frozen sports snapshots and their HTTP freeze logs were SHA-pinned and recovered **offline**, with zero further HTTP requests, into `data/capability_records/p102_connected_recovery_salvaged_v2/`.

Every new target then passed `scripts/p102_connected_wiki_gate.py`, which checks title, normalized page URL, and split against pinned P92 router, P95 merged pool, and P97 gated pool (694 prior unique titles/URLs). The stadiums-in-Serbia target conflicted with an earlier **eval** page while its anchor is train, so the global gate rejected it. The second-division-clubs target was globally novel. `scripts/p102_merge_admitted.py` required both gates to use exactly the same prior registry pins and rejected any duplicate new target title/URL across gates. The final source pool `data/capability_records/p102_final_connected_pool_v1/` has two train worlds (heritage and sports), five native JOIN tasks, and no eval world. The global-gate ledger records the excluded stadium target; its two tasks never enter the final pool.

An additional reader-visible identity check found that native exact-row-name JOIN is not sufficient to establish real entity identity. The sports source's first page has two `Mladost Stadium` rows, and the target page's same-name row belongs to a different team/city; one of the five native tasks is a false JOIN. `scripts/p102_join_identity_gate.py` now requires a unique name on both pages plus a nonconflicting independent witness in Team/Club, City/Location, State/Province, or explicit Country-to-page scope. It rejects mismatched auxiliary fields. The final **curated** P102 shard is `data/candidates/p102_final_connected_identity_v2/merged/`: four tasks/views, heritage three and sports one. The kept sports task has Team=`Sloboda` and City=`Užice` on both pages. The three heritage questions use the same bound Stockholm Palace row but ask for different target attributes/answers: Swedish name, date, and condition. Their two visible Location cells are `Stadsholmen, Stockholm` and `Stockholm`.

Final P102 lengths and supervision are measured after the locked chat template: three heritage readers are 8,325–8,330 tokens, one sports reader is 53,158 tokens (32–64K); all four are train. The original native position audit maps selector and target cells at 5,839–5,856 tokens apart for heritage and 40,615 tokens apart for the kept sports case. The independent all-reader curated mask passes 4/4, with 78,141 full-chat tokens and 31 supervised tokens. These are candidate-only results (`train_ready=false`), not evidence of model improvement or an unrestricted minimum-proof guarantee.

The same identity rule was applied to P101 geothermal JOINs without changing their frozen sources. The two `Heber` directions have an identical complete Location cell in both pages and remain in `data/candidates/p102_p101_geothermal_curated_v2/merged/`; the three `Imperial Valley` tasks are excluded because the location cells differ in `name=Salton Sea` versus `name=Imperial Valley`. Their numeric coordinates agree, but the visible source does not establish that the two named facilities are the same entity. The curated P101 shard has two train views, each about 26K; its independent exact-mask audit checks 2/2. The original five-row P101 candidate is historical and must not enter a new index.

The pilot demonstrates that parameterized domain vocabulary alone does not produce JOIN-supporting worlds at high rate: 14 domain anchors and 82 page previews yielded two globally admissible worlds and four high-confidence P102 tasks. The primary constraints were pages without compatible row keys, unrelated list-page search results, strict target-cell/shortcut filtering, cross-split page reuse, and entity-name ambiguity. The source support/rejection ledgers are retained rather than filling unsupported domains with unrelated pages.

Inspect the exact evidence and receipts:

```bash
cat data/capability_records/p102_connected_wiki_pilot_v1/acquisition_manifest.json
cat data/capability_records/p102_connected_recovery_salvaged_v2/acquisition_manifest.json
cat data/capability_records/p102_final_connected_pool_v1/manifest.json
less -R data/capability_records/p102_final_connected_pool_v1/admission.jsonl
less -R data/candidates/p102_final_connected_identity_v2/identity_audit.jsonl
cat data/candidates/p102_final_connected_identity_v2/merged/manifest.json
cat data/candidates/p102_final_connected_curated_full_mask_v2/manifest.json
less -R data/candidates/p102_final_connected_positions_v1/position_index.jsonl
less -R data/candidates/p102_p101_geothermal_curated_v2/identity_audit.jsonl
```

Replays, all offline:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p102_recover_frozen.py --config configs/p102_recover_frozen_v2.json --output-dir data/capability_records/p102_connected_recovery_salvaged_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p102_connected_wiki_gate.py --config configs/p102_connected_gate_v2.json --output-dir data/capability_records/p102_connected_admitted_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p102_merge_admitted.py --config configs/p102_final_union_v1.json --output-dir data/capability_records/p102_final_connected_pool_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p102_join_identity_gate.py --config configs/p102_final_identity_v1.json --output-dir data/candidates/p102_final_connected_identity_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py data/candidates/p102_final_connected_identity_v2/merged --all --output data/candidates/p102_final_connected_curated_full_mask_v2 --verify-only
```
