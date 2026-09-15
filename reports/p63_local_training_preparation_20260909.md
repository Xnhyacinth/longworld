# P63 local training input preparation — 2026-09-09

The dedicated TASKBANK path prepared and signed 1,252 validated tasks: **1,067 train and 185 eval**. All six source-world validators returned PASS. The longest complete message sequence measured with the pinned tokenizer was **129,654 tokens**, below the recipe cutoff of 262,144. This is a measured maximum, not a claim that these examples contain 256k tokens.

Status is `local_training_eligible=true`, `production_eligible=false`, and `legacy_release_eligible=false`. The local preparation uses the existing probe report authority; it does not grant public redistribution or production eligibility. `framework_preprocessing_verified=false`: the actual LLaMA-Factory preprocessing, model training, and GPU execution have not been run. The launcher validation and readonly snapshot reuse were run successfully.

## Durable local artifacts

- Manifest: `/workspace/wynckeliao/.longworld-taskbank-training-private/p63_v2/TASKBANK_TRAINING_MANIFEST.json`
- Manifest file SHA256: `86acc0735836a4a9184803b106220a7c75669c421200d6347645ee21734ec640`
- Snapshot: `/workspace/wynckeliao/.longworld-taskbank-training-private/snapshots/f7b9f54b1f541eecbad7484fef771ef40188021c5f9726cb94b08179de6f9887`
- Completion receipt: `/workspace/wynckeliao/.longworld-taskbank-training-private/PREPARATION_CLOSEOUT.json`
- Report trust file: `/workspace/wynckeliao/.longworld-scaleout-20260906-private/amazon/local_probe_trust.json`

These artifacts reside outside Git. The snapshot root is mode 700, the content-addressed directory is mode 500, and its `train.jsonl`, `eval.jsonl`, and `dataset_info.json` files are mode 400. The trust file path identifies the local authority used by the wrapper; this report contains no key material. The manifest binds source files, configs, batch/world receipts, split groups, stage code, recipe, and output bytes. Changes to those bindings require fresh preparation.

## Reproduce validation without training

Run from the owning checkout. This exact command passed with exit code 0 and reused the snapshot above:

```bash
cd /workspace/wynckeliao/longworld-worlds
LONGWORLD_TASKBANK_MANIFEST=/workspace/wynckeliao/.longworld-taskbank-training-private/p63_v2/TASKBANK_TRAINING_MANIFEST.json \
LONGWORLD_TASKBANK_REPORT_TRUST=/workspace/wynckeliao/.longworld-scaleout-20260906-private/amazon/local_probe_trust.json \
LONGWORLD_TASKBANK_SNAPSHOT_ROOT=/workspace/wynckeliao/.longworld-taskbank-training-private/snapshots \
LLAMA_FACTORY_ROOT=/nonexistent \
bash scripts/train_llamafactory.sh TASKBANK --prepare-only
```

The returned JSON must have `ok=true`, counts `train=1067` and `eval=185`, and the snapshot path above. The intentionally nonexistent LLaMA-Factory path demonstrates that this preparation check exits before framework installation checks or model/GPU initialization. Preserve `--prepare-only` when reproducing this check.

## Verification and first-attempt failure

The focused training preparation and independent adversarial tests passed **9 tests**. Ruff and `bash -n scripts/train_llamafactory.sh` passed. The tests cover source/output tampering, symlink rejection, legacy-row exclusion, credential removal during tokenization, unsupported recipe subsetting, and serialized tokenizer construction. Independent CLI checks rejected data-changing overrides including `max_samples`, `data_shared_file`, `interleave_probs`, `dataset`, and `tokenized_path`.

The first attempt, private directory `p63_v1`, passed all source validators but failed during concurrent first-time Transformers tokenizer initialization with `ImportError: cannot import name 'AutoTokenizer' from 'transformers'`. It produced no signed manifest or snapshot. A regression test reproduced overlapping constructors; construction is now serialized while workers retain independent tokenizers for counting. The complete retry used `p63_v2` and succeeded.

Failure diagnostics remain at `/workspace/wynckeliao/.longworld-taskbank-training-private/p63_v1/PREPARATION_FAILURE.txt`, alongside the six source validation logs and `FAILED_PARTIAL_OUTPUT_RECEIPT.json`. The two incomplete unsigned JSONL files were removed after recording their sizes and hashes. No `p63_v1` rows are counted or usable as training input.

The isolated five-issuer training set and AMD eval split must remain separate from legacy training exports, which already contain AMD exposure. The preparation does not certify a union with legacy data or a source-unseen evaluation for such a union.
