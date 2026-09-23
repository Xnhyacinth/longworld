# GraphWalks per-sample diagnostics (2026-09-21)

Reproduced by `scripts/diagnose_graphwalks_predictions.py` (deterministic, read-only
over eval outputs). JSON: `reports/gw_per_sample_diagnostics_20260921.json`.

Gold: `data/evals/mrcr_graphwalks_20260917/openai_graphwalks/graphwalks_128k_and_shorter.parquet`
(750 rows: parents 0–399, bfs 400–749; columns `prompt`, `answer_nodes`, `prompt_chars`,
`problem_type`, `date_added`). Uids in every `samples.jsonl` are
`graphwalks_128k_and_shorter.parquet:<type>:<global row index>` — the client's global-index
scheme, which this script uses. **Note:** `scripts/rescore_graphwalks_free.py` builds
per-type-count uids instead; these coincide for parents (rows 0–399 in type order) but
mismatch for bfs (bfs row 403 is labeled `bfs:2` by the client and `bfs:403` by the
rescore), so the rescore silently skips all bfs rows — no `summary.format_free.json`
exists for any bfs run, and its per-type mapping cannot be used for bfs.

Denominators: metrics are over ALL gold-matched rows for the run (400 parents / 350 bfs,
including the 50 overlength `skipped` rows whose response is absent and therefore scores
0) — matching the rescore's convention. `n_ok`/`n_error` are in the JSON. Two anomalies
carried in the numbers:
- `p64_base_ckpt680` rows have no `response` field at all (the raw-response storage was
  added to the client on 0918, after that run), so all its parse rates are 1.0 and
  metrics 0 — it is effectively unmeasurable, not "worse than b0".
- `arm_a_ckpt125` parents has 64 `error` rows (API connection errors) and bfs 78; its
  denominators still count them as 0.
- `arm_a_ckpt248` bfs has 248 error rows (only 52 scored).

Official parse = the client's last-line `Final Answer: [...]` parser (imported from
`scripts/eval_mrcr_graphwalks_client.py`). Format-free parse = last bracket group,
hex ids (mirrors `rescore_graphwalks_free.py`). `f1_standard` (local): 1.0 iff both
sets empty, 0.0 on parse failure, else harmonic mean (0.0 for disjoint non-empty).

## Headline metrics: run × subtask × parse mode

| run | subtask | parse | n | precision | recall | f1_std | exact | superset | |P| med (nonempty) | |P| p90 (nonempty) | |P| med (all) | parse_fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| b0_qwen35_4b_base | parents | official | 400 | 0.1053 | 0.1114 | 0.1139 | 0.1750 | 0.2000 | – | – | 0.0 | 0.8100 |
| b0_qwen35_4b_base | parents | format_free | 400 | 0.1581 | 0.1562 | 0.1506 | 0.1975 | 0.2300 | 3 | 7 | 0.0 | 0.7450 |
| b0_qwen35_4b_base | bfs | official | 350 | 0.0968 | 0.0971 | 0.0969 | 0.2857 | 0.2886 | – | – | 0.0 | 0.8771 |
| b0_qwen35_4b_base | bfs | format_free | 350 | 0.1197 | 0.1200 | 0.1340 | 0.2886 | 0.3114 | 3 | 9 | 0.0 | 0.7600 |
| p64_base_ckpt680 | parents | format_free | 400 | 0.0000 | 0.0000 | 0.0000 | 0.1100 | 0.1100 | 0 | 0 | 0.0 | 1.0000 |
| p64_base_ckpt680 | bfs | format_free | 350 | 0.0000 | 0.0000 | 0.0000 | 0.1971 | 0.1971 | 0 | 0 | 0.0 | 1.0000 |
| arm_a_ckpt25 | parents | format_free | 400 | 0.0791 | 0.3003 | 0.1087 | 0.0500 | 0.2550 | 20 | 84 | 6.0 | 0.4125 |
| arm_a_ckpt25 | bfs | format_free | 350 | 0.0500 | 0.2188 | 0.0727 | 0.1457 | 0.3086 | 55 | 153 | 0.0 | 0.6629 |
| arm_a_ckpt125 | parents | format_free | 400 | 0.0657 | 0.3532 | 0.0951 | 0.0725 | 0.3700 | 27 | 152 | 0.0 | 0.5500 |
| arm_a_ckpt125 | bfs | format_free | 350 | 0.0528 | 0.2859 | 0.0802 | 0.1486 | 0.3743 | 99 | 194 | 0.0 | 0.6371 |
| arm_a_ckpt248 | parents | format_free | 400 | 0.0687 | 0.3603 | 0.0984 | 0.0400 | 0.3750 | 23 | 123 | 2.0 | 0.4875 |
| arm_a_ckpt248 | bfs | format_free | 350 | 0.0288 | 0.1075 | 0.0420 | 0.1686 | 0.2829 | 18 | 96 | 0.0 | 0.8514 |
| arm_b_ckpt25 | parents | format_free | 400 | 0.0711 | 0.2130 | 0.0952 | 0.0650 | 0.2300 | 10 | 56 | 0.0 | 0.6475 |
| arm_b_ckpt25 | bfs | format_free | 350 | 0.0237 | 0.0982 | 0.0335 | 0.1714 | 0.2743 | 21.5 | 156 | 0.0 | 0.8343 |
| arm_b_ckpt200 | parents | format_free | 400 | 0.0707 | 0.2824 | 0.0974 | 0.0425 | 0.2575 | 16 | 107 | 2.0 | 0.4525 |
| arm_b_ckpt200 | bfs | format_free | 350 | 0.0600 | 0.2957 | 0.0881 | 0.1343 | 0.3371 | 79 | 162 | 0.0 | 0.5514 |
| arm_b_ckpt402 | parents | format_free | 400 | 0.0797 | 0.3149 | 0.1074 | 0.0525 | 0.2800 | 19 | 131 | 2.5 | 0.4550 |
| arm_b_ckpt402 | bfs | format_free | 350 | 0.0630 | 0.3076 | 0.0921 | 0.1371 | 0.3457 | 88.5 | 177 | 0.0 | 0.5486 |

