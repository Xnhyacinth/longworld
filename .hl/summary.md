# Summary — LongWorld v2 slice 2

- Best result: 12-world `data/v2_p28` — train **and** eval 16/32/64/128/256k (1968 rows each), N_eff 50.47, RFC 16k 0.43 / 256k 0.85, rfc9110 used, B5 export 4530 rows with 128k+256k. Recipe `configs/swift/B5_v2_long.yaml`. Mid freeze `v2_p27` kept.
- Honest N_eff from process ops and GroundedWorld proofs, not JOIN. 16–64k majority native workspace; 128/256k unique public sources. Deep is still a minority; 860/1640 remain 2-essential retrieval.
- Open: GPU train of B5_v2_long when free; 48/210 deferred; no Agent sandbox/WorldACC. External baselines already in `data/external/swift/`.
- Train recipe: Qwen3.5-4B full SFT, cutoff **262144** (native cap), **packing off** so B5 8/32/64k stay native. UltraChat-64K is ProLong short-SFT MDS, not a QA baseline.
- Invariant: do not kill foreign GPU jobs; do not delete data/p0.
