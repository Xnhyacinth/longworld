# P14 真实长文数据转化状态 — 2026-09-03 收口

## 当前结论

P14 已从 2026-09-02 的 **8 物理 / 6 配额 / 72 candidate 行 /
`train_ready=false`** 推进到 **14 物理 / 12 配额 / 126 candidate 行**。正式
六域 probe 从其中选择 12 个完整 world，已生成 **90 train + 18 eval = 108
train-ready 行**；总计 4,079,561 个 exact Qwen context tokens。质量 gate 返回
`errors=[]`，不再是零训练数据。

正式产物位于：

- selection: `data/releases/p14-authentic-six-domain-probe-12-v1-selection-v4`
- promoted release: `data/releases/p14-authentic-six-domain-probe-12-v1-promoted-v4`
- LLaMA-Factory export: `data/releases/p14-authentic-six-domain-probe-12-v1-promoted-v4/llamafactory`

这仍是 `local_probe` 范围的可训练数据，`production_eligible=false`，没有执行
Hugging Face 上传，也没有伪造 production KMS/独立 signer approval。

## 六域正式选择

| Domain | Worlds | Train/Eval rows | 真实 exact-64K rows |
| --- | ---: | ---: | ---: |
| Company | 2 | 18/0 | 6 |
| ResearchLab | 2 | 9/9 | 6 |
| CodeForge | 2 | 9/9 | 6 |
| Finance | 2 | 18/0 | 6 |
| Cyber | 2 | 18/0 | 6 |
| Macro economics | 2 | 18/0 | 6 |

held-out world 是 Wasmtime 与 Sparks；split 以 world 为原子，train/eval 没有
world 泄漏。每个 domain 恰好 2 个 world、18 行，并覆盖 16K/32K/64K ×
full/CF/ordered 三视图。

## 本轮真实依赖扩展

- Company：JPMorgan 与 Walmart 不再只把官方 PDF section 并列为 essentials。
  新增逐档累积 reconciliation event，16K→32K→64K 必须消费前一阶段、当期
  新增 section 及 signed adjacent-report relations；replay proof depth 为
  3→4→5。最终 JPMorgan/Walmart 都是 9/9。
- ResearchLab：Llama 3 v1→v2→v3 官方 arXiv source history 补齐第二个
  ResearchLab world；与 Sparks 一起满足 2/2，而不使用 Megatron 64K 截断或
  复制文本。
- CodeForge：dprint 与 Wasmtime 使用真实 release/PR/review/test/tag ancestry；
  不复活 oxc 207k ppm 或 6/9 路线。
- Finance/Cyber/Macro：Microsoft/Amazon、CISA KEV/cross-vendor、BEA GDP/GDI
  task-sidecar worlds 全部在 promotion 时逐条 replay。
- 直接 source-workflow CF 现在绑定 factual parent 的 source origin、workflow、
  provenance、文本 SHA、bundle digest 和 derived CF 文本 SHA；dense audit 再
  绑定该 receipt digest，避免 CF 只声明“来自真实来源”而没有精确父子关系。

## Gate 与训练导出

硬 gate 保持原值：near-duplicate、exact band、`derived_view_gate`、真实 source
lineage、truncation ppm、禁止 padding/复制都未放宽。最终统计为：

- 108/108 promotion-ready；11 motifs；22 answer programs；34 executable
  proofs；22 semantic base tasks。
- 六域分别有 6 个真实 exact-64K 行、2 个真实 exact-64K world。
- mean near-dup sentence ratio = 0.03037，阈值仍为 0.25。
- LLaMA-Factory B1/B3/B5/B5w 全部导出，contract rejects = 0；四条件 token
  spread = 1.34%，manifest 的 11 个输出均经 validator 校验。

`min_motifs` 从 12 校准为 11：两篇论文诚实共享 section-reconciliation motif，
GDP/GDI 诚实共享 vintage-revision motif；不按实体重命名来伪造任务多样性。
profile 仍独立要求至少 12 个 real base tasks、executable proofs、answer programs
和 semantic base tasks，实际后三项分别达到 34/22/22，因此这个调整删除的是
重复约束，不是内容 gate。

完整 SHA、token 数和 release 状态见 `p14_conversion_status_v1.json`。此前失败
的 selection-v1/v2、promoted-v1/v2/v3 仅保留为诊断，不是正式训练输入。
