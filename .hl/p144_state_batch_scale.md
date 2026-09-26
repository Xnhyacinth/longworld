# P144 parameterized state-world batch scale

`scripts/p144_state_batch_scale.py` schedules immutable P133 campaigns over
disjoint seed windows. The first production configuration runs two batches in
parallel; each campaign uses P133's four worker processes and existing
partial-reversal/authorization-hold gold, event-deletion, action-flip, exact
token and mask gates. Each batch is a separate reader candidate shard. The
wrapper checks seed, world ID, base-record context hash and reader task ID
uniqueness **across** batches, and it pins the prior P133 v4 campaign to
prevent source reuse. It can resume missing shards without overwriting any
frozen bytes; `--verify-only` reruns both producers and compares exact files.

| Stage | Batch 000 | Batch 001 | Combined |
| --- | ---: | ---: | ---: |
| Gross worlds | 128 | 128 | 256 |
| Admitted complete worlds | 122 | 118 | 240 |
| Reader QA candidates | 488 | 472 | 960 |
| Separate one-step policy candidates | 488 | 472 | 960 |
| Full-chat tokens, both contracts | 57,150,002 | 53,959,006 | 111,109,008 |
| Assistant-supervised tokens, both contracts | 137,662 | 127,414 | 265,076 |

The 16 incomplete worlds remain frozen with attempt/rejection receipts and
are excluded as whole worlds. The two native manifests record 0 prior-source
overlaps, selected witness-to-query distances of 16,613–90,633 final-chat
tokens, and all accepted masks. Physical lengths across both contracts are
185 below 32K, 903 at 32–64K and 832 at 64–128K; **none reach 128K**.
These are new independent controlled worlds within two existing mechanisms
and one record schema, not new real domains or multi-turn agent trajectories.

The unified P145 reader bank includes all 960 QA candidates from these two
shards. The policy candidates retain their separate contract. All artifacts
remain `train_ready=false`; no model training or gain is claimed.

```bash
cat data/candidates/p144_state_batch_scale_v1/manifest.json
cat data/candidates/p144_state_batch_scale_v1/batch_000/campaign/manifest.json
cat data/candidates/p144_state_batch_scale_v1/batch_001/campaign/manifest.json
less -R data/candidates/p144_state_batch_scale_v1/batch_000/campaign/attempt_ledger.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/p144_state_batch_scale.py --config configs/p144_state_batch_scale_v1.json --output data/candidates/p144_state_batch_scale_v1 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p144_state_batch_scale.py
UV_LINK_MODE=copy uv run --offline ruff check scripts/p144_state_batch_scale.py tests/test_p144_state_batch_scale.py
```
