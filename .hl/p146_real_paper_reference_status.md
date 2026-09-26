# P146 source-backed real-paper reader candidates

P146 adds one generic recipe to the frozen 34-work raw-TeX inventory. It
extends P127's same-file, single-line reference search to adjacent natural
prose spanning source lines, cross-file links, and a second target type: the
first complete plain-prose sentence immediately after an exactly labeled
section heading. It compiles only from unique parser-recognized references and
literal text in the final reader. It does not rewrite paper text or fabricate
paper content.

The frozen v6 shard has **nine new independent QA tasks from six paper source
groups**, eight train and one eval. Six answer a referenced section's opening
sentence and three answer a table caption. Six cross source-file boundaries;
three remain within a file. Final-chat lengths are one below 32K, six at
32–64K, one at 64–128K and one at 128–256K. The largest reader has 169,899
final-chat tokens, a 143,965-token reference-to-target gap, and 14,292 tokens
from its last evidence to the question. All nine have at least 12,359 tokens
between the two cited spans; the last-evidence-to-query range is 61–26,229.
The shard contains 480,293 full-chat tokens and 193 assistant-supervised
tokens. All counts use the pinned final Qwen tokenizer/chat template.

The compiler checks source archive hashes; unique active reference and target;
answer absent from cue/label; one exact visible answer occurrence; clean prose
cue with no quoted TeX math/commands; removal of either reference or answer
span from the *reader text*; actual final-chat offsets, gap and mask; no
source-group/answer overlap with P139. The unified shard replay and a separate
all-row source/gold/deletion/mask test pass. These checks are bounded: they do
not prove there is no semantic paraphrase of the answer elsewhere or that a
model will learn the dependency. The work is local research only; source
license clearance and training gains remain unverified. `train_ready=false`.

The explicit decision ledger explains why throughput remains low. Among its
rejected proposals, 1,242 have no unique parser target; 383 lack a plain-prose
opening; 364 expose answer words through cue or label; 251 repeat the exact
answer elsewhere in the visible source; 118 lack a unique visible prose cue;
76 have ambiguous cue-to-reference binding; 34 fail the final token
gap/order gate. Ten eligible proposals were held at the four-task-per-work
cap. Rejection rows can include two answer modes for one reference; they are
not a count of distinct sources.

The 128K sample came from the 605,761-character arXiv 2303.12712 source;
P127 had admitted no QA from that work. P146's gain is therefore real source
coverage under the fixed gate, but nine tasks are a pilot, not large-scale
paper data. Expanding paper production requires more frozen source works with
high-yield unique links and answer text, then source-level split and the same
reader-side gates. Copying or padding the current contexts would not add
independent supervision.

Inspect and replay from the repository root:

```bash
cat data/candidates/p146_real_paper_reference_v6/manifest.json
less -R data/candidates/p146_real_paper_reference_v6/sample_index.jsonl
less -R data/candidates/p146_real_paper_reference_v6/audit.jsonl
less -R data/candidates/p146_real_paper_reference_v6/decision_ledger.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/p146_real_paper_reference.py --config configs/p146_real_paper_reference_v1.json --output data/candidates/p146_real_paper_reference_v6 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p146_real_paper_reference.py
```
