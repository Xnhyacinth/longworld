# LongWorld v2 orchestration policy

version: "2.0"
owner: "longworld"
updated_at: "2026-09-07"

objective:
primary_goal: "Scale verified independent semantic tasks from signed source graphs, not n_worlds × n_view × n_length. Strict long-dependency stays a separate profile."
non_goals: - "210-world regen before N_eff ≥ 40" - "Pulse/prose fill to 64k/128k" - "Claiming ordered_artifact_view is ACC" - "Six new domains in the first slice" - "LLM-as-judge gold" - "Pad unique leftover to a 128k bucket" - "Count packed parents or unaudited views as inventory" - "Auto-promote 4k/8k-window-answerable rows into the strict product"

scale_unit:
count: - "N_source-world" - "N_semantic-task" - "N_proof-family" - "N_training-view"
default_training: "one semantic task, one primary length; 10-20% paired second length; full length gradients stay diagnostic"
profiles:
  strict_long_dependency: "4k/8k/16k raw windows insufficient; keep current Canonical/strict inventory"
  integration: "multi-document, may be RAG-solvable; do not claim deep search"
  retrieval: "correct but short-window answerable; anti-interference / instruction-following only"
hard_gates: "source-supported answers, units/versions, provenance, no false labels, train/eval isolation, executable CF when the sample claims CF"
soft_gates: "every world collecting 16/32/64/128k; every task having CF; leftover-only RFC padding to 128k"

quality_gates:
code: - "pytest tests/ green" - "no unique_prose / status_pulse fill" - "no universal _procedure / padding-manifesto discourse" - "new events do not put gold tokens in params"
data: - "12-world probe, not 210" - "canonical N_eff ≥24 with join_row_share ≤0.40" - "boilerplate 0; pulse 0" - "unbound source_pack never gold; ingest-bound packs may be essential for source_grounded and source_choice" - "unbound RFC is last-resort length, not preferred filler; HN is same-schema instances, not RFC" - "revisitation 3-hop LT-_; ratification 4-hop LT-_; docket_control DK-* distinct gold; source_choice adopted||unused pair gold; competing LD-; packer skip-oversized; export drops local_or_mixed on 32k+" - "N_style is sampled registers (≥3 in a 12-world probe), not doc_type aliases" - "SFT B5/B5w drop memory calendar cards; train buckets include 16k and 128k/256k caps; memory gold is unanswerable" - "leftover source packs include rfc9110 (not alphabetical drop); 128k/256k on train split when unique pool allows"

safety:

- "Do not kill foreign GPU jobs"
- "Do not delete data/p0 freeze"
- "data/ is gitignored; do not force-add jsonl"
