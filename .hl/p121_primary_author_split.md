# P121 Gutenberg catalog identity plan

**Catalog-only research plan; no downloads, book bodies, reader tasks, masks or
training data were produced.** P121 addresses P120's over-conservative
full-catalog coauthor closure while leaving every P113/P114/P120 source and
task artifact frozen. The pinned official catalog is
`data/sources/p113_book_catalog_v1/pg_catalog.csv.gz` (SHA-256
`9965df5b1fdd56f19c876054c891c09b2a98d65ab910bee6e91fa734e645f31d`).
The current plan is `data/sources/p121_book_primary_plan_v3/plan.json`; its
reproducible comparison is the adjacent `report.json`. V1 and V2 are
unpromoted local diagnostics. V2's independent review found three mismatches
between stored prior work keys and keys recomputed from the catalog, plus
organization and house-name labels that passed its singleton rule. V3
addresses both findings. Only V3 should be considered for a later source
acquisition decision.

## Why the old graph is too broad

The P120 planner unions *every* listed catalog person on every English text,
including illustrators, translators, editors and anthology contributors, even
when that book is never used as a source. Its largest transitive component
contains **30,706** catalog rows and **8,751** parsed author keys. Within
multi-person rows in that component, 7,393 include `[Illustrator]`, 2,685
`[Translator]`, 2,063 `[Editor]`, 433 `[Contributor]` and 114 `[Compiler]`;
categories overlap. If rows carrying bracketed roles are removed *only from
the graph edges*, the largest component has 812 rows. Removing those and
rows with more than two listed people leaves 251. The graph also conflates
different people with the same normalized name: eBook 59463 lists Robert
Herrick (1868–1938), while eBook 1211 lists Robert Herrick (1591–1674);
P120's `herrick|robert` key merges them. These are catalog graph diagnostics,
not claims that role-bearing rows are low-quality texts.

`Various` is not the main bridge: 3,660 English rows mention it, and the
P114/P120 parser yields no author key for 3,650 of them; none of the ten
nonempty parsed cases belongs to the giant component.

Concrete provenance paths from previously blocked **single-author** books
to a frozen prior source run through unselected works:

| Candidate book | Unselected catalog bridge | Prior source reached |
| --- | --- | --- |
| 50323, Roy Rockwood | 73763 lists Rockwood and Shute `[Illustrator]` | 77036, Shute, eval |
| 19136, Emma Leslie | 21797 Leslie–Rainey `[Illustrator]`; 18868 Henty–Rainey | 8745, Henty, train |
| 76046, Gordon Stables | 37253 Stables–Browne `[Illustrator]`; 19206 Henty–Browne | 8745, Henty, train |
| 59463, Robert Herrick (1868–1938) | 1211 Herrick (1591–1674)–Palgrave `[Editor]`; 19221 Palgrave–Pearse `[Illustrator]`; 20173 Griffith–Pearse | 73683, Griffith, train |

The first path also illustrates a separate ambiguity: `Roy Rockwood` is a
catalog author label, not proof of a unique real person. P121 never promotes
catalog key separation to real-human identity or textual independence.

## Versioned policy and fair comparison

The new candidate rule admits a work only when an English catalog row has
**exactly one parsed author, one author fragment, no bracketed role tag, and
a personal-name-shaped surname/given field**. Parentheses, braces,
ampersands, missing given names and organization words are rejected by
generic syntax. This rejects `Carter, Nicholas (House name)` (eBook 11989),
`Blackie & Son` (36411) and `Spinners' Club` (20343); it does not establish
that every passing name is a unique human.
It rejects any candidate whose normalized author key appears in *any*
P113 v6/P114 v2/P120 v1 prior source, plus prior ebook IDs, normalized work
keys and P114/P120 mirror attempts. **Both stored and recomputed catalog work
keys** from the prior sources are excluded. Their difference is recorded for
eBooks 11 (Carroll), 1342 (Austen) and 1661 (Doyle). It keeps one ebook variant per work,
requires all *qualifying* variants under that normalized work key to have the
same author key, assigns train/eval by that sole key, and caps planned works
per key. Other catalog variants and the actual body remain a later source
gate.
This means the same listed author cannot appear on both sides among admitted
P121 books, and no new admitted author key is listed in an older source.
Unselected multi-author/role-tagged books do not create split edges. Pinned
catalog and prior manifest hashes are checked before planning.

| Denominator on the frozen catalog | Unique works |
| --- | ---: |
| Historical P120 full-graph eligible, before P120 sources/attempts existed | 3,759 |
| Full-graph eligible with the same P113/P114/P120 sources and P114/P120 attempts used by P121 | 3,221 |
| P121 V3 strict personal-name-shaped eligible under those same exclusions | **6,734** |
| Present under both fair policies | 2,132 |
| Newly unlocked under P121 V3 | **4,602** |
| Lost under the stricter rule | 1,089 |

Of the 4,602 newly unlocked works, 4,598 were in P120's giant component.
The P121 V3 pool has 2,204 distinct normalized author keys. All 177 author keys
listed in the 147 prior source records have zero direct train/eval conflicts;
the new pool has **zero** overlap with them, zero author-key split conflicts,
and zero prior ebook-ID or stored/recomputed work-key overlap. The deterministic first 1,000
planned works use 741 author keys, 790 train/210 eval, and all 17 subject
classes found in the eligible pool. The first 180 planned positions cover
those 17 classes with 10–11 sources each; they are a plan, not download
successes or verified tasks.

The old 3,759 and new 6,734 figures have different prior-source sets; the
controlled comparison is **3,221 vs 6,734**. The 1,089 losses are
multi-person/role-bearing or personal-name-shape exclusions, not failed books.
Among otherwise topic- and source-eligible catalog rows, V3 records 1,995
parenthesis/ampersand/brace rejections, 102 missing surname/given shapes and
four organization-term rejections, separately from 6,570 multi-person or
role-tagged rows. These reason counts are rows, not unique works.
Any future acquisition
must check raw/body SHA, source text and chapter overlap, work variants,
rights/attribution and exact train/eval exposure before task compilation.
Pseudonyms and unlisted contributions remain unresolved by this catalog
rule. A book passing the plan is not train-ready or evidence of long-context
learning.

## Inspect and replay

```bash
cat data/sources/p121_book_primary_plan_v3/report.json
cat data/sources/p121_book_primary_plan_v3/plan.json
cat data/sources/p121_book_primary_plan_v3/prior_manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/p121_book_primary_author.py --config configs/p121_book_primary_author_v3.json --catalog data/sources/p113_book_catalog_v1/pg_catalog.csv.gz --output data/sources/p121_book_primary_plan_v3 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p121_book_primary_author.py
UV_LINK_MODE=copy uv run --offline ruff check scripts/p121_book_primary_author.py tests/test_p121_book_primary_author.py
```
