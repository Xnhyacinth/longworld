# P14.3 CodeForge Wasmtime patch/review/test closeout (2026-09-02)

## Outcome

The Wasmtime `patch_review_test_ancestry` candidate is a complete local-probe
9-cell world. It contains native signed full, counterfactual, and ordered views
at exact 16K, 32K, and 64K bands. All nine rows passed pinned dense ranking and
strict episode-bundle replay; the audit accepted 9/9 rows and rejected 0.

The task does not use `version_selection` or `release_supersession_trace`.
Each release cycle joins five exporter-grounded records: the merged head patch,
the merge-linked `APPROVED` review, one release-linked selected final pre-merge
test, the merge record, and verified merge-to-tag release ancestry. Answers bind
the patch SHA-256, review decision, named test result, and ancestry. The
counterfactual changes the selected test result from passed to failed. Removing
any essential artifact changes the answer.

## Authentic source inventory

The chronological bundle uses four distinct Wasmtime release episodes and a
natural 1/2/4-cycle program:

| Release | Pull request | Patch/review/test/merge/release closure | Ancestry |
|---|---:|---|---|
| v45.0.0 | 13251 | closed | exporter-verified merge to tag |
| v46.0.0 | 13354 | closed | exporter-verified merge to tag |
| v47.0.0 | 13580 | closed | exporter-verified merge to tag |
| v48.0.0 | 13806 | closed | exporter-verified merge to tag |

Source manifests are under `reports/p14_codeforge_wasmtime_sources_v1/` and
`reports/p14_codeforge_wasmtime_sources_v4/`. Their SHA-256 values are:

- v45: `b8aa3d4b3c2add33c13f18601aaab5bbd5fec463b92ebab5cf9acce708f75bb6`
- v46: `bfa6b808ed511db51aa0f26a13cf98b91d13595ec386ed602b652999d798901c`
- v47: `a5615762b167cd34602eb9a7c1a352c4a95f844b33059b659f726eb85e17143b`
- v48: `533a52d3d01f27997fd3d6d26f8b18f831fd0187115a362425329d4f9e8228f2`

The signed chronological-causal-union bundle SHA-256 is
`50bd2e1ec48b99b74cbf85cdeec6bc73d2a3b6e902d9c93b0d985801e84872ac`.

## Exact 9-cell result

| Band | Full | CF | Ordered | Cycles / essential events |
|---|---:|---:|---:|---:|
| 16K | 16,363 | 16,363 | 16,363 | 1 / 5 |
| 32K | 32,694 | 32,694 | 32,694 | 2 / 10 |
| 64K | 64,842 | 64,842 | 64,842 | 4 / 20 |

All cells have `full_sufficient`, `remove_one_fails`,
`counterfactual_changes_answer`, `counterfactual_replay_sufficient`,
`local_window_insufficient`, and `contiguous_windows_insufficient` true. All
native view replays reproduce the expected answer. The exact natural context
is unchanged across the three views of each band; no padding, repeated source
text, or truncation is used.

The mean authentic source-token ratio is 0.9959. Near-duplicate sentence ratios
are 0.0000, 0.0111, and 0.0133 at 16K, 32K, and 64K. There are zero exact
duplicate rows. The candidate contains three distinct executable proofs and
three answer programs.

## Dense and strict replay audit

Pinned dense rankings use
`sentence-transformers/all-MiniLM-L6-v2@1110a243fdf4706b3f48f1d95db1a4f5529b4d41`.
For every row, dense top-3 is insufficient (`strict_replay_answer=unknown`),
while the replayed full artifact pool is sufficient and reproduces the expected
answer. The strict auditor independently reconstructs the episode bundle,
artifact classifications, source relation edges and IDs, task proof, view,
counterfactual, ordering, windows, remove-one results, and tokenizer metrics.

The first strict run correctly rejected 9/9 rows because the generator marked
same-repository causal edges authentic without requiring the child record's
signed exporter link. A fail-first regression reproduced this with a valid
child binding that omitted the parent link. The shared generator relation
builder now uses the same fail-closed attested-binding predicate as strict
replay; the auditor was not changed. After regeneration, each row's
`context_source_relation_count`, relation edge list, provenance labels, and
relation IDs match replay exactly.

Final receipts:

- candidate rows: 9; row-set SHA-256
  `05e1550a2b66e6328b574040719e64ba31f9eec45f9de613ffbaf18836765743`
- train JSONL SHA-256
  `3c4c817cc8bfd7a815ea95cccd0ecb739e1d3b5bf1f502291284568ced6790cc`
- dense ranking SHA-256
  `a963725f4ced29036d459d306e8af31f4fcc6c1a0ca178d450d7339fe9d88af1`
- strict audit SHA-256
  `c23a1c786ba51e940590b8f69c92ba3c38dd33564ecabc13d08181ea4b5f4c47`
- strict audit rows/accepted/rejected: 9/9/0

The candidate rows are signed by the local-probe candidate role, rankings by
the local-probe ranker role, audits by the local-probe auditor role, and source
bundle by the local-probe source role. This is diagnostic trust, not a
production attestation.

Artifacts:

- config: `configs/p14_codeforge_wasmtime_patch_review_test_v2.yaml`
- episode bundle: `configs/p14_codeforge_wasmtime_patch_review_test_v2_bundle.json`
- candidate: `data/releases/p14-codeforge-wasmtime-patch-review-test-v2-candidate/`
- rankings and audits: `reports/p14_codeforge_wasmtime_patch_review_test_v2/audit/`

Generation, ranking, and audit used the project `.venv` through the local-probe
trust wrapper. The exact payload commands were:

```text
.venv/bin/python scripts/generate.py \
  --config configs/p14_codeforge_wasmtime_patch_review_test_v2.yaml \
  --seed-start 130 \
  --out-dir data/releases/p14-codeforge-wasmtime-patch-review-test-v2-candidate \
  --workers 1

.venv/bin/python scripts/rank_candidates_dense.py \
  --candidates data/releases/p14-codeforge-wasmtime-patch-review-test-v2-candidate/train.jsonl \
  --output reports/p14_codeforge_wasmtime_patch_review_test_v2/audit/rankings.jsonl \
  --model-id sentence-transformers/all-MiniLM-L6-v2 \
  --revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41 \
  --batch-size 32

.venv/bin/python scripts/promote_candidates.py audit \
  --candidates data/releases/p14-codeforge-wasmtime-patch-review-test-v2-candidate/train.jsonl \
  --rankings reports/p14_codeforge_wasmtime_patch_review_test_v2/audit/rankings.jsonl \
  --output reports/p14_codeforge_wasmtime_patch_review_test_v2/audit/audits.jsonl \
  --accepted-candidates reports/p14_codeforge_wasmtime_patch_review_test_v2/audit/accepted.jsonl \
  --rejects reports/p14_codeforge_wasmtime_patch_review_test_v2/audit/rejects.jsonl \
  --top-k 3 --release-profile p7-github-source-slice-1-v1 \
  --episode-bundle configs/p14_codeforge_wasmtime_patch_review_test_v2_bundle.json \
  --workers 1
```

## Formal verification

Project-root `.venv` checks:

```text
uv run pytest -q tests/test_codeforge_patch_review_test_ancestry.py \
  tests/test_codeforge_real_workflow.py::test_release_cycles_create_band_specific_executable_proofs \
  tests/test_codeforge_real_workflow.py::test_exactly_two_release_cycles_create_16k_and_32k_programs
..... 5 passed in 3.06s

uv run ruff check <seven changed implementation/test files>
All checks passed!

uv run ruff format --check <seven changed implementation/test files>
7 files already formatted
```

No Git commit was created.
