# P114 automatic Wiki source expansion: short L1 anchor lane

The frozen result is `data/candidates/p114_wiki_scale_screen_v1` (`manifest.json` SHA-256 `51dceb614114b19451cd37f398da44645d139a5a88c40158bb82e70e0835654b`), with exact post-screen mask receipt `data/candidates/p114_wiki_scale_screen_mask_v1/manifest.json` (SHA-256 `dd64edd908704472187c3c195a6343303bf79ad3cdc0ededd51de8459470214c`). These are local research candidates, `train_ready=false`, in a separate **short L1 anchor** lane. They are not a multi-evidence long-dependency dataset and were not added to the repaired P113 reader index.

This wave reused the P108 automatic title/category discovery, P93/P97 source acquisition and sharding, P99 global title/URL gate, P92/P76 native source-shape probe and compiler, P112-style normalized title shortcut screen, and the independent final-template mask auditor. No topic-specific question function or invented domain labels were added. The new catalog changes only P97's `max_queries_per_shard` from 40 to 8, preserving all automatically selected terms and source bindings, so three intake shards can run concurrently. P99 used three intake processes and four native task workers.

| Stage | Observed result |
| --- | ---: |
| Pinned discovery inventory | 15 intakes, 293 historical discovery queries, 314 historical source groups |
| Strict prior union | 247 groups, 707 distinct pages (not new contributions) |
| New term candidates | 276; only 19 passed strict novelty and topic checks (13 train, 6 eval; 9 inherited domain labels) |
| New acquisition | 19 gross groups, 32 pages, 1,095 facts |
| Global source gate | 18 new groups, 28 pages, 471 facts; one train group rejected for overlap with an eval page |
| Native shape and compilation | 5 legal lookup jobs; 59 unsupported source/recipe cells; 31 native row rejects; 79 tasks/views |
| Independent pre-screen mask | 79/79 final chats |
| Post-native screen | 17 answer-concentration rejections, 1 answer already in question/title, 61 retained tasks/views |
| Exact novelty vs repaired P113 index | 61 new semantic task IDs, zero answer-hash conflicts or prior sample overlaps |
| Independent final mask | 61/61; 494,330 full-chat tokens, 475 supervised tokens |
| Split and length | train 15, eval 46; 57 below 32K, 4 in 32–64K |

The 19 terms are the **actual yield** of this frozen metadata, not a quota. The catalog rejected 180 previous query terms, 65 without sufficient new title/category evidence, seven prior topics and five title fragments. Of the 18 new source groups, only five supported an admitted native operation; all 79 native tasks are `table_cell_lookup`. The final 61 samples also come from five groups. One group originally produced 17 copies of the answer `Kitchener`; keeping at most two identical answers per source removes this easy answer prior. The exact title/question screen rejected sample `p75-93113e08977d034ac183` because `Waterloo` can be read from the question/title without the target cell. Rejection decisions are in `decisions.jsonl`.

Every retained task has **one** necessary value span, only 1–9 tokens wide in the executed lineage. Forty-five readers contain one source document and 16 contain four, but the four-document readers still consume only one fact span. The four physically longer inputs (38,655–38,668 chat tokens) each contain one document and one fact; exact final-chat evidence-to-question distances are **154, 175, 296 and 748 tokens**. Their physical length is background before near-query evidence, not a far-distance or cross-document dependency. The last selected evidence to input end across all retained rows ranges from 83 to 16,134 tokens; this is placement, not a certified shortest proof. No padding was added to force longer bins.

The native lookup exporter independently replays its answer from visible table text and performs a scoped cell-removal check. P114 verifies each native proof/index against the unified reader, checks all 79 source masks, applies normalized question/title and malformed-answer screens, checks exact task ID and answer hash against `p113_book_quarantined_refs_v1`, then emits the 61-row shard. A fresh independent audit of the emitted bytes passed all 61 assistant-only masks. These checks do not exclude every prose paraphrase or establish multi-document necessity.

Reproduce the frozen stages and inspect samples:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/p108_wiki_autotopic_inventory.py \
  --search-root /volume/pt-dev/qjiu/longworld-worlds/data/capability_records \
  --output data/capability_records/p114_wiki_scale_inventory_v1/manifest.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p108_wiki_autotopic_prior.py \
  --config data/capability_records/p114_wiki_scale_configs_v1/prior.json \
  --output-dir data/capability_records/p114_wiki_scale_prior_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p108_wiki_autotopic_catalog.py \
  --config data/capability_records/p114_wiki_scale_configs_v1/catalog.json \
  --output-dir data/capability_records/p114_wiki_scale_catalog_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/plan_p97_wiki_intake_shards.py \
  --catalog data/capability_records/p114_wiki_scale_catalog_sharded_v1/catalog.json \
  --output-dir data/capability_records/p114_wiki_scale_plan_sharded_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/run_p99_wiki_campaign.py \
  --config data/capability_records/p114_wiki_scale_configs_v1/campaign.json \
  --output-dir data/capability_records/p114_wiki_scale_campaign_sharded_v1 --mode verify
UV_LINK_MODE=copy uv run --offline python scripts/p114_wiki_scale.py \
  --config configs/p114_wiki_scale_v1.json \
  --output data/candidates/p114_wiki_scale_screen_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py \
  data/candidates/p114_wiki_scale_screen_v1 --all \
  --output data/candidates/p114_wiki_scale_screen_mask_v1 \
  --max-seq-len 131072 --verify-only
cat data/candidates/p114_wiki_scale_screen_v1/manifest.json
less -R data/candidates/p114_wiki_scale_screen_v1/decisions.jsonl
less -R data/candidates/p114_wiki_scale_screen_v1/sample_index.jsonl
```

The next long-dependency expansion should select richer source shapes and operations before more acquisition. This frozen topic metadata produced useful real-source L1 anchors but no new long-dependency task; repeating these terms or adding length padding would inflate volume without the intended capability.