Official-parse rows for all trained arms are omitted from the table as all-zero:
every arm response is a bare JSON list with no `Final Answer:` prefix, so the official
parser fails 100% of rows (parse_fail 1.0, precision/recall 0.0) — the only nonzero
official values are `exact_set_rate` at the empty-gold baseline (0.11 parents / 0.1971
bfs = the row-level rate of `P == G == ∅`), which the JSON carries. Two near-zero
exceptions: arm_a_ckpt125 parents 0.9875 fail rate (5 rows emitted a parseable line),
arm_b_ckpt25 bfs 0.9943 (2 rows).

b0's own official numbers (precision 0.1053 parents) differ from the official
`summary.json` (0.1203) because that summary averages over `n_scored`=350 `ok` rows
only; over the rescore's 400-row denominator it is 0.1053.

## Response-shape diagnostics (format-free)

| run | subtask | n nonempty parse | exact among nonempty | giant-list rows (|P|≥20) | mean fraction of graph nodes in giant lists | empty-gold rows answered nonempty |
|---|---|---|---|---|---|---|
| b0 | parents | 100 | 37 | 3 (3%) | 0.66 | 2 / 44 |
| b0 | bfs | 73 | 39 | 1 (1%) | 0.23 | 7 / 69 |
| p64_base | parents | 0 | 0 | 0 | 0.00 | 0 / 44 |
| p64_base | bfs | 0 | 0 | 0 | 0.00 | 0 / 69 |
| arm_a_ckpt25 | parents | 235 | 0 | 123 (52%) | 0.27 | 24 / 44 |
| arm_a_ckpt25 | bfs | 118 | 0 | 86 (73%) | 0.63 | 18 / 69 |
| arm_a_ckpt125 | parents | 180 | 0 | 119 (66%) | 0.78 | 15 / 44 |
| arm_a_ckpt125 | bfs | 127 | 0 | 102 (80%) | 0.89 | 17 / 69 |
| arm_a_ckpt248 | parents | 205 | 0 | 118 (58%) | 0.74 | 28 / 44 |
| arm_a_ckpt248 | bfs | 52 | 0 | 26 (50%) | 0.86 | 10 / 69 |
| arm_b_ckpt25 | parents | 141 | 0 | 50 (35%) | 0.33 | 18 / 44 |
| arm_b_ckpt25 | bfs | 58 | 0 | 30 (52%) | 0.47 | 9 / 69 |
| arm_b_ckpt200 | parents | 219 | 1 | 97 (44%) | 0.57 | 28 / 44 |
| arm_b_ckpt200 | bfs | 157 | 0 | 130 (83%) | 0.63 | 22 / 69 |
| arm_b_ckpt402 | parents | 218 | 2 | 108 (50%) | 0.56 | 25 / 44 |
| arm_b_ckpt402 | bfs | 158 | 1 | 130 (82%) | 0.69 | 22 / 69 |

