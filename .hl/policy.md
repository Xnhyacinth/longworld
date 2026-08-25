# LongWorld v2 orchestration policy

version: "2.0"
owner: "longworld"
updated_at: "2026-08-19"

objective:
primary_goal: "Scale N_proof × N_world-process × N_artifact × N_task × N_style, not n_worlds × n_view × n_length."
non_goals: - "210-world regen before N_eff ≥ 40" - "Pulse/prose fill to 64k/128k" - "Claiming ordered_artifact_view is ACC" - "Six new domains in the first slice" - "LLM-as-judge gold"

complexity_model:
this_run: high
routing: mixed
max_subagents: 4

quality_gates:
code: - "pytest tests/ green" - "no unique_prose / status_pulse fill" - "no universal _procedure / padding-manifesto discourse" - "new events do not put gold tokens in params"
data: - "12-world probe, not 210" - "canonical N_eff ≥24 with join_row_share ≤0.40" - "boilerplate 0; pulse 0" - "unbound source_pack never gold; ingest-bound packs may be essential for source_grounded and source_choice" - "unbound RFC is last-resort length, not preferred filler; HN is same-schema instances, not RFC" - "revisitation 3-hop LT-_; ratification 4-hop LT-_; docket_control DK-* distinct gold; source_choice adopted||unused pair gold; competing LD-; packer skip-oversized; export drops local_or_mixed on 32k+" - "N_style is sampled registers (≥3 in a 12-world probe), not doc_type aliases" - "SFT B5/B5w drop memory calendar cards; train buckets include 16k and 128k/256k caps; memory gold is unanswerable" - "leftover source packs include rfc9110 (not alphabetical drop); 128k/256k on train split when unique pool allows"

safety:

- "Do not kill foreign GPU jobs"
- "Do not delete data/p0 freeze"
- "data/ is gitignored; do not force-add jsonl"
