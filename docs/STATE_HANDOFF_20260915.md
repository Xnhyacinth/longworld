# LongWorld state handoff — 2026-09-15

This handoff separates the reviewable source tree from large operational
artifacts. It preserves scientific status labels: an uploaded artifact is not
thereby strict-long-dependency verified, production eligible, or model-utility
validated.

## Source and environment

- GitHub repository: private `Xnhyacinth/longworld`.
- Integration branch: `integrate-worlds-sanitized-20260915`, based on
  `origin/main` without the experimental branch's probe-key history.
- Python 3.12.3; uv 0.12.6; Git 2.43.0.
- Compute host inventory: 8 NVIDIA H200 GPUs, driver 580.126.09, 143,771 MiB
  per GPU. No GPU job was started for this handoff.
- `pyproject.toml` SHA-256:
  `222a003027a49cdb02965c30940d05d40e0890b75ec9c706331124662966c730`.
- `uv.lock` SHA-256:
  `b8518611546a53105169cb3d8d508cfab352b4ac0f29088f7be01b4a6f3bfc7a`.

Restore the owning environment from the repository root with `uv sync
--frozen`. Workspace-local temporary/cache roots are
`/workspace/wynckeliao/.config/iquest/tmp`, `/workspace/wynckeliao/.hf`, and
`/workspace/wynckeliao/.cache/uv`; they are operational caches rather than
delivery artifacts.

## Git contents

The integration includes P57--P66 compact reports and receipts, P64/P65/P66
taskbank materializers and handoff paths, the multi-domain source runner,
OpenAlex capability curriculum, simulated world/rule families, LLaMA-Factory
P64 preparation, tests, and `.hl` plans/status records. Generated large JSONL,
training checkpoints, and evaluation payloads remain outside Git.

Source pipeline catalogs bind project-relative names to SHA-256 values. Local
trust file paths remain configuration inputs, while trust key bytes stay
outside Git and Hugging Face. A new machine must provision equivalent trust
material separately; the public `probe_id`, role, and `key_id` bindings remain
in the catalog.

## Hugging Face operational snapshot

- Private dataset: `Xnhyacinth/LongWorld-Training-State`.
- Frozen manifest revision: Hub tag `state-2026-09-15`.
- Intended local view: 2,297 files / 214,172,921,054 bytes.
- Uploaded payload: 2,157 files / 1,173,229,609 bytes, plus the dataset card,
  Hub attributes, and uploaded snapshot manifest.
- Storage-quota blocked: 140 files / 212,999,691,445 bytes.
- Local resumable view:
  `/workspace/wynckeliao/.longworld-hf-staging/LongWorld-Training-State`.
- Complete inventory: `SNAPSHOT_MANIFEST.json` in that Hub repository. Each
  intended file has its relative path, byte size, SHA-256, and upload state.

The local view contains four full experiment families:

| Family | Complete checkpoints | Purpose |
| --- | --- | --- |
| `swift_ext_acc_base` | 600, 680 | Base ACC 128K |
| `swift_ext_longtrace_base` | 200, 680 | Base LongTrace 128K |
| `swift_ext_acc` | 600, 680 | Instruct ACC 128K |
| `swift_ext_longtrace` | 100, 200 | Instruct LongTrace partial run |

Each checkpoint includes both model shards, optimizer/scheduler state, trainer
state, RNG state, tokenizer/config files, and arguments. Existing private model
repositories already preserve step-680 inference weights for Base ACC, Base
LongTrace, and Instruct ACC. The new state repository preserves all accepted
small metadata and logs; complete resumable checkpoints await additional
private LFS capacity or another authorized object store.

Evaluation delivery includes MRCR, GraphWalks, aligned downstream results, run
protocols, client/server logs, and P4 held-out inputs. Evaluation caches and
serve views were excluded because they are reproducible from the saved inputs,
configs, and model references.

## Deliberate exclusions

- `.venv`, `__pycache__`, package caches, downloaded model caches.
- `data/sft/tokenized` and evaluation cache/serve directories.
- Git object databases and disposable temporary files.
- `.local-probe-env`, HMAC key bytes, API tokens, and credentials.

The untracked/modified experimental report set from the worlds worktree was
included in the HF snapshot view before upload. Its accepted files are on Hub;
quota-blocked large report JSONL files remain listed with hashes in the
manifest and retained in the resumable local view.
