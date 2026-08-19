# Search notes — CausalTwin / LongWorld / Causal-Exposure

Date: 2026-08-18
Mode: exploratory (direction scouting + closest-work clustering)
Source policy: arXiv API + official abs/html pages; MDPI excluded

## Queries used (public)

- LongCrafter evidence graph; ACC agent context compilation
- counterfactual long context QA; evidence necessity leave-one-out
- EXACT effective-context exposure; LongFilter information gain
- InfoMem; Maven evidence-state; GEAR copy/distractor
- QwenLong-L1 / L1.5; Context Synthesis; LongPO; PolicyLong; EntropyLong
- TheAgentCompany; CRMArena; Cartridges self-study; TaskPress
- NSA; SeerAttention; DuoAttention; FutureQuery / query-agnostic KV
- SearchArt; S1-DeepResearch; LongTraceRL; WebClipper; LoongRL KeyChain
- SWE-smith; R2E-Gym; ByteSized32

## Verification method

Batch `export.arxiv.org/api/query?id_list=` for all user-cited 24xx–26xx IDs.
Almost all cited IDs **exist**. Notable ID corrections vs informal names:

- EXACT paper ID is **2605.10544** (not in original ACC-survey ID list)
- EntropyLong is **2510.02330** (not 2510.25804)
- 2510.25804 is **LongFilter / Beyond Length**
- QwenLong-L1.5 is **2512.12967**
- WebClipper is **2602.12852** (not 2607.24850; that ID is SearchArt)
- LoongRL/KeyChain **2510.19363** is a high-overlap missing citation

## Inference vs source

- Abstracts/HTML of listed papers: treated as **known from public source**
- “Reviewer will say X”: **inferred**
- Unfetched full PDFs (S1-DeepResearch internals, SearchArt verifier details): **needs-full-read**
