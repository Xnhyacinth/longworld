# P14 Company Berkshire task closeout

Date: 2026-09-02

Outcome: **0/9, no quota increment, task implementation removed**. The signed
Berkshire official-PDF source foundation is valid and reusable, but the tested
Pilot/cyber/governance disclosure-chain program did not have enough
answer-bearing natural span for the unchanged 16K/32K/64K distance gates.
No threshold was relaxed and no unrelated PDF text was promoted to essential
evidence.

## Exact candidate result

The pinned Qwen tokenizer and seed 14011 produced nine rejects across
`full`, `cf`, and `ordered_artifact_view`:

| Bucket | Views | Exact evidence span | Required | Result |
|---|---:|---:|---:|---|
| 16K | 3 | 7,837 | 8,000 | reject |
| 32K | 3 | 7,904 | 16,000 | reject |
| 64K | 3 | 8,044 | 22,000 | reject |

Evidence is retained in
`reports/p14_company_berkshire_disclosure_evolution_v1/candidate/reject_log.jsonl`
and `quality_report.json`. The quality report has 0 rows, 9 rejects, and only
`distance_shortfall`.

## Read-only span diagnosis

The source-workflow checkpoint filter retained all eligible artifacts:
116/116 at 16K, 117/117 at 32K, and 118/118 at 64K. The loader therefore did
not discard a report or section.

- 16K essentials: 2023 Item 1 153-1470; 2023 Item 1C 1537-2162; 2024 Item 1
  3040-4279; 2024 Item 1C 5066-5692; relation 6691-6758; answer 7935-7995.
- 32K essentials: 2022 Item 1 159-1336; 2022 Item 1A 2122-3121; 2023 Item 1
  4000-5317; 2023 Item 1C 5317-5942; relation 5942-6008; 2024 Item 1
  6008-7247; 2024 Item 1C 7247-7874; relation 7874-7940; staged answers
  7940-8001 and 8001-8068.
- 64K essentials: 2021 Item 1 975-1762; 2021 Item 1A 1762-2640; 2022 Item 1
  2640-3817; 2022 Item 1A 3817-4816; relation 4816-4883; 2023 Item 1
  4883-6200; 2023 Item 1C 6200-6825; relation 6825-6891; 2024 Item 1
  6891-8130; 2024 Item 1C 8130-8757; relation 8757-8823; staged answers
  8823-8884, 8884-8951, and 8951-9024.

The falsified alternative was source-filter loss. The supported root cause is
that the executable answer used only the chunks containing the selected Pilot,
cyber, and committee facts. Other authentic chunks were not required by the
answer program and could not truthfully become causal evidence merely to add
distance. A whole-section classification program might be a different task,
but no remove-one-essential semantics and precomputed 8K/16K/22K lower bounds
were established, so it was not implemented.

## Cleanup

All uncommitted Berkshire task-specific Company events, queries, simulation,
rendering, schema registration, engine/generation/promotion hooks, task test,
and task config were removed. The generic signed
`issuer_official_pdf` inventory auditor/adapter, entity-specific Berkshire
exporter, dependency lock, exporter tests, fetch request, and source acquisition
report remain. The failed experiment contributes 0 physical worlds and 0
Company quota worlds.
