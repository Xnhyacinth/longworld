# P14 真实长文数据转化状态 — 2026-09-02

## 当前结论

P13/P14 当前有可审计的候选库存，但还没有可发布的正式训练包。Cyber
cross-vendor remediation 和新的 Microsoft multi-filing asset/operating-cash
trajectory 均已完成 16/32/64K × full/CF/ordered 的 9-cell dense audit。因此
当前账本从 7 物理 / 5 配额更新为 **9 物理 / 7 配额 / 81 行 / 3,047,978
exact Qwen context tokens**。所有行仍为 candidate，统一 selection 尚未在这个
9-world 账本上重跑；在 12/12 前重跑只会重复 fail-closed。

新增 Cyber world 的候选 SHA 为 `068a10daf9e4fbfbc0f59c91b4f2aff6fe043cbbcdef45b514f8bdf4adcfc18d`，
audit SHA 为 `d64aabeb43804ca02b88f65e84314a3b536b5535b16d88db6daeec32ea25fcce`。
九个 Cyber cell 均在 exact band 内，source-token measurement、strict replay、
CF、remove-one、4K/8K window 与 dense retrieval gate 保持原阈值通过。该
world 使 Cyber 达到 2/2。

新增 Finance world 使用 Microsoft FY2022--FY2025 四份 issuer-owned SEC iXBRL
10-K，不复用 Amazon Finance world。16/32/64K history 精确为
16,119/32,028/64,083 tokens，依次包含 2/3/4 filings、8/12/16 个必要 statement
rows 和 1/2/3 条相邻 filing 关系。第一次只使用 revenue/assets/balance rows 的
版本被 ordered-view 16K 窗口解出，未通过；最终程序加入逐年 operating-cash
证据和 trajectory 后重新物化，9/9 audit 通过，near-dup 最大值为 0.0。该
world 贡献 337,159 final-view tokens，使 Finance 达到 2/2。

## 剩余缺口与 P14 路线

| Domain | 当前/目标 | P14 路线 |
| --- | ---: | --- |
| Company | 0/2 | 换实体和 staged narrative/filing relation 程序，不再做 Microsoft reconstruction 或 YoY clone |
| ResearchLab | 0/2 | 换自然长度足够的论文、revision/review/benchmark 程序，不复用 MLRC/AEVB/Megatron 死路 |
| CodeForge | 1/2 | 换仓库与 artifact/task 类型，不再 retune oxc/ruff/pulumi/deno |
| Finance | 2/2 | 已由 Microsoft asset/operating-cash trajectory 补齐；不再扩充配额 |

硬 gate 不变：near-duplicate、exact band、`derived_view_gate`、真实 source
lineage、truncation ppm 和禁止 padding/复制。为减少冗余，只在新逻辑存在时
写失败优先的专属测试；每个 candidate 跑一次局部 9-cell audit；达到 12/12 后
再统一执行 selection、promotion、quality gate 和训练导出。

机器可读增量账本见 `p14_conversion_status_v1.json`；2026-09-01 的 P13 报告
保留为不可变历史快照。
