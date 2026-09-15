# P58 NASA accident investigation and return-to-flight preflight

Date: 2026-09-08. **New domain/source acquired; current strict oracles rejected;
training inventory delta 0.** Three executable source-extraction diagnostics are
preserved as retrieval recipes, not admitted retrieval or strict training rows.

## Frozen official source chain

- CAIB Volume I (August 2003), NTRS `20030066167`:
  https://ntrs.nasa.gov/api/citations/20030066167/downloads/20030066167.pdf
- Return to Flight Task Group final report (July 2005), NTRS `20050201800`:
  https://ntrs.nasa.gov/api/citations/20050201800/downloads/20050201800.pdf
- Source-specific metadata is frozen from each NTRS `/api/citations/{id}` endpoint.
  Both return `distribution=PUBLIC`,
  `copyright.determinationType=GOV_PUBLIC_USE_PERMITTED`, and
  `containsThirdPartyMaterial=false`. These are the recorded repository rights
  determinations; no new production approval or source attestation was issued.
- NASA History Volume I mirror and Volume III were also frozen. They are not
  additional independent Volume I capacity. Volume III includes text-extraction
  gaps and lacks its own frozen NTRS rights record; it is excluded from eligible
  source/proof capacity. The initial SMA URL returned HTTP 500; the official NASA
  History and NTRS API downloads succeeded.

Exact URLs, bytes, SHA-256 values and local tokenizer SHA-256 are in
`configs/p58_domain_nasa_caib_preflight_v1.json`. Raw files are isolated under
`data/p58_domain_nasa_caib_v1/`; nothing was uploaded or promoted.

## Measured source capacity

Qwen3.5-4B tokenizer, `add_special_tokens=False`; raw joined text uses the recorded
page delimiter. Exact unique-line counts remove identical lines only, **not**
semantic duplicates or near-duplicate passages, and are not eligible proof tokens.

| Source | PDF pages | Raw joined tokens | Exact unique-line tokens |
| --- | ---: | ---: | ---: |
| NTRS CAIB Volume I | 248 | 291,154 | 260,881 |
| RTF Task Group final | 220 | 143,661 | 138,161 |
| NASA History Volume I mirror; do not add twice | 248 | 290,591 | 260,005 |
| Volume III; extraction/rights unresolved | 359 | 192,865 | 156,031 |

The two Volume I PDF byte representations and page-extracted texts differ;
diagnostic evidence uses the NTRS copy to bind it to the source-specific rights
record. No in-band packing or near-dedup pass is claimed.

## Real recommendation and disagreement chain

The original CAIB R3.2-1 requires an aggressive program to eliminate External Tank
thermal-protection debris shedding, especially around bipod attachment. The RTF
report's section 3.1 reproduces that requirement, interprets it, describes NASA's
implementation and testing, and records a genuine disagreement:

1. At the final June 27, 2005 plenary, the Technical Panel recommended closure as
   meeting the CAIB intent.
2. The full Task Group majority assessed R3.2-1 as **not met**.
3. Section 3.1.6 preserves the minority view that the intent **was met**, within
   the limitations in NSTS 60555.

These are different decision authorities, not a single status changing twice.
The June plenary narrative is PDF page 150 (printed page 148); original requirement,
implementation, majority assessment and minority opinion are PDF pages 33–40
(printed pages 31–38). The rule must retain majority and minority separately;
neither “NASA accepted remaining risk” nor the Technical Panel advice is the
Task Group majority's closure determination.

## Executable shortcut and source-removal findings

| Diagnostic | Complete contiguous source witness | Qwen tokens | Consequence |
| --- | --- | ---: | --- |
| Physical-cause launch time and RCC panel | CAIB PDF page 9 | 1,197 | 4K-answerable |
| All 29 recommendation identifiers | CAIB PDF pages 225–227 | 2,585 | 4K-answerable |
| Original required action, final majority, minority and decision date | RTF PDF pages 33–40 | 5,435 | 8K-answerable |

For the third task, the deterministic extractor returns the same answer after
removing the entire original CAIB source: RTF quotes its requirement. Removing
RTF makes the answer unavailable; empty input also fails. Thus the tempting
“CAIB → implementation → independent acceptance” source chain exists historically,
but the chosen answer does **not** require the original source. Adding it to a
128K/256K dossier would not create a necessary long dependency.

These are positive shortcut witnesses sufficient to reject the current strict
task recipes. They are not a replacement for the shared dense auditor, exhaustive
raw-window enumeration, question-only model evaluation or an executable
counterfactual adapter. Simple punctuation/line-break normalization is used only
to compare quoted required-action text across PDF reflow; token counts retain
the raw extracted text.

## Reproduction and next conversion

Run from the owning project root:

```bash
uv run python reports/p58_domain_nasa_caib_preflight.py
```

The program validates frozen bytes and tokenizer hashes, re-extracts all PDFs,
recomputes capacity, checks the 29-ID set size, executes source-removal assertions,
and writes `preflight_receipt.json` and `retrieval_diagnostic_recipes.jsonl` in the
isolated data directory. It emits no shared candidates. No dependencies were added.

For a useful retrieval/integration product, connect the retained recipes to a
source-bound shared adapter and validate majority/minority scope, repeated-source
handling, question-only answers, and train/eval source isolation before counting
them. The three recipes are not three independent source-worlds.

For strict expansion, a materially different oracle is required: for example,
independently frozen earlier implementation commitments whose changed content is
not reproduced by the final acceptance report, with answer-changing version
comparison and real intervention checks. That additional source/version work has
not been performed here. Retrying these three exact tasks with more pages or
processes cannot fix their measured short-window/source-removal shortcuts.