Key pattern: trained arms answer empty-gold queries with nonempty lists at far
higher rates than b0 (34–64% of empty-gold parents rows across arm ckpts, 57–64%
at finals, vs 4.5% for b0; 13–32% on bfs finals vs 10% for b0), and on ~half or
more of their parsed rows they emit 20–200 ids covering a large fraction of the
graph's node inventory. b0's giant-list rate is 1–3%. p64_base's zeros are a
missing-data artifact (no responses stored), not a behavioral measurement.

Superset rate restricted to nonempty-gold rows (the 44 parents / 69 bfs empty-gold
rows are trivial supersets for every run and inflate the headline number):

| run | parents (n=356) | bfs (n=281) |
|---|---|---|
| b0 | 0.1348 | 0.1423 |
| arm_a_ckpt25 | 0.1629 | 0.1388 |
| arm_a_ckpt125 | 0.2921 | 0.2206 |
| arm_a_ckpt248 | 0.2978 | 0.1068 |
| arm_b_ckpt25 | 0.1348 | 0.0961 |
| arm_b_ckpt200 | 0.1657 | 0.1744 |
| arm_b_ckpt402 | 0.1910 | 0.1851 |

## False-positive taxonomy (format-free parse; per-FP-occurrence counts)

Depth buckets: parents FPs bucketed on **reverse**-BFS distance to the query node
(gold parents are reverse-depth 1; the query node itself is reverse-depth 0 via
self-loop exclusion); bfs FPs on forward BFS distance from the query vs the prompt's
target depth. "unreached" = id is in the prompt but not reachable from the query
along the bucketed direction. `same_depth_wrong_node` is structurally near-empty:
for bfs, gold = ALL nodes at the target depth, so an in-context FP cannot sit at the
gold depth; for parents it would require the model to name the query node itself
with a self-loop — observed 0.

| run | subtask | total FP ids | in_context | hallucinated | too_deep | too_shallow | same_depth | unreached |
|---|---|---|---|---|---|---|---|---|
| b0 | parents | 154 | 154 | 0 | 146 | 0 | 0 | 8 |
| b0 | bfs | 149 | 149 | 0 | 51 | 93 | 0 | 5 |
| p64_base | parents | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| p64_base | bfs | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| arm_a_ckpt25 | parents | 7945 | 7843 | 102 | 5734 | 50 | 0 | 2059 |
| arm_a_ckpt25 | bfs | 6982 | 6784 | 198 | 2073 | 2279 | 0 | 2432 |
| arm_a_ckpt125 | parents | 9874 | 9827 | 47 | 7772 | 104 | 0 | 1951 |
| arm_a_ckpt125 | bfs | 10555 | 10496 | 59 | 4130 | 3299 | 0 | 3067 |
| arm_a_ckpt248 | parents | 9712 | 9700 | 12 | 7400 | 98 | 0 | 2202 |
| arm_a_ckpt248 | bfs | 1445 | 1443 | 2 | 436 | 344 | 0 | 663 |
| arm_b_ckpt25 | parents | 3104 | 3033 | 71 | 1699 | 40 | 0 | 1294 |
| arm_b_ckpt25 | bfs | 2833 | 2627 | 206 | 616 | 259 | 0 | 1752 |
| arm_b_ckpt200 | parents | 7961 | 7934 | 27 | 6439 | 71 | 0 | 1424 |
| arm_b_ckpt200 | bfs | 11280 | 11259 | 21 | 4544 | 4032 | 0 | 2683 |
| arm_b_ckpt402 | parents | 9241 | 9219 | 22 | 6999 | 71 | 0 | 2149 |
| arm_b_ckpt402 | bfs | 12298 | 12282 | 16 | 4720 | 4307 | 0 | 3255 |

Graph reconstruction: the prompt edge list fully determines each graph; recomputing
parents (with the self-loop/query-node exclusion) and exact-depth BFS reproduces the
gold sets on 750/750 rows, so the in-context/hallucinated split and depth buckets are
exact, not approximate.

"Hallucinated" ids are almost never novel well-formed node ids: they are
transcription corruptions of real ids (e.g. `fef605fc8d` for the real `7ef605fc8d`,
plus junk like `ff00000000`, 9- or 11-char strings). Across the whole 750-row
vocabulary (8716 distinct md5-prefix ids) no "hallucinated" id appears in any other
graph — i.e., no cross-sample id leakage; corruption, not confabulation.

## Claims verified

