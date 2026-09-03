# P24 IETF OAuth exact-length candidate generation (2026-09-03)

## Result

Two authentic parent candidates and six candidate-local projections were built.
All six projections pass their unchanged exact token bands, and the independently
signed dense ranker produced one ranking per projection. The dense audit stopped
at its first semantic gate failure, before row 1 was emitted:

```text
PromotionError: task upstream proof replay failed: task proof complexity is insufficient
```

The first audited row is the 128K counterfactual view. Its materialized answer
has all six fields `UNKNOWN`, so replay reports `event_count=6` but
`strict_support_event_count=0`; the unchanged task-proof complexity contract
requires at least two strict support events. No audit manifest or audit row was
written. Nothing was selected, promoted, counted, or marked train-ready.

## Authentic source and natural packing

- P20 fetch inventory SHA-256 remains
  `bb428790b2d0dd8c53d64244cf8bd770ab9ddaf9081c0518bf7696fab2f1d0d8`.
- P24 pins the hash-bearing manifest export time to
  `2026-09-03T18:00:00Z`. The resulting manifest/task hashes are
  `a7e5528d3220c2aa8663c024c6206ed019adfe7ef90ed3f62fd55a1b4bdc1427`
  and `a4797942d0579dea1e911f2e11edbaf5dd8ca24772731011c6b0cecee1a7a8ea`.
- These differ from the P20 report's `93c02b3e...` / `dfab2bf6...` because
  P20 did not record its hash-bearing manifest export timestamp. This is not a
  source-byte substitution: the signed fetch inventory hash is unchanged.
- Packing uses only exact source spans ending at existing blank-line or form-feed
  boundaries. RFC 9700 remains one complete record for byte-exact CF replay; the
  near-duplicate published draft contributes only its identity paragraph.
- No filler, padding, cloned artifact, invented standard text, or gate retuning
  was used.

| Parent bucket | Prompt tokens | Source bytes | Artifacts | Essential |
|---|---:|---:|---:|---:|
| 64K | 64,528 | 281,768 | 10 | 8 |
| 128K | 128,541 | 552,315 | 17 | 8 |

The pinned exact tokenizer is `Qwen/Qwen3.5-4B` at revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`, asset-manifest SHA-256
`bbcbdfe073f579453f3c891f989a43fbb15cc88952e9f8ae294f04f6ca2036cb`.

## Projection distribution

| Bucket | View | Exact prompt tokens | Artifacts | Real-source marginal |
|---|---|---:|---:|---:|
| 64K | full | 64,528 | 10 | 0.9989926853 |
| 64K | cf | 64,509 | 10 | 0.5308871630 |
| 64K | ordered | 64,528 | 10 | 0.9989926853 |
| 128K | full | 128,541 | 17 | 0.9994943248 |
| 128K | cf | 128,522 | 17 | 0.7645383670 |
| 128K | ordered | 128,541 | 17 | 0.9994943248 |

Projection manifest: 2 parents, 6 projections, views `full`, `cf`, and
`ordered_artifact_view`; `train_ready=false`, `production_eligible=false`, and
`dense_audit_complete=false`.

## Public-path RED/GREEN repairs

The repo's `data` directory is a symlink. The secure sidecar loader correctly
rejects unresolved symlink components, but the public projection/audit entries
were passing their caller-relative base paths directly to it.

1. Projection RED: the real public CLI failed with
   `cannot read task replay sidecar: TASK_REPLAY_SIDECAR.json`. A focused public
   symlink-path test reproduced the failure. The only implementation change was
   resolving `sidecar_path` at the `project()` entry. GREEN: `1 passed, 4
   deselected`.
2. Audit RED: the real audit call failed with
   `cannot read task replay sidecar: TASK_REPLAY_SIDECAR_V3.json`. The same public
   regression then exercised relative symlinked `output_dir`. The only
   implementation change was resolving `output_dir` at the
   `audit_projections()` entry. GREEN: `1 passed, 4 deselected`.

The secure loader itself was not weakened or changed.

## Candidate-local hashes

- generation config:
  `70e96f612f237ee312205b4824af04af7c11baea9769699accf699f04e9124ca`
- parent candidates:
  `0c970b02205993dcb513e134318ae9fc5be0527a9a42f01c917003966d603dd0`
- v1 task sidecar:
  `f109d2536f2eb641125534642396eb935b8230e7838a4ff5f5d51ce2d44484f7`
- projection candidates:
  `eca80ae1368166aa38523e1ecae26a44d8b43d15d822c2b2130e5cb4b9a20d5e`
- v3 task sidecar:
  `f77811290ea4c1d6f551f597a239507c5969cce292e42bcc21520d24c508d301`
- dense rankings:
  `ca7538653cbb10e04e0cf8758e1a8225c2afdd68e24ad147fcea149820a42c2d`

Candidate-local generated files are under
`data/candidates/p24_ietf_oauth_cross_spec_v1/` and remain ignored by Git.

## Exact command status

- Generation: `uv run python reports/p24_ietf_oauth_cross_spec_generate.py
  --config configs/p24_ietf_oauth_cross_spec_generation_v1.json` — PASS, 2
  parents.
- Projection: `uv run python scripts/project_task_candidate_views.py ...` —
  PASS, 6 projections.
- Dense ranking: `uv run python scripts/rank_candidates_dense.py ...` — PASS,
  6 rankings.
- Dense audit: one public `audit_projections(...)` call after the path repair —
  BLOCKED on 128K CF proof complexity; 0 audit rows, no audit manifest.
