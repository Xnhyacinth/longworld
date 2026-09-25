# P96 frozen-paper cross-reference QA

This lane uses the five hash-pinned arXiv work inventories already admitted by
the P86 paper batch. It reads each work's latest frozen LaTeX archive and
deduplicates identical source-file copies; it makes no network request. The
parser finds an experiment/results/analysis reference whose nearby prose cue
is unique in the final reader, then follows a unique TeX label into another
source file. The target must be a uniquely labelled section heading or a
single caption in a figure/table block. Questions quote the prose cue but do
**not** provide the label. Each final reader keeps the full frozen source-file
packet, not a hidden fact graph or a rendered PDF.

The compiler resolves each link from the final reader text, masks the
reference and target spans separately, and requires either deletion to break
resolution. It also rejects cases where the full answer occurs in the quoted
cue or a short section heading can be decoded from the label. The final audit
reopens the pinned archive, retokenizes the chat bytes, checks both evidence
token intervals and query distance, reruns the resolver and deletions, and
verifies the assistant loss mask. This is a **source-level cross-file
reference task**, not a claim of scientific inference.

```bash
UV_LINK_MODE=copy uv run python scripts/run_p96_paper_reference_qa.py \
  --config configs/p96_paper_reference_qa_v1.json \
  --output-dir data/candidates/p96_paper_reference_qa_v4
UV_LINK_MODE=copy uv run python scripts/run_p96_paper_reference_qa.py \
  --config configs/p96_paper_reference_qa_v1.json \
  --output-dir data/candidates/p96_paper_reference_qa_v4 --verify-only
```

The current [manifest](../data/candidates/p96_paper_reference_qa_v4/manifest.json)
contains **8 new independent train tasks from two existing real paper worlds**:
MLRC 3 and GPT-3 5. Seven return a figure/table caption and one returns a
referenced section heading. Three final chats are 32–64K and five are
64–128K tokens; the range is 34,205–81,930. Actual two-span evidence extents
are 8,202–61,731 tokens, with final evidence-to-query gaps of 379–15,854.
The [final mask audit](../data/candidates/p96_paper_reference_qa_v4/mask_audit.json)
passed 8/8 readers: 512,154 full-chat tokens and 432 supervised tokens.
Output remains `train_ready=false`; no training gain was tested.

The separate native-to-unified adapter replays the frozen source compiler and
final audit before exporting the reader messages. The normalized
[candidate shard](../data/candidates/p96_paper_unified_v2/manifest.json) has
the same eight semantic tasks, with `source_kind=real_paper_source`, a
raw-LaTeX evidence profile, and no audit metadata inside model-visible rows.
Its manifest pins both the native manifest and final mask audit. Build or
verify it with:

```bash
UV_LINK_MODE=copy uv run python scripts/p96_paper_to_unified.py \
  --config configs/p96_paper_reference_qa_v1.json \
  --native-dir data/candidates/p96_paper_reference_qa_v4 \
  --output data/candidates/p96_paper_unified_v2
UV_LINK_MODE=copy uv run python scripts/p96_paper_to_unified.py \
  --config configs/p96_paper_reference_qa_v1.json \
  --native-dir data/candidates/p96_paper_reference_qa_v4 \
  --output data/candidates/p96_paper_unified_v2 --verify-only
```

The [capacity index](../data/candidates/p96_paper_reference_qa_v4/capacity_index.jsonl)
records 22 discovered cross-file links before final gates. Seven proposed
links failed the independent final-reader resolver. Five were rejected for
answer leakage through the cue or label, including `Consistency Checks`,
`Regularization`, `Further Scaling Analysis`, and `Text Samples`. AEVB's
candidate refers to two figures near the same cue and is rejected instead of
assigning a guessed caption. Inspect actual readers and provenance with:

```bash
less -R data/candidates/p96_paper_reference_qa_v4/train.jsonl
less -R data/candidates/p96_paper_reference_qa_v4/audit.jsonl
less -R data/candidates/p96_paper_reference_qa_v4/rejected.jsonl
```

The five archives are arXiv *source* trees. TeX comments, macros, conditional
compilation and figure inclusion are not fully rendered; the certificate
supports the source-visible reference graph, not the final typeset paper or
every semantic alternative. Four P86 works have a hash-pinned source bundle;
the GPT-3 inventory does not, and this batch does not newly verify historical
HMAC signatures. These eight tasks broaden operations on existing sources but
do not add domain labels or paper works to LongWorld.

`p96_paper_reference_qa_v1` through `v3` are historical. V2 added final
token-position and archive-hash checks; v3 and v4 filtered observed answer
shortcuts. **V4** is the only accepted native batch for downstream integration.
