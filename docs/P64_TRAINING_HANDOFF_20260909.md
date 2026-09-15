# P64 local training-input handoff

The P64 handoff contains separate Finance and CodeForge primary SFT datasets with explicit train/eval splits. Finance splits by issuer CIK; CodeForge splits by canonical repository URL. Each primary semantic task and sample appears once. Combined dataset selection references the same files and does not add views or duplicated training rows.

The preparation command freshly replays the two registered source validators, preserves exact user/assistant messages, and checks complete chat and answer tokenization against the pinned Qwen tokenizer with a 262,144-token cutoff. It rejects truncation. Source-role credentials are isolated from report signing; the final CPU tokenizer workers run without attestation environment variables, with serial tokenizer construction and bounded worker queues.

The output files are `finance_train.jsonl`, `finance_eval.jsonl`, `codeforge_train.jsonl`, and `codeforge_eval.jsonl`. `sample_index.jsonl` preserves canonical task/sample identities, domain, source group, split and full-message token counts. `dataset_info.json` maps the four domain/split aliases. The signed `P64_TRAINING_MANIFEST.json` binds source files, source validation, producer code, handoff code, tokenizer assets, recipe, metadata and output bytes.

Default dataset selection is separate: `p64_finance_train` or `p64_codeforge_train`. The explicit combined selection is `p64_finance_train,p64_codeforge_train`. Evaluation uses the corresponding separate eval aliases. CodeForge's short-context output and overlength rejects remain outside this primary handoff. Finance training preserves the configured `margin_extremes_cashflow_difference` structure holdout.

`configs/llamafactory/TASKBANK_P64.yaml` is an input/template recipe. The existing `scripts/train_llamafactory.sh` does not validate the `TASKBANK_P64` condition and is not a supported launch path for this handoff. Actual framework preprocessing, framework integration and GPU execution remain unverified. Public release eligibility and strict long-dependency certification remain false.

Supported delivery is validation followed by a private, read-only snapshot:

```bash
uv run python scripts/run_with_local_probe_trust.py \
  --trust-file <isolated-report-trust-file> --role report -- \
  .venv/bin/python scripts/prepare_p64_training.py validate \
  --manifest data/sft/p64_primary_training_v2/P64_TRAINING_MANIFEST.json \
  --snapshot-root <private-snapshot-root>
```

Validation checks the signed report identity, current input/code/tokenizer bindings, canonical identities, exact source-to-output projection and dataset mapping. Snapshot creation copies verified bytes into a content-addressed directory, with files mode `0400` and directories mode `0500`. No training process is launched.

## Verified result

Signed preparation, validation and the private read-only snapshot passed. All nine registered domain validation jobs returned PASS. The 14 focused tests and Ruff passed.

| Domain | Train | Eval | Full-message tokens |
|---|---:|---:|---:|
| Finance | 1,296 | 386 | 228,008,943 |
| CodeForge | 657 | 147 | 102,343,169 |
| Combined primary inventory | 1,953 | 533 | 330,352,112 |

There are 2,486 distinct primary tasks/samples. Maximum complete-chat length is 261,954 tokens. CodeForge short samples (13) and one complete-chat overflow remain excluded. Full-message token totals include the chat template and answer; they are not exact source-token-band certificates.

Manifest: `/workspace/wynckeliao/longworld/data/sft/p64_primary_training_v2/P64_TRAINING_MANIFEST.json`.

Read-only snapshot: `/workspace/wynckeliao/.longworld-scaleout-20260906-private/p64-training-snapshots/bb92e3b1c37f482d26d8697cec481dde75dae274a0b7eacca8bcaff20a9bd3bc`. The snapshot contains the seven payload files; its signed manifest remains at the separate manifest path above.

The initial unsigned attempt failed at output-path validation because of the repository data mount. A regression test and a mounted-path fix resolved the issue. All seven payload files are byte-identical across that fix; the unsigned diagnostic directory was removed after the validated snapshot completed. Failure evidence remains in `reports/p64_training_prepare_failure_20260909.json`. Full handoff metrics and bindings are in `reports/p64_training_handoff_20260909.json`.
