# P87 unified long-context synthesis checkpoint

Status: **historical candidate checkpoint, superseded by P89**; `train_ready=false`.
Independent review subsequently found that the P87 hybrid v5 connection IDs encoded
attributes by a modulo pattern: an ID-only baseline recovered all 12 answers.
The corrected v6 pairs identical IDs with different visible states and appears
only in the P89 final index. Do not select the P87 v5 shard for training. No
GPU training, model-gain claim, release promotion, or redistribution of newly
fetched full texts occurred.
The v5 native manifest and v1 config retain their original pinned SHA-256
values. Its invalidation is a separate
`data/candidates/p87_hybrid_rfc9114_pilot_v5/selection_status.json` sidecar;
the historical shard's `--verify-only` receipt check still passes.

## Capability and quantity result

The historical reference index is `data/candidates/p87_p83_scale_hybrid_refs_v1/manifest.json`. It combined the frozen P83 bank with one small P86/P87 canonical shard, without copying P83's 2.14 GB reader files on append. Its **8,062 views / 7,909 independent semantic tasks / 1,075 source-world groups** include 6,253 train and 1,809 eval views. These counts are not the current final bank. There were 153 groups with more than one operation, 16 domain labels, 38 topic labels, and 36 operation labels. Physical final-chat bins were `<32K` 965, `32–64K` 2,229, `64–128K` 3,000, and `128–256K` 1,868. Source/work identity, operation, actual token length, and semantic task ID remain distinct dimensions; these labels are not 16 independent domain ontologies or proof of domain generalization.

The 819 newly added views comprise:

| Native lane | Independent tasks / views | Source groups | What was checked |
| --- | ---: | ---: | --- |
| Real paper revision | 7 / 7 | 3 works | Pinned archives, substantive unique prose, visible version replay, exact-line and record deletion; 13/20 adjacent pairs rejected |
| Controlled shared state | 800 / 800 | 100 worlds | Four operations (200 each), shared record IDs, 3,200 legal reader-text deletions, 1,600 effective/reveal boundary changes; 100 accepted, 0 rejected |
| Real RFC rule + simulated state | 4 / 12 | 1 real-rule group, 2 simulated worlds | Native rule/state interventions passed, but later ID-only shortcut review invalidated v5 for selection |

P86 state actual lengths are 30,811–170,993 tokens: 74 of 800 fall below 32K despite planned long cells. The longest bounded required-fact span is 162,456 final-chat tokens. Its 2,905 cross-operation record-ID occurrences are overlap evidence, not a count of globally distinct facts. P87 hybrid is 15,349–56,310 tokens and has four below 32K views. P86 paper is 33,079–35,831 tokens with 21,850–31,972 bounded evidence-token extent. The paper questions are explicit revision-alignment instructions; state questions are explicit structured operations. Neither is natural-intent transfer evidence.

## Pipeline and mask

`scripts/merge_p86_native_candidates.py` normalized the paper, state and v5 hybrid native readers into `data/candidates/p87_unified_scale_hybrid_shard_v1/`, checked source/receipt hashes, split, semantic-task/answer consistency and reader-only bytes, and deterministically rebuilt the shard with `--verify-only`. `longworld/synthesis/sharded_candidate_bank.py` then extended the P83 reference index; full reader hashes of both shards and the global duplicate/split ledger passed. These checks did not detect the ID-only shortcut. The P83 reference file is about 9.45 MB while its existing reader bodies are about 2.14 GB; full training bytes are materialized only when a later selection needs them. The new storage path is not yet wired into the historical unified runner automatically.

`scripts/select_p86_mask_grid.py` chooses one final reader per occupied `(source_kind, operation, actual_length_bin)` cell, preferring train; it supports a separate eval pass. The P83 bank passed 87/87 selected-cell Qwen3.5 chat/mask replays (8,579,929 full-chat and 29,214 supervised tokens). The final P87 shard passed 22/22 train and 12/12 eval selected cells, with about 2.52 million full-chat tokens retokenized. The P86 state native verifier independently rechecked all 800 final rows and their assistant-only masks. These are candidate reader checks through `scripts.train_sft.tokenize_assistant_only`; the eventual SWIFT/Megatron loader and an actual optimization step remain unverified.

The new unified shard has 49,737,183 input tokens and 197,652 supervised tokens: paper 238,546/1,903, state 49,064,053/194,225, hybrid 434,584/1,524. Sampling for training must be controlled by source groups, independent tasks, input tokens and supervised tokens; using all 800 simulated state rows by default would overwhelm the small real/hybrid lanes. No training mix has been promoted.

## Source-capacity limit and next work

The frozen paper inventory has roughly ten distinct works. A bounded three-process pass over five works admitted only seven new train tasks, so hundreds of grounded topics cannot be obtained by permuting labels or copied revisions. The official arXiv legacy API returned HTTP 406 on two spaced requests; `data/capability_records/p87_arxiv_bounded_acquisition_v2/manifest.json` records **zero** newly acquired works/archives. The acquisition code retains one-connection, three-second pacing, per-work split and license fields; offline tests pass. This is an access constraint for that route, not a fabricated source expansion. arXiv permits local research use subject to its API terms and requires rights for redistribution of most full texts ([official terms](https://info.arxiv.org/help/api/tou.html)).

The next scaling bottleneck is still diverse, source-backed semantic structure: more independent source works and native tables/reports/books/code, source-held-out splits, natural-intent questions, denser real L2/L3 coverage, and reader-level alternate-support checks. LongFaith's known-gold/citation route and DeepReasonQA's real-document graph composition are useful for proposing faithful questions; neither by itself establishes that every necessary fact is remote in the final reader ([LongFaith](https://arxiv.org/abs/2502.12583), [DeepReasonQA](https://aclanthology.org/2026.findings-acl.1306/)). WildLong's co-occurrence-based task-form expansion suggests a low-cost wording layer, but source truth and dependencies must stay in native compilers ([WildLong](https://arxiv.org/abs/2502.16684)).

Inspect the final candidate state:

```bash
cat data/candidates/p87_p83_scale_hybrid_refs_v1/manifest.json # historical; use P89 final for selection
cat data/candidates/p87_unified_scale_hybrid_shard_v1/manifest.json
cat data/candidates/p86_frozen_paper_batch_v2/manifest.json
cat data/candidates/p86_state_shared_scale_v2/manifest.json
cat data/candidates/p87_hybrid_rfc9114_pilot_v5/manifest.json
cat data/candidates/p87_final_mask_grid_audit_v1/manifest.json
cat data/candidates/p87_final_eval_mask_grid_audit_v1/manifest.json
less -R data/candidates/p86_frozen_paper_batch_v2/rejected.jsonl
less -R data/candidates/p87_unified_scale_hybrid_shard_v1/sample_index.jsonl
```