1. **Prediction-set median 3 (Base) → 23 (trained arms): CONFIRMED (with the right
   denominator).** Format-free parse, parents: b0 median |P| over rows with a
   nonempty parse (n=100) = 3; arm_a_ckpt248 (n=205) = 23; arm_b_ckpt402 = 19
   (p90 131); arm_a_ckpt125 = 27. The official parser cannot produce this number
   (100% parse fail on arms); the median over ALL rows is 0 for b0 (75% unparsed)
   and 2–6 for arms. bfs follows the same pattern (b0 median 3; arm_b_ckpt402 90,
   p90 177). The review's number is real; its denominator is "rows with a
   parsable list."
2. **Exact-set answers 37 → 0: CONFIRMED, and the denominator is now identified.**
   37 = exact-set matches among b0's 100 parents rows with a nonempty format-free
   parse (b0 parents `summary.format_free.json` has `n_with_parsable_list: 100`;
   37 of those 100 have P == G; the other 42 of b0's 79 total exact rows are the
   empty-gold rows where both sets are empty). Trained arms: arm_a 0 at every
   checkpoint; arm_b 1 (ckpt200) and 2 (ckpt402) of 219/218 — effectively zero,
   driven by the giant-list mode, not by close misses.
3. **Superset rate increased: CONFIRMED (direction), with two caveats.** Headline
   superset (all rows): b0 0.2300 parents → arm_a 0.2550/0.3700/0.3750; arm_b
   0.2300/0.2575/0.2800. But the all-rows rate is inflated by 44/69 empty-gold rows
   that are trivially "supersets" for everyone. On nonempty-gold rows only
   (n=356): b0 0.1348; arm_a 0.1629/0.2921/0.2978; arm_b 0.1348/0.1657/0.1910.
   arm_b_ckpt25 is exactly at baseline, arm_a more than doubles. Direction holds
   for final checkpoints of both arms; magnitude is arm-dependent, and bfs is mixed
   (arm_a_ckpt248 bfs 0.1068 is BELOW b0's 0.1423, because most of its rows failed
   to parse at all after the API-error wave).
4. **Base format-free parents precision 0.1581: CONFIRMED, source found and
   recomputation matches exactly.** Source:
   `data/evals/mrcr_graphwalks_20260917/b0_qwen35_4b_base/graphwalks_parents/summary.format_free.json`
   (`format_free_precision: 0.1581`). Recomputed from raw samples over the same
   400-row denominator: 0.1581. Note the summary's own denominator: all 400 rows
   including the 50 skipped; over the 350 `ok` rows it is 0.1807. arm_a final =
   0.0687 and arm_b final = 0.0797 (both match their summaries, 400-row
   denominator).
5. **Parents degradation consistent across the exposure curve: CONFIRMED.** The
   format-free precision drop is present at the FIRST checkpoint of both arms and
   does not recover: b0 0.1581 → arm_a 0.0791 / 0.0657 / 0.0687 (ckpt 25/125/248)
   → arm_b 0.0711 / 0.0707 / 0.0797 (ckpt 25/200/402). Within-arm it is flat, not
   monotonic — the damage is immediate (first exposure checkpoint) and stable,
   which matches a response-prior/format shift at the start of SFT rather than
   gradual drift. Recall actually RISES (0.156 → 0.30–0.36): gold ids are usually
   inside the giant lists. Exact collapses (0.1975 → 0.04–0.07), |P| inflates, Jaccard
   drops; f1_standard roughly halves.

## What decides the next data wave

The FP breakdown is NOT "model invents graph nodes": 99.7–99.9% of arm FPs are real
nodes of the same graph, and the dominant bucket is **too_deep** (parents: 56–81% of
in-context FPs across ckpts, 76% at the arm_a final, are nodes that can reach the
query only through 2+ hops; bfs: mixed
too_deep/too_shallow ≈ nodes at wrong BFS depth) plus ~18–27% "unreached" nodes at
final ckpts (higher at ckpt25, up to 43%). The
failure is set-size calibration and depth discrimination, not node fabrication.
Empty-gold queries are the cleanest single signal: final arm ckpts answer them
nonempty at 57–64% on parents (28/44, 25/44) vs b0's 4.5% (2/44); on bfs 13–32%
vs b0's 10% — the arms have largely lost the ability to return `[]` where b0
still mostly can. A data wave that (a) trains exact empty-set answers, (b)
penalizes listing nodes beyond the queried depth, and (c) restores the
`Final Answer: [...]` last-line format (official parse is 100% failed on all arm
outputs) targets all three observed failure modes.
