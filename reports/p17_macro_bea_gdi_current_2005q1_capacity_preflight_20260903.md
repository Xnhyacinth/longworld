# P17 BEA GDI-current 2005Q1 capacity preflight

This preflight was run before P17 generation against the immutable BEA workbook
workflow and the locally pinned `Qwen/Qwen3.5-4B` tokenizer revision
`a7b0d22b993d71000cf2eadfb37222a67cee521e`.

## Source and topology

- source observations: 3,959
- source relations: 3,567
- source trajectories: 384
- structurally eligible four-band target entities: 202
- selected entity: `BEA_GDI_CURRENT_DOLLARS` / `2005Q1`
- selected target vintages: 15
- selected executable prefixes: 5 (`10`, `12`, `13`, `14`, `15` vintages)
- same-series authentic pool: 1,770 artifacts across 95 trajectories
- same-series serialized distinct-record capacity: 731,123 pinned-tokenizer tokens

The target is distinct from P16's `BEA_REAL_GDP_PERCENT_CHANGE` / `2005Q4`:
it asks for a current-dollar GDI revision path rather than a real-GDP percentage
change revision path. It retains the existing executable
`macro.as_of_revision_path.v2` program and its changed/unchanged transition proof.

## Exact-band dry run

The production builder was called read-only with all four requested bands. It
returned four candidates without writing generated data:

| bucket | context tokens | document tokens | essential span tokens | essential artifacts | distinct documents | background trajectories |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 16k | 16,103 | 16,000 | 8,426 | 19 | 40 | 11 |
| 32k | 32,103 | 32,000 | 22,732 | 23 | 78 | 11 |
| 64k | 64,147 | 64,044 | 43,795 | 25 | 154 | 11 |
| 128k | 128,103 | 128,000 | 100,954 | 29 | 306 | 13 |

The essential prefix lengths grow `10 -> 12 -> 13 -> 15`; all bands remain in
their exact pinned-tokenizer intervals. No padding, relabeling, truncation, or
gate relaxation was used.

## Rejected alternative

`BEA_GDI_CURRENT_DOLLARS` / `2004Q1` had 10 structurally eligible prefixes but
failed the executable packer at 16k with
`cannot fill exact 16k from distinct macro records: retained document_context tokens 2875`.
It was rejected rather than rescued with padding or weaker constraints.
