# CausalTwin / LongWorld pipeline state

Date: 2026-08-24

## P3 release path

The active release path is:

```text
allowlisted public workflow export
→ signed replay bundle
→ 20 candidate worlds
→ pinned dense ranking
→ independent strict audit
→ signed world-atomic 12-world selection (10 train / 2 eval)
→ independent promotion replay
→ exact signed train/eval report
→ immutable B1/B3/B5/B5w training transform
```

The immutable transform preserves factual/CF dossier twins atomically under
the equal-token cap, requires every view × length cell after truncation, and
places the system instruction inside the ShareGPT conversation consumed by the
trainer. The in-repo single-GPU trainer is unsigned diagnostic-only and cannot
produce a release manifest.

The dense audit receipt also binds the replayed per-view near-duplicate ratio.
World selection fails closed on a missing ratio and excludes the entire world
when any sibling exceeds the profile limit; it does not trust the candidate's
producer-authored ratio.

Every longer real release task must add visible event relations, required
support events, and proof depth. P3 has no source-pack filler. The local probe
now has 80 freshly exported episodes (2,232 records), a green 12-world release
receipt, 346 train-ready rows, 28 hybrid real-workflow rows and six exact 64K
rows. The 48/210 profiles remain blocked on production trust and broader source
coverage; this local HMAC probe is not their authorization.
Selection for 48 must bind the post-gate 12-world receipt; selection for 210
must bind the post-gate 48-world receipt. Each receipt binds the complete
report/train/eval hashes, gate revision and green metrics, and is verified with
its pinned original environment/key identity. Even with those predecessor gates, 48/210 are
not production-authorized until asymmetric/KMS signing, independently enforced
source policy, a production secret/PII scanner, and pinned training supply-chain
inputs replace the probe-only HMAC boundary.

The remainder of this file records the older p1/v2 diagnostic pipeline and is
not a P3 release runbook.

## Positioning

Causal core kept. Semantic shell and length KPI rewritten.

LongWorld is **not** a SearchArt/ACC substitute. Paper claim for this slice:
**CausalCore-v0** — verified causal long-context SFT at natural length.

Four products (do not mix quality functions):

| Product         | Status                                                         |
| --------------- | -------------------------------------------------------------- |
| CausalCore-v0   | p1.1 diagnostic freeze (`data/p0`); do not regen with pulse    |
| CausalCore v2   | mix frozen (`data/v2_p27` + `reports/causalcore_v2/FREEZE.md`) |
| WorldLong-SFT   | B5 export of v2 mix; 16k–256k caps; memory dropped             |
| NaturalLong-CPT | not started                                                    |
| WorldAgent-ACC  | not started (`ordered_artifact_view` ≠ ACC)                    |

p1.1 `data/p0` (64k/256k pulse-filled) is frozen: `reports/causalcore_v0/FREEZE.md`.

## Phase A shipped

- `n_pulses: 0`; renderers no longer append `unique_prose`
- pack: max-token cap; reject only `semantic_shortfall`, never `unique_shortfall`
- `trajectory` → `ordered_artifact_view`
- `canonical_topology` anonymizes entity IDs
- BM25 top-1 must not solve ≥2-doc proofs
- quality gate: boilerplate < 15%, pulse share < 10%; 64k not required

## Phase B/C lite (2026-08-19)

- Proof JOIN of two existing necessary proofs (SearchArt width, no new events).
- Content plans in `Artifact.slots` only.
- Same-schema extra worlds → `structural_hard_negative`; other domains / anchors → background.
- Records: `hop_count`, `searchart_width`, `oracle_long_gain` (LongFilter oracle proxy).

## Slice 2 shipped (2026-08-19)

JOIN capped at 3/world, unordered canonical hash, first-class compare_belief / delayed_effect / cross_stream.
Source pack expanded to 16 public texts. 32k is a length _outcome_.

```bash
/usr/bin/python -m pytest tests/ -q
python scripts/generate.py --config configs/v2_probe.yaml --out-dir data/v2_probe --workers 4
python scripts/quality_gate.py --data data/v2_probe
```

12-world: N_eff 36.2 (honest; slice-1 42.2 was JOIN-heavy), join_row_share 0.293, pulse 0, 32k 1416 rows.
Do not run 48/210 until more process ops lift N_eff without raising JOIN share.

Train (when GPUs are free; wrap hold):

```bash
bash scripts/setup_swift.sh
INSTALL_SWIFT=1 bash scripts/setup_swift.sh
GPUS=0,1,2,3,4,5,6,7 bash scripts/train_baselines_128k.sh
```
