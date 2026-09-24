# P84 ResearchLab source expansion and quality gate

Status: **source route and candidate audit completed; zero quality-admitted tasks**. The P83 unified bank remains the latest merged candidate bank. No GPU training or release promotion was run.

## Source and native construction

The P84 router reads two frozen arXiv revision families from pinned source bundles and hashes, excludes papers already used by the P66 bank, and sends supported families to the existing P66 compiler. Adam (9 revisions) yielded zero native long-form task specs; eight adjacent version pairs were rejected as too short or without a substantive delta under the native heuristic. AEVB (11 revisions) yielded 11 raw train-split semantic tasks from six adjacent version pairs; four other pairs were rejected. Ten raw tasks fit the 64K capacity and one fits 128K. This is one paper family and one operation, not a broad new domain or capability distribution. The source bundles are historically signed and hash-bound; this wave did not freshly verify the HMAC in the current environment.

The native build ran with two processes and its `--validate` mode rebuilt the output and matched its file hashes, task counts, and bindings. The independent reader audit checked all 11 context hashes, exact user/assistant message bytes, answers, split, pinned Qwen3.5 chat tokenization, and assistant-only mask. Final chat lengths were 33,688–67,350 tokens. It found 508,928 masked prefix tokens and 2,594 supervised assistant tokens, with 11/11 mask checks passing. This verifies candidate serialization, not long-context model learning or the downstream SWIFT/Megatron loader.

## Quality decision

All 11 raw tasks failed the conservative content gate: three compare added/removed files, five use only LaTeX commands, headings, or short excerpts, and three include prose replacements but repeat the same answer evidence under copy paths. The native receipt already marks `local_training_candidate=0` and `strict_long_dependency_verified=false`; the P84 quality selector marks **0 review candidates**. Its `train.jsonl` is a raw native candidate export and must not be merged into a training selection or counted as qualified train tasks. The P83 bank therefore stays at 7,243 views, 7,098 independent semantic tasks, and 971 groups.

This failure locates the next compiler fix: identify semantically meaningful revision hunks inside each source file, deduplicate copied file trees and equivalent answer evidence before sampling, then run reader-text interventions and alternate-support checks on the final context. More source vocabulary or more versions of this paper would not fix the current first-change-line behavior. Real source expansion also needs additional independent papers and source forms; the present route is a reusable pinned-family adapter, not evidence of hundred-domain scale.

Inspect the receipts and rejected cases:

```bash
cat data/capability_records/p84_researchlab_source_route_v1/routing_receipt.json
cat data/candidates/p84_researchlab_aevb_native_v1/BUILD_RECEIPT.json
cat data/candidates/p84_researchlab_aevb_reader_audit_v1/audit_manifest.json
cat data/candidates/p84_researchlab_aevb_quality_v1/quality_manifest.json
less -R data/candidates/p84_researchlab_aevb_quality_v1/rejected.jsonl
```

Source route: `configs/p84_researchlab_source_route_v1.json`, `scripts/route_p84_researchlab_sources.py`. Independent checks: `scripts/audit_p84_researchlab_reader.py`, `scripts/filter_p84_researchlab_quality.py`. The two focused test files passed (5 tests), as did Ruff and `git diff --check`.
