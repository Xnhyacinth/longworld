# P66 IETF taskbank closeout

P66 re-audits seven authenticated P57 IETF succession worlds as a bounded local
SFT handoff. It consumes the already materialized ACME, DNSSEC, HTTP/2, HTTP
Semantics, PKIX, SSH and TLS 1.3 projections pinned in
`configs/p66_ietf_taskbank_v1.json`; it does not fetch or alter source records.
The source manifests authorize the underlying public IETF fetches. The current
user instruction authorizes this local derived synthesis, but no production or
release authorization is inferred.

The four-process run inspected 24 inherited rows: the full, counterfactual and
ordered views for seven families, with two length bands for TLS. Sixteen factual
or ordered rows were rejected because their full answer is exactly recoverable
from the question's public codebook. Two counterfactual rows were below the
32,768-token long-context floor. Three more counterfactual rows were rejected
because every positive answer-bearing quote fits inside a 16K token span.

Three counterfactual rows remain local long candidates:

| world | split | context tokens | full HF chat tokens | positive-evidence cover |
|---|---:|---:|---:|---:|
| DNSSEC succession | train | 65,492 | 65,830 | 32,400 |
| HTTP Semantics succession | train | 128,811 | 129,336 | 36,377 |
| HTTP/2 succession | eval | 65,092 | 65,464 | 48,083 |

Thus the exact numeric bands are 64K=2, 128K=1 and 256K=0; train=2 and eval=1.
The absence-dependent counterfactual branch is source-bound, but the current
probe only measures question-only exact match and the positive evidence cover.
Latest-document, complete-record compact and neural controls remain unmeasured,
so all rows retain `strict_long_dependency_verified=false`,
`training_release_eligible=false`, and `production_eligible=false`. They are
usable only as local training candidates or diagnostics.

Build and validation both used `--workers 4`. Native validation rebuilt the
tree in a temporary directory and matched every output hash in
`data/candidates/p66_ietf_taskbank_v1/BUILD_RECEIPT.json`. Ruff passed and the
three focused tests passed. No unrelated protocol families were concatenated
to manufacture a 256K row; the authenticated inventory has no honest 256K IETF
task after these filters.
