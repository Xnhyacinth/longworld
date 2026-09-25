# P94 CodeForge expansion: two signed repository worlds

The P94 CodeForge lane compiles frozen, source-attested DuckDB and Wasmtime
workflow bundles through the existing native taskbank and SFT projection.
These are **43 candidate reader views and 43 independent tasks**, not a new
general code parser or verified model-learning result.

| Repository | License in all frozen episodes | Signed episodes / eligible | Native tasks | Split | Reader length |
| --- | --- | ---: | ---: | --- | --- |
| DuckDB | MIT | 8 / 7 | 40 | train | 64K and 128K bins |
| Wasmtime | Apache-2.0 | 3 / 2 | 3 | eval | 128K bin |

One episode in each source is rejected because its merged head has no
source-readable diff paths. There is no source fact fallback. The native
receipts pin each bundle, underlying export, source code and tokenizer. The
catalog preserves repository-level splits, and the P94 audit checks both
groups and `(source_kind, semantic_task_id)` against the frozen P93 index.
The P93 index has no overlap with either repository. The two source banks
were built concurrently as two bounded processes; native materialization,
replay and projection were run under the existing P17 source-role trust.
The native receipt's `genuinely_new_repository=false` is a conservative
release-eligibility field; the audit's two new groups mean only **new relative
to the P93 candidate index**.

The [unified manifest](../data/candidates/p94_codeforge_unified_v1/merged/manifest.json)
records 43 views: 40 train, 3 eval; 15 in the 64K bin and 28 in the 128K
bin. Full chat lengths range from 73,384 to 239,144 tokens, with nine distinct
reader contexts and six native operations. The [mask audit](../data/candidates/p94_codeforge_audit_v1/manifest.json)
replayed all **43/43** final readers with the pinned assistant-only training
mask, summing 6,568,038 full-chat and 6,211 supervised tokens. It is bound
to the P93 index and P94 native/merged manifests and passes `--verify-only`.

The stronger existing P65 filename-copy proof was also replayed on both new
banks. [DuckDB](../data/candidates/p94_codeforge_duckdb_reading_proof_v1/BUILD_RECEIPT.json)
has seven tasks with the finite raw single-window alias certificate, but
**zero content-backed scoped certificates**. [Wasmtime](../data/candidates/p94_codeforge_wasmtime_reading_proof_v1/BUILD_RECEIPT.json)
has zero under either profile. The remaining operations lie outside that
filename-copy proof's scope. Native program replay proves the oracle over
visible records, but this wave does **not** prove that every reader needs
distributed text or survives the P65 content control. Keep `train_ready=false`
and do not promote these rows as strong long-dependency examples.

Reproduce and inspect:

```bash
UV_LINK_MODE=copy uv run --offline python scripts/run_unified_synthesis_batch.py \
  --config configs/p94_codeforge_unified_v1.json \
  --output data/candidates/p94_codeforge_unified_v1 --workers 2 --resume
UV_LINK_MODE=copy uv run --offline python scripts/audit_p94_codeforge_expansion.py \
  --batch data/candidates/p94_codeforge_unified_v1 \
  --base-index data/candidates/p93_final_candidate_refs_v5 \
  --output data/candidates/p94_codeforge_audit_v1 --verify-only
cat data/candidates/p94_codeforge_audit_v1/manifest.json
cat data/candidates/p94_codeforge_duckdb_v1.native_banks/duckdb/BUILD_RECEIPT.json
cat data/candidates/p94_codeforge_wasmtime_v1.native_banks/wasmtime/BUILD_RECEIPT.json
less -R data/candidates/p94_codeforge_unified_v1/merged/sample_index.jsonl
less -R data/candidates/p94_codeforge_duckdb_reading_proof_v1/proofs.jsonl
```

The `--resume` command verifies the prior native/unified batch without
regenerating it. The P65 proof validation used the P17 source-role wrapper
with the frozen public-policy, source-client and `HF_HOME` pass-through.
Six focused novelty tests and Ruff pass. No GPU training was run.
