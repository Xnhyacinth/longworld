# P63 original-source checks for a world-to-task compiler

Checked 2026-09-09. Scope: the six requested original sources and the mechanisms
needed for the first reusable task compiler. **All six originals were accessible.**
Numbers below are verified statements in those sources, not reproduced experiments.
The requested unversioned SoG abstract currently resolves to v3; its v3 full text
was used. No novelty, universal scaling ratio or LongWorld training benefit is
certified by this check.

| Work and source status | Verified mechanism and relevant numbers | Immediate engineering implication |
| --- | --- | --- |
| [SWE-smith, arXiv v1, 2025](https://arxiv.org/html/2504.21798v1#S2) | A reusable repository environment supports many bug candidates. LM rewrites, AST edits, combined patches and PR reversal produce candidates; execution retains changes that break previously passing tests. Table 1 reports **50,137 instances / 128 repositories**; training uses **5,016 trajectories**. Repository setup still includes manual checking. | Amortize source/environment preparation. Generate many bound instances and validate them before language generation; do not count rewording or views as new environments. |
| [CLEVR, official CVPR 2017 project](https://cs.stanford.edu/people/jcjohns/clevr/) | Each question has natural language and a functional program over annotated scenes. Supported operations include attributes, counts, comparisons and spatial/logical relations. The official training split is **70,000 images / 699,989 questions**. | Store a canonical program and concrete bindings independently of the question text. A template can describe an operation family without fixing its answer. |
| [InfiniteScienceGym, arXiv v1, 2026](https://arxiv.org/html/2604.13201v1#S3) | A seed-driven simulator, privileged procedural QA generator and paraphraser are separate components. Code samples path/data filters and executes calculations; empty selections or invalid variable/operator combinations can yield unanswerability. Section 4 samples **500 questions from 15,988 questions across the first 500 repositories**. | First instantiate typed conditions and compute answers; render language afterward. Preserve an explicit unknown/invalid reason rather than fabricating a value. This is repository analysis evaluation, not evidence of naturally long SFT output. |
| [RACES, arXiv v1, 2026](https://arxiv.org/html/2606.12373v1#S3) | Environments expose a sampler, deterministic mapper, descriptor and verifier. Type-compatible continuations are actually executed in bounded randomized BFS; failed executions are rejected. SEQUENTIAL/PARALLEL/SORT/SELECT have different prediction and verification semantics. Table 2 reports Qwen3-4B averages **50.8 with 50 composed environments vs 50.4 with 300 individual environments**. | Compatible types propose an edge; runtime input/output consumption validates it. The 50-versus-300 result is a setting-specific observation, not a guaranteed sixfold efficiency gain. |
| [ArbiGraph, arXiv v1, 2026](https://arxiv.org/html/2607.20764v1#S2) | Each task has a prompt constructor, input/output contract and Python solver. Named predecessor outputs become successor inputs; intermediate truths are recorded. Generation rejects degenerate outputs. Scalar/list adapters can bound or reshape formal task states. | Keep execution traces and test instance-level redundancy. Do not transfer its formal list padding/truncation into real-document padding, or reject legitimate financial zero values merely because this benchmark filters trivial synthetic outputs. |
| [Synthesize-on-Graph, arXiv v3, 2025](https://arxiv.org/html/2505.00979v3#S3) | Entity–paragraph co-occurrence forms a context graph. BFS with context-similarity selection proposes cross-document paths; a second sampling stage balances entity exposure. CoT and contrastive clarification generate text afterward. | Graph walks are valid candidate proposals and coverage samplers. Co-occurrence and generated explanations do not establish executable financial, causal or version truth. |

## Five minimal checks for this batch

These are engineering deductions from the mechanisms above, not claims that
the papers already validate LongWorld's real-document setting.

1. **Reuse and distinctness.** From one frozen manifest, enumerate multiple
   program/parameter bindings. Hash source scope, canonical program and bound
   parameters into task identity; exclude paraphrase, view and padding length
   from that identity. Report parsed worlds, candidate tasks and accepted tasks
   separately from CF/reordered sample counts.
2. **Typed, executed composition.** Check issuer, period, units and reporting
   basis before arithmetic. Record each operator's inputs and output. A claimed
   sequential step must consume its predecessor's result; disconnected outputs
   remain parallel tasks. Detect self-comparison and redundant operations on the
   actual binding rather than counting AST nodes.
3. **Scope and readable-value agreement.** Verify the renderer's period,
   population, operation and rounding fields against TaskSpec. Every answer
   operand must bind to the correct document/hash and readable span, including
   its sign and units. Hidden XBRL attributes must not silently supply a value
   that the rendered document contradicts.
4. **Bounded provenance and alternatives.** Re-execute after a source-fact
   intervention and check any declared indispensable input against alternative
   disclosures within the supported grammar. Keep genuine summaries and complete
   relevant documents; a short sufficient source is a difficulty label, not a
   reason to remove the source or manufacture a long proof.
5. **Batch accounting and isolation.** Persist candidate → executable → scope →
   visible → distinct/profile counts and rejection reasons. Hold all variants
   of a scope-defective task. A new issuer should run through the same query
   library without issuer-specific query code; evaluation source groups must
   remain separate from earlier training products.

## Verification boundaries

- The six requested scale/mechanism statements above were checked against
  original project/paper content, not search snippets. Sources are linked at the
  corresponding claims; SoG's abstract was followed to its full v3 text.
- Other claims in the longer supplied review—including the other named papers,
  data-mixing percentages, and broad novelty comparisons—were outside this
  bounded pass and remain unverified here.
- This pass did not run the released paper code or reproduce training results.
  No additional compiler architecture or general graph language is required by
  these five checks.
