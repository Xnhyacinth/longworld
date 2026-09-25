# P89 integrated synthesis and shortcut closeout

Status: **hash-verified candidate bank; `train_ready=false`**. Current index:
`data/candidates/p89_final_candidate_refs_v1/manifest.json`. P87's earlier
v5 hybrid shard is superseded because an ID-only modulo baseline solved all
12 views. The final bank contains only the corrected v6 hybrid shard.

## Measured result

The index references P83, P89 Wiki, and the corrected P86/P87 native shard:
**8,085 reader views, 7,932 independent semantic tasks, 1,077 source/world
groups**. Split counts are 6,275 train and 1,810 eval. There are 153 groups
with more than one operation, 18 domain labels, 40 topic labels, and 36
operation labels. Actual full-chat bins: `<32K` 965; `32–64K` 2,252;
`64–128K` 3,000; `128–256K` 1,868. This is +842 views and +834 tasks over
P83. The change is 800 controlled state tasks, seven real paper revision tasks,
four hybrid tasks exposed at 12 lengths, and 23 new real Wiki lookup tasks.

P89 froze 12 previously unused Wikipedia list pages across architecture,
cultural heritage and geology, with 763 source fact spans and revision/hash/
CC BY-SA receipts. Two groups produced 23 independent table-cell lookups
(22 train, one eval) at 36,872–43,734 final-chat tokens; ten native rows were
rejected. The volcano group yielded zero tasks. An offline typed-row overlap
index found **zero new cross-document JOINs** from these pages: seven candidate
pairs duplicated prior tasks and three failed topic/domain compatibility.
Thus P89 expands real-topic L1 supervision but does not fix the real L2/L3 gap.
The 23 lookup answers provide only 165 supervised tokens over 855,228 final
chat tokens, so sample count alone overstates their training weight.

P86's 100 controlled worlds generate four operations each, including two
state operations whose answers change under both effective-date and
disclosure-date boundary interventions. The native replay checked 800/800
reader rows twice, 3,200 legal text deletions and 1,600 date-boundary
changes. This is a structural state curriculum, not real historical prose.
The paper lane contributed seven explicit version-alignment tasks from three
real works; it is not natural-intent QA. The hybrid lane pairs verbatim RFC
rules with clearly simulated connection records. It supplies four independent
tasks, but only one real rule source and two simulated worlds.

## P87 shortcut repair and final-byte checks

The original hybrid generator mapped `cx-seed-i` IDs to attributes via
`i mod 9`. Independent review reconstructed 12/12 answers from IDs alone.
Corrected v6 uses the same opaque IDs and row order in paired worlds while
independently permuting attributes. In all six `(operation, length)` pairs,
the ID-only projection is identical and the answers differ. The rule text
and simulated state are reparsed from the final reader; rule removal and
replacement plus a positive-state-record deletion change the bounded oracle
result. The corrected native output passed exact token-offset and
assistant-mask checks. This defeats the demonstrated ID-only shortcut; it
does not prove absence of every semantic or model shortcut. A separate
bounded review of 24 P86 as-of set tasks found 0/24 exact answers from six
ID/position baselines, also not a general proof.
Recompiling v6 with two workers reproduced its manifest, reader JSONL and
sample index byte for byte.
The frozen v5 manifest and `configs/p87_hybrid_rfc9114_pilot_v1.json` keep
their original pinned hashes; its invalidation is recorded separately in
`data/candidates/p87_hybrid_rfc9114_pilot_v5/selection_status.json` and the
historical P87 note. V6 uses `configs/p87_hybrid_rfc9114_pilot_v6.json`.

`scripts/merge_p86_native_candidates.py` deterministically rebuilt the
corrected native shard byte for byte. The P89 Wiki lane used the existing
unified native adapter and passed `--resume`. The three-shard final index
passed global sample/task/split checks and full reader SHA-256 validation,
without recopying P83's 2.14 GB reader files during append. P89's 23/23 final
Wiki readers passed pinned-Qwen assistant-only mask replay. The corrected
hybrid/state/paper shard passed 22/22 train-cell and 12/12 eval-cell final
mask audits; the P86 native verifier separately checked all 800 state rows.
These checks use the diagnostic `scripts.train_sft` contract; the final
SWIFT/Megatron loader and model optimization remain untested.

The source-capacity bottleneck remains material: arXiv legacy and OAI-PMH
queries both returned HTTP 406 from this environment, yielding zero newly
acquired paper works. The Wikipedia expansion succeeded but generated only
lookup tasks. Hundreds of domains, broad real L2/L3/L4 task coverage and a
demonstrated model gain have **not** been delivered. Training selection must
cap the 800 simulated rows by world and account separately for input tokens,
supervised tokens, source works and semantic tasks before any experiment.

Inspect the final reviewable bytes:

```bash
cat data/candidates/p89_final_candidate_refs_v1/manifest.json
cat data/candidates/p89_unified_scale_hybrid_v6_shard_v1/manifest.json
cat data/candidates/p89_unified_wiki_shard_v1/merged/manifest.json
cat data/candidates/p87_hybrid_rfc9114_pilot_v6/manifest.json
cat data/candidates/p89_all_reader_mask_audit_v1/manifest.json
cat data/candidates/p89_scale_hybrid_v6_mask_grid_audit_v1/manifest.json
cat data/candidates/p89_scale_hybrid_v6_eval_mask_audit_v1/manifest.json
less -R data/candidates/p89_unified_scale_hybrid_v6_shard_v1/sample_index.jsonl
```
