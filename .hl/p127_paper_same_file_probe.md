# P127 frozen-paper same-file reference pilot

**Bounded local research result; `train_ready=false`; not appended to the
candidate bank.** P127 tests one reusable raw-TeX relation absent from P96:
a natural prose cue contains an active `ref`/`autoref` whose unique section or
caption target is farther away **in the same source file**. It reuses P96's
frozen TeX renderer/target parser and P104's exact-answer uniqueness rule.
The question quotes the prose cue but not the TeX label. The reader sees the
complete source-file packet, not hidden task metadata.

The only current candidate is
`data/candidates/p127_same_file_paper_reference_v4/` (manifest SHA-256
`eeeb39a92590de937491ed200cd4f2d9ce30a48a78b4fc981407a913b1e12ec5`).
The manifest pins the two P113 source matrices, all seven output files, the
configuration, prior P120 candidate refs, and compiler code SHA-256
`48c3017bae853e871dca0c6031845aae2602c2540a1521c7ed2771f1b0a8b188`.
Each proof pins its source archive SHA and the original work-stable split.
Verify-only checks the compiler SHA before rebuilding and byte-comparing the
full output. V1–V3 are unpromoted diagnostics: V1's broad local resolver
accepted only two, V2 admitted eight before stronger label review, and V3
admitted six before plural/prefix shortcuts were rejected.

| Gate on frozen P104/P105/P110/P113 works | Count |
| --- | ---: |
| Source works / parsed / parser rejects | 34 / 29 / 5 |
| Active and commented TeX references scanned | 2,091 |
| Unique same-file target geometry | 540 |
| Unique cue attached to a same-file target | 299 |
| Passed parser, exact-answer and conservative label/cue word gates | 42 |
| Final reader, ≥12,288-token support gap, mask and source-prior gates | **2 tasks, 2 worlds** |

The final two are both train tasks, one from arXiv work `2606.13877` and one
from `2510.27268`; the original split was not relabeled. Their final-chat
lengths are **21,692 and 36,186 tokens**, with **12,915 and 16,982 tokens**
between the reference and target support spans. The two inputs have 57,878
full-chat and only 15 assistant-supervised tokens. Every final mask is
assistant-only, positive and byte-replayed. `proofs.jsonl` records both
visible source spans and actual prompt-token spans, the exact answer's sole
visible occurrence, one parser-recognized target for its label, unique cue,
and distinct reader-context-minus hashes for removing either the reference
or target. Both deletions break the final-reader resolver. These checks are
bounded to source-visible TeX links and exact answer strings; they do not
prove no paraphrased title or other semantic path exists, nor that the model
learns long-context reasoning.

The other source-native options remain limited: P113's new six frozen works
had zero cross-file-reference work, while its separate revision route made
one task; P115's 40 report cells yielded three Amazon threshold tasks, 15
unsupported table-grammar cells and 22 with a shorter numeric-support
window. Replacing topic or issuer labels does not create missing source
relations. P127's result likewise shows that same-file references alone are
not a high-throughput paper recipe under a strong shortcut and distance
contract. The decision ledger preserves all rejections. The next source
adapter should improve native section/table parsing or choose sources with
more verified relations before another acquisition wave.

All 34 source records have an unreported license URI (`21`
`not_recorded_in_capacity`, `13` `not_reported_by_atom`), so this artifact is
`local_research_only_no_redistribution`. It has no gold-blind model test,
train/eval release, GPU run or training-benefit claim. Independent adversarial
review remains separate from the producer's replay.

```bash
cat data/candidates/p127_same_file_paper_reference_v4/manifest.json
less -R data/candidates/p127_same_file_paper_reference_v4/support_matrix.jsonl
less -R data/candidates/p127_same_file_paper_reference_v4/decision_ledger.jsonl
less -R data/candidates/p127_same_file_paper_reference_v4/proofs.jsonl
less -R data/candidates/p127_same_file_paper_reference_v4/sample_index.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/p127_same_file_paper_reference.py --config configs/p127_same_file_paper_reference_v1.json --output data/candidates/p127_same_file_paper_reference_v4 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p127_same_file_paper_reference.py
UV_LINK_MODE=copy uv run --offline ruff check scripts/p127_same_file_paper_reference.py tests/test_p127_same_file_paper_reference.py
```
