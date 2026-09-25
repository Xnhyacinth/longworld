# P103 Wiki table-width capacity audit

The frozen P97 reader text does **not** support a safe, generic width-repair
rule. No P103 reader tasks were admitted, and no shared parser, P100/P102
artifact, or frozen source was changed.

## Pinned census

Run `python scripts/p103_wiki_width_audit.py` from the repository root. The
script verifies the input SHA-256 values and each affected snapshot's pinned
SHA-256 before inspecting the reader-visible text.

| Input | SHA-256 |
| --- | --- |
| `data/capability_records/p97_wiki_gated_delta_v1/source_pool.json` | `094353d647ae888821ede5683fd912d125f336018317ffc9364b40b7cd840268` |
| `data/candidates/p100_wiki_categorical_scan_v3/page_audit.jsonl` | `8e3e008858a122d4a722c96d132f24d21bf9eeef5897417e4053bf09de379846` |

The page audit has 186 `row_width_mismatch` rejection records. These reduce to
139 distinct `(doc_id, heading, header)` keys in 30 pages and 24 source
groups. For the first occurrence of each key, the first mismatched line has
fewer delimited fields in 83 cases and more in 56; 109 of 139 fail before one
full-width data row. Only seven have at least eight preceding full-width rows.
This is a lexical census, not an estimate of 139 recoverable tables: repeated
headers, prose, and continuation lines can also be the first mismatched line.

The unique-key rejection distribution spans 12 domains: environment 54,
civic 24, transport 15, heritage 9, healthcare 8, maritime 8, sports 6,
arts 5, education 5, energy 3, finance 1, media 1. These are *rejected
table-header occurrences*, not independent tasks or source worlds.

## Why rendered-text repair is unsound

The frozen snapshot's `documents` hold `text`, sections and revision URLs but
not the source Wikitext/HTML table grid. `render_wikitext()` in
`longworld/synthesis/wiki_adapter.py` joins cleaned cells with ` | `;
`_split_row_cells()` drops empty cells and does not preserve rowspan/colspan
provenance. P100's `parse_tables()` then correctly rejects the whole table on
a width mismatch. Padding, truncating or carrying a prior cell would fabricate
a column alignment that the final reader cannot verify.

Concrete pinned reader examples:

* **Public housing, Hong Kong**, revision `oldid=1361851848`, snapshot SHA
  `b5a28e674f1ba6389b08a314d4f324251b9f992b2981621f5e5c0da3c8cdf1e6`.
  The Eastern District header is `Name | Type | Inaug. | No Blocks | No Units |
  Notes`, while rows begin `Fung Wah Estate | 峰華邨 | TPS | 1991 | 2 | 463`.
  The second visible field is a Chinese name, not `Type`; even full-width rows
  are already semantically shifted. A later row adds a seventh notes field.
* **US credit unions**, revision `oldid=1372923908`, snapshot SHA
  `1ac613ebae6329b47b89012ae89df7b46964e95a161766d03aef4722cf14e2ce`.
  The header has seven fields including `Name`, but a visible row is
  `Swedesboro, New Jersey | FCU | 1166 | 1936 | Active`; the credit-union name
  is absent from that row. Appending blanks cannot restore the subject.
* **Russia volcanoes**, revision `oldid=1339052300`, snapshot SHA
  `1799746a0b4ef12be9de2c24d50253e9b7fc3edeefc3ca30f3f10cdcc587e104`.
  The `Kamchatka` table has 88 full-width rows before a row whose first cell
  carries an `ill|...` template-like string and whose width drops from five
  to four. There is no reader-visible origin grid from which to infer which
  column is absent.
* **Ottawa–Gatineau cinemas**, revision `oldid=1366447699`, snapshot SHA
  `6d4cbd699a35ff0add61d170ecfcde6cb9f3621221361ee673c5f157dc67744e`.
  Two consecutive header-looking lines have six and nine fields, followed by
  eight-field rows. Treating either first line as the sole schema is unsafe.

## Recoverable route

Acquire the **exact pinned revision** Wikitext or HTML into a new, versioned
source snapshot alongside the unchanged P97 text and revision URL. Parse an
explicit cell grid with row/column spans, empty-cell positions, header scope,
cell coordinates and original text offsets. Render a new reader view whose
visible row/column semantics match that grid; keep old reader bytes and P102
receipts immutable. Admit only tables for which every needed row and header
can be replayed from that new reader view, including all negative candidates
for complete-set tasks. Reject ambiguous nested tables/templates or missing
subjects. Then run the existing source-split, duplicate-support, text
intervention, exact-token and assistant-mask checks on a new shard. No such
raw-revision acquisition or reader admission was performed in P103.
