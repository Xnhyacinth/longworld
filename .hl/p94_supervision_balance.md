# P94 candidate supervision balance on the P93 frozen index

The P93 candidate index has 9,788 reader views and 3,305,745 recorded
supervised tokens. Controlled simulation supplies 3,092,363 of those tokens
(93.54%), despite supplying 6,116/9,788 views. Its answers are often longer
than real-document answers; balancing rows alone does not balance the loss.
These are index-side token counts, not an independently retokenized data loader.

The existing split × source kind × operation × physical-length round-robin,
with a 400/100 train/eval per-kind view cap, selected 1,179 unique tasks from
526 groups in `data/candidates/p94_balanced_selection_v1`. It selected
251,535 simulated supervised tokens out of 288,162 total (87.29%). The
source-aware view cap alone therefore leaves the same supervision imbalance.

P94 adds an optional, explicit `max_supervised_tokens_by_kind` parameter to
the existing selector. It filters each proposed view before admission and
records the cap in the frozen selection manifest. With a 40,000-token cap on
controlled simulation, `configs/p94_balanced_selection_v2.json` selects
782 unique tasks from 153 groups: 103 simulated, 304 Wiki, 192 finance,
168 code workflow, 8 grounded simulation and 7 paper revision. The measured
supervised-token distribution is 39,998/76,625 controlled simulation
(52.20%). Its actual full-chat bins are 176 under 32K, 231 at 32K–64K,
201 at 64K–128K and 174 at 128K–256K. This is a reviewable candidate
selection, not an endorsed training recipe or proof of model gain.

The selector still does not enforce all source-specific evidence/dependency
profiles or independently replay each assistant mask. Those gates are
separate from balancing. The cap does not make a low-quality real task useful,
and it cannot create missing real L2/L3 tasks. The P91 frozen selection
replayed byte-for-byte after this optional change, so its old configuration
remains compatible.
In the 782-view P94 v2 set, 283 rows have no dependency status and another
192 finance rows carry only `native_candidate`; neither label certifies
reader-text necessity. This is why the selection remains candidate-only.

After the P94 real-source expansion, `configs/p94_balanced_selection_v3.json`
reuses the same 40,000-token simulation cap on
`data/candidates/p94_candidate_refs_v3`. It selects 824 distinct tasks from
171 groups and 80,043 supervised tokens, of which 39,998 are controlled
simulation (49.97%). It retains all 12 new train Wiki dense-table scans and
all 7 new eval Wiki L2 pair/interval tasks. The selected 27 new CodeForge
tasks remain research candidates: their stronger P65 content-backed
reader-dependency certificate is 0/43 for the entire new CodeForge batch.
Balancing must not silently upgrade their quality status.

On the final P94 index with 48 additional real-document length views,
`configs/p94_balanced_selection_v4.json` selects 827 unique tasks from
171 groups and 80,124 supervised tokens, with 39,998 simulated (49.92%).
Its selected physical bins are 166 under 32K, 245 at 32K–64K, 228 at
64K–128K and 188 at 128K–256K. It keeps one view per semantic task:
all four new cross-page comparison tasks use a selected 32K/64K/128K
view, while eight of the 12 school dense-scan tasks use a selected
64K/128K view. This is a distribution pilot, not a training release.

Inspect and replay:

```bash
cat data/candidates/p94_balanced_selection_v1/manifest.json
cat data/candidates/p94_balanced_selection_v2/manifest.json
less -R data/candidates/p94_balanced_selection_v2/selected_refs.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p94_balanced_selection_v2.json \
  --output data/candidates/p94_balanced_selection_v2 --verify-only
```
