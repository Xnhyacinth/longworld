# P85 unique-source paper revision pilot

Status: one new **train-split research candidate** from real paper source; `train_ready=false`. It is kept outside the P83 unified bank pending a broader source recipe and model evaluation.

The P84 source audit found that the previous ResearchLab compiler often targeted LaTeX boilerplate and copied file trees. The P85 compiler pins the AEVB v4→v5 archives through the P84 source route, removes byte-identical same-name copies, and selects changed prose lines from the distinct main and appendix sources. The two old/new file pairs contain 32,709 tokenizer tokens before record boundaries and the question. The final chat is **33,426 tokens** with 493 assistant-supervised tokens.

The question requires the old and new text of two different changes: a main-text Gaussian expression and an appendix description of the generative and variational components. All four answer lines occur once in the final reader. A visible-record diff solver reproduces the gold answer. Masking each of the four complete records or each of the four selected lines makes that solver unable to return the answer. The four answer spans cover **26,964 tokens** in the composed context under the pinned tokenizer. These are bounded text interventions and exact-line checks; no claim is made about every possible semantic paraphrase, model shortcut, or training gain.

The capacity report records per-version duplicate paths and per-file old/new token counts. It shows why a lexical `copy` path should not increase content capacity. This pilot is one task from one paper family, not evidence of scaled ResearchLab throughput or hundred-domain coverage. The next scaling step is a source-independent prose-hunk index with duplicate-evidence grouping, followed by source-held-out generation and final-reader audits across many papers. The task must still be evaluated in a controlled training mix before `train_ready` can change.

Inspect:

```bash
cat data/candidates/p85_aevb_revision_qa_v3/manifest.json
cat data/candidates/p85_aevb_revision_qa_v3/capacity_report.json
less -R data/candidates/p85_aevb_revision_qa_v3/audit.jsonl
less -R data/candidates/p85_aevb_revision_qa_v3/sample_index.jsonl
```

Compiler and pinned config: `scripts/compile_p85_paper_revision_qa.py`, `configs/p85_aevb_revision_qa_v1.json`.
Independent recompilation to `p85_aevb_revision_qa_replay_v1` matched the final
manifest, reader, index, audit, and capacity report byte for byte. Three
focused regression tests, Ruff, format, and diff checks passed.
