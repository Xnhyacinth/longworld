# P58 inventory audit — 2026-09-08

This is a physical local-product inventory, not publication or a new signed union. Data root: /workspace/wynckeliao/longworld/data/releases.

| Scope | Products | Train rows / recorded exact tokens | Eval rows / recorded exact tokens | World IDs | B5 examples / estimated tokens |
|---|---:|---:|---:|---:|---:|
| canonical_baseline | 7 | 153 / 8002320 | 18 / 682032 | 17 | 92 / 5757426 |
| p57_increment | 13 | 60 / 4289481 | 0 / 0 | 13 | 60 / 4313887 |
| physical_product_union | 20 | 213 / 12291801 | 18 / 682032 | 30 | 152 / 10071313 |

All gate source hashes and export manifest output hashes verified: True. Signatures were not revalidated and tokenizer counts were summed from rows, not retokenized. Production-eligible products: zero. P15 superseded v3–v6 excluded; v7 retained.

| P57 product | Train rows | Recorded exact tokens | B5 estimated tokens |
|---|---:|---:|---:|
| p57-finance-amazon-128k-probe-1-v1-promoted-v1 | 3 | 392296 | 395617 |
| p57-finance-meta-128k-probe-1-v1-promoted-v1 | 3 | 391305 | 393780 |
| p57-finance-micron-asset-trajectory-probe-1-v1-promoted-v1 | 12 | 733444 | 742178 |
| p57-finance-micron-dual-partition-probe-1-v1-promoted-v1 | 3 | 98123 | 99206 |
| p57-finance-nvidia-market-mix-crossover-probe-1-v1-promoted-v1 | 3 | 195631 | 197036 |
| p57-finance-nvidia-market-segment-probe-1-v1-promoted-v1 | 12 | 728085 | 734226 |
| p57-ietf-acme-issuance-32k-probe-1-v1-promoted-v1 | 3 | 98175 | 98339 |
| p57-ietf-dnssec-64k-probe-1-v1-promoted-v1 | 3 | 196494 | 196643 |
| p57-ietf-http-semantics-succession-probe-1-v1-promoted-v1 | 3 | 386493 | 386711 |
| p57-ietf-http2-succession-probe-1-v1-promoted-v1 | 3 | 195344 | 195480 |
| p57-ietf-pkix-path-succession-probe-1-v1-promoted-v1 | 3 | 195390 | 195555 |
| p57-ietf-ssh-architecture-32k-probe-1-v1-promoted-v1 | 3 | 97050 | 97169 |
| p57-ietf-tls13-handshake-succession-probe-1-v1-promoted-v1 | 6 | 581651 | 581947 |

ACME 32K and DNSSEC 64K each have 3 audit rows; full-pool strict replay sufficiency and dense top-k insufficiency are true on all 3. Their current gates pass and production_eligible=false. Evidence: data/releases/<product>/release_gate_receipt.json, train_audits.jsonl, llamafactory/B5.meta.json. `.hl/progress.md` records the fifth-gold DNSSEC successor and source-unit ACME packing repair. Old failed candidates are excluded, not retried. `configs/p57_task_pipeline_v1.json` already_promoted is workflow bookkeeping, not production/HF eligibility.

CURRENT_RELEASE.md still records the seven-product 2026-09-06 baseline. No CURRENT_RELEASE/HF mutation made. Independent local-probe trust roots do not constitute one signed release. B5 estimates are a different bound set and cannot be added to product exact context tokens.

Identifier diversity in physical union: {"world_ids": 30, "domains": 7, "semantic_task_ids": 35, "answer_program_ids": 36, "proof_ids": 63}. These counts are not independence certification: full/CF/ordered and length variants remain correlated; missing identifiers excluded. Cross-product near-duplicate/split leakage checks were not rerun.

Raw dependency_class labels: {"deep_dependency": 201, "long_range_retrieval": 30}. Labels may retain long_range_retrieval despite passed strict replay flags; use recorded audits rather than equating labels with actual eligibility. This audit does not certify an exact strict/integration/retrieval causal taxonomy across legacy rows.


Current semantic caveat: parent independently reproduced question-codebook factual-answer recovery for ACME/SSH/DNSSEC and relation-deletion answer invariance for ACME/SSH. The numbers above describe frozen content-gate receipts only; shortcut-audited training readiness is NOT certified. Existing strict gate pass is insufficient to establish causal long dependency. See the parent’s separate diagnostic report for reproduction.
