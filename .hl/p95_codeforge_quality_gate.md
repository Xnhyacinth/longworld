# P95 CodeForge content-proof eligibility in candidate balancing

The P94 DuckDB/Wasmtime lane has 43 valid candidate readers and 43/43 exact
assistant-mask replays. Its stronger P65 **content-backed scoped** filename
reading certificate is 0/43. The DuckDB proof classifies 24 tasks outside
the proof grammar, seven with a raw-only scoped certificate that fails after
record text is normalized while filename aliases are preserved, four with a
literal-copy opportunity, three with no effective approval condition and two
with redundant source support. Wasmtime has two outside-grammar tasks and one
literal-copy opportunity. Outside-grammar means **not certified by this
checker**, not a demonstrated incorrect answer. The raw-only seven are not
content-backed after the source-text control.

`scripts/select_p90_balanced_candidates.py` now has an optional positive
CodeForge evidence gate. The P95 config hash-pins the P65 legacy proof and
both P94 proof receipts plus their `proofs.jsonl` files. The selector checks
receipt profile and inventory, then keeps a CodeForge view only if its source
group, semantic task ID, sample ID, split and final-chat token count match a
proof row with `content_backed_scoped_certificate=true`. It never interprets
mere native program replay, a raw filename certificate or missing proof as
strong reader evidence. All non-code rows retain their prior **candidate**
status. This gate is opt-in, so the frozen P94 v4 selection replays unchanged.

Against `data/candidates/p94_candidate_refs_v5`, the 860 CodeForge views have
847 matching proof records: 804 legacy P65 and all 43 P94. Only 130 legacy
views carry positive content-backed certificates; 717 proof-covered views
fail and 13 have no proof. Twelve legacy failed-proof rows differ from their
proof's full-chat count by one token; they remain excluded. All 130 positive
proofs match current sample IDs, splits and full-chat counts exactly. The
P94 DuckDB/Wasmtime 43 are therefore all excluded without deleting them from
the frozen candidate index.

The [P95 selection manifest](../data/candidates/p95_balanced_selection_code_proof_v1/manifest.json)
records 10,106 input views, 9,376 views after the CodeForge eligibility
filter and 736 balanced selected tasks. Of the selected tasks, 104 CodeForge
views all have positive scoped content proofs; **zero** come from the new
DuckDB or Wasmtime worlds. For comparison, the unchanged P94 v4 balance
selected 827 tasks including 195 CodeForge, of which 27 were the new
proof-failing tasks. The new selection has 492 train / 244 eval views and
153 under-32K, 197 at 32K, 208 at 64K and 178 at 128K. Its supervised-token
sum is 89,658 versus 80,124 in the old selection because the round-robin
fills other cells after removing unqualified code. This is a selection
distribution change, not a model-gain result.

The positive P65 certificate is scoped to finite filename aliases, a single
raw-token window and the declared content control. Its proof receipt still
has `strict_long_dependency_verified=false`; it does not certify arbitrary
long-document reasoning. Finance, Wiki, simulation, paper and RFC rows need
their own source-specific evidence gates. P95 remains a **candidate-only**
selection with `train_ready=false` and no GPU experiment.

Inspect and replay:

```bash
cat data/candidates/p95_balanced_selection_code_proof_v1/manifest.json
cat data/candidates/p94_codeforge_duckdb_reading_proof_v1/BUILD_RECEIPT.json
cat data/candidates/p94_codeforge_wasmtime_reading_proof_v1/BUILD_RECEIPT.json
less -R data/candidates/p95_balanced_selection_code_proof_v1/selected_refs.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p95_balanced_selection_code_proof_v1.json \
  --output data/candidates/p95_balanced_selection_code_proof_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/select_p90_balanced_candidates.py \
  --config configs/p94_balanced_selection_v4.json \
  --output data/candidates/p94_balanced_selection_v4 --verify-only
```

Seven focused selector tests, Ruff and both exact selection replays pass.
