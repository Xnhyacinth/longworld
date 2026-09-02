# Qwen3.5-4B MRCR / GraphWalks (2026-09-01)

Same-protocol greedy eval (`temperature=0`, `enable_thinking=false`, seed 42,
max length 131072). Official OpenAI graders. Artifacts live under
`data/evals/mrcr_graphwalks_20260901/` (gitignored). Launchers:
`scripts/eval_vllm_lm_eval_sharded.sh` (IFEval / GPQA / MMLU-Pro) and
`scripts/eval_vllm_mrcr_graphwalks.sh`.

This is **not** ACC Table 2 (thinking avg@3 on Qwen3-30B-A3B-Thinking).
Overlength rows are skipped when chat-template tokens exceed
`max_model_len - max_gen_toks`. GraphWalks headline is **precision**; official
F1 is 1 when precision and recall are both 0 (including parse misses).

| Model | MRCR 2 / 4 / avg | n (skip) | GraphWalks parents / BFS / overall P | n (skip) |
| --- | ---: | ---: | ---: | ---: |
| Instruct B0 | 0.966 / 0.831 / 0.899 | 491+484 (309+316) | 0.842 / 0.473 / 0.671 | 350+300 (50+50) |
| ACC ckpt-680 | 0.987 / 0.819 / 0.903 | 491+484 (309+316) | 0.818 / 0.483 / 0.664 | 350+300 (50+50) |
| 4B-Base | 0.495 / 0.260 / 0.377 | 491+484 (309+316) | 0.118 / 0.113 / 0.116 | 350+300 (50+50) |

ACC vs B0: MRCR slightly up, GraphWalks slightly down. Base is uninstructed;
the same chat protocol is an ablation floor, not a like-for-like instruct score.

4B-Base ACC then LongTrace SFT was **not** started: `.vendor/ms-swift/.venv`
was installed with `UV_LINK_MODE=symlink` into a pruned uv cache, so FLA/torch
were dangling. Setup now uses project-local `.uv-cache` and `link-mode = copy`.
Intended train recipe (unchanged GBS): 4 GPUs, SP=4, DP=1, accum=16, GBS=16,
680 steps, `flash_attn`, padding_free, DeepSpeed none.
