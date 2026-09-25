# P104 frozen-paper source discovery and curated raw-TeX QA

P104 extends the existing P96 cross-file reference compiler without writing a
question for any named paper. `scripts/p104_paper_source_discovery.py` scans
frozen `paper_fetch_inventory.json` files, groups them by arXiv work ID,
excludes works already exposed by the pinned P86 source plan or P100 candidate
index, and verifies each selected archive through the P86 inventory/hash
reader. Four worker processes probe the latest source revision for the P96
parser's cross-file `\ref`/caption-or-section relation. It emits a P86-shaped
source config and P96-shaped QA config with `workers=4`; only two eligible
works existed, so the actual QA pool had two concurrent work jobs. No HTTP
fetch or GPU task was launched.

The frozen source inventory contained 16 inventory records for 10 unique
works. Five were already in P86/P100. Of the five newly considered works,
Adam had no cross-file link in its latest tiny source packet, Gopher's source
paths violated the reader boundary, and PaLM had no eligible cross-file
result link. Sparks and Llama 3 supplied two and five raw links respectively.
The deterministic discovery receipt is
`data/capability_records/p104_paper_source_discovery_v1/manifest.json` (SHA-256
`fc2d5aa666e427356e9dafe623d64e819d86e3d1e6e8f04c286ca9a6400a02d7`).
The selected source config preserves archive revisions, hashes, source
authorization basis and work-level train/eval split. Source bundles are
**byte-pinned**, but historical signatures were not cryptographically
reverified by this wave. Source use is local research only; redistribution is
not established.

The unchanged P96 native compiler took the seven discovered links through
reader-level resolution, cue/label shortcut checks, separate reference and
target deletions, exact final-chat positions and assistant-only masks. It
admitted three raw reader views: two Sparks train and one Llama 3 eval. Four
links were rejected: two answer-encoded labels, one answer in the question
cue, and one final-reader resolver mismatch. Raw native, unified and all-mask
receipts are under `data/candidates/p104_paper_reference_native_v1/`,
`p104_paper_reference_unified_v1/` and `p104_paper_reference_mask_v1/`.
The raw three views total 466,155 full-chat tokens but only 27 supervised
tokens. Physical lengths are one 64–128K and two 128–256K.

P104 then applied an additional answer-quality gate to the **final reader
bytes**. One Sparks title contains an unexpanded `\DV` TeX macro. The Llama 3
title `Post-trained Language Model` appears twice after case folding: the
earlier prose explicitly lists it as item `(2)`, only about 345 characters
before the target heading. Both are rejected. Their exact source IDs and
reasons remain in
`data/candidates/p104_paper_reference_curated_v1/quality_ledger.jsonl`.
The curated unified shard contains only the other Sparks task:

| Field | Curated task |
| --- | --- |
| Source group / split | `researchlab:arxiv:2303.12712` / train |
| Gold heading | `Misconceptions and Fact-Checking` |
| Full chat / supervised | 169,868 / 9 tokens |
| Final-template target span | `[74229, 74236]` tokens |
| Final-template reference span | `[79078, 79086]` tokens |
| Query start | token 169,817 |
| Two-evidence extent | 4,857 tokens |
| Last evidence to query | 90,731 tokens |

The curated shard manifest is
`data/candidates/p104_paper_reference_curated_v1/manifest.json` (SHA-256
`a4588131ae214f59612acd4eece96c646c29f9cf422c4c1901bde2a23664dd67`).
Its independent all-reader mask receipt is
`data/candidates/p104_paper_reference_curated_mask_v1/manifest.json` (SHA-256
`428aae0011d45cae7573901f961df4be5607802f113f52b4abe8da7902fd2621`):
one train view, 169,868 full-chat tokens, nine assistant-supervised tokens.
The quality gate rehashes every native source-index and audit file against
the native manifest before reading them; the two tamper regressions cover
both files. It also verifies the P104 discovery outputs, raw unified shard,
native mask and raw all-mask receipt.

This is a raw-LaTeX source-navigation task. P96 proves a bounded unique
resolver and answer change under deleting the two selected text spans; P104
excludes exact/casefold duplicate answer strings in the visible context.
Neither establishes a rendered-PDF answer, every possible semantic
alternative, minimum full-reader dependency, or model gain. After curation
there is no P104 eval example. The work-level source split remains clean, but
this one-task shard is evidence of pipeline reuse, not paper-domain scale.
Broader scale requires acquiring many new, usable TeX source worlds and
measuring the same gross-to-net yield rather than multiplying length views.

Replay and inspect from the project root:

```bash
cat data/capability_records/p104_paper_source_discovery_v1/manifest.json
cat data/candidates/p104_paper_reference_curated_v1/manifest.json
cat data/candidates/p104_paper_reference_curated_mask_v1/manifest.json
less -R data/capability_records/p104_paper_source_discovery_v1/capacity_index.jsonl
less -R data/candidates/p104_paper_reference_curated_v1/quality_ledger.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/p104_paper_source_discovery.py \
  --config configs/p104_paper_source_discovery_v1.json \
  --output-dir data/capability_records/p104_paper_source_discovery_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/run_p96_paper_reference_qa.py \
  --config data/capability_records/p104_paper_source_discovery_v1/qa_config.json \
  --output-dir data/candidates/p104_paper_reference_native_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p96_paper_to_unified.py \
  --config data/capability_records/p104_paper_source_discovery_v1/qa_config.json \
  --native-dir data/candidates/p104_paper_reference_native_v1 \
  --output data/candidates/p104_paper_reference_unified_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p104_paper_quality_gate.py \
  --config configs/p104_paper_quality_gate_v1.json \
  --output-dir data/candidates/p104_paper_reference_curated_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py \
  data/candidates/p104_paper_reference_curated_v1 --all \
  --output data/candidates/p104_paper_reference_curated_mask_v1 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p104_paper_source_discovery.py
```
