# P65 GovInfo authorization hold

The H.R. 4366 EAS/EAH comparison build is retained only as a diagnostic. Its
bound P49 source authorization explicitly prohibits `generate_candidates` and
`promote_or_count_inventory`, so the 28 generated rows (seven long and 21
short) are excluded from every P65 training handoff and inventory increment.

`scripts/materialize_p65_govinfo_taskbank.py` now fails closed unless the bound
source authorization explicitly allows candidate generation and does not
prohibit it. The earlier `reports/p65_pipeline_wave1/PIPELINE_RECEIPT.json` is
therefore superseded. The current `configs/p65_source_pipeline_wave1.json` and
`reports/p65_pipeline_wave1_v2/PIPELINE_RECEIPT.json` contain only the three
Finance disclosure jobs.

The diagnostic still establishes a bounded source/grammar result: one existing
bill chain yielded seven programs over one 59,765-token context, while H.R. 815
yielded no usable paired single-amount cases. It does not add a source world,
training row, strict dependency certificate, or release permission.
