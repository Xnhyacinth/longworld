# P12 baseline、promotion 与 scale-out 状态 — 2026-08-31

## 结论

截至本报告截点，**LongWorld ACC = N/A**。仓库中尚不存在使用当前
LongWorld 数据训练、并在固定底模和统一评测设置下运行的 checkpoint，因而不能把
任何外部论文的 LongBench、LongBench v2、RULER 或 HELMET 分数写成 LongWorld
成绩，也不能声称当前数据优于 baseline。

当前可以确认的是两类彼此分离的本地资产：

| 资产                    |                                                    当前数量 | 信任与用途边界                                                                     |
| ----------------------- | ----------------------------------------------------------: | ---------------------------------------------------------------------------------- |
| content-audited CPT     |                  3,006 rows / 286,634,406 exact Qwen tokens | local-probe；manifest 明确为 `train_ready=false`、`production_eligible=false`      |
| promotion-v2 SFT        | 12 rows / 2 source-bound worlds / 454,846 exact Qwen tokens | 已通过当前本地 promotion 门禁，但仍是 local-probe，未达到 12-world production 边界 |
| production/KMS eligible |                            **0 rows / 0 worlds / 0 tokens** | 尚无可正式训练发布的数据                                                           |

CPT 的长度分布为 300×16K、253×32K、1,111×64K、1,109×128K 和
233×256K；其中 64K/128K 各 1,000 条来自配置 quota，其余是过滤后的自然容量。
全部 3,006 条通过内容、精确 token、来源记录、去重和跨 release 重叠审计；显式
minimum-span 与 truncation-quality gate 目前只覆盖新增 Bitcoin/pandas 381 条。
这些行不是 executable SFT task；不能用 CPT 的 3,006 rows 代替 12 条
promotion-v2 SFT，也不能把 authentic source history 等同于 answer-changing
long-range dependency。

提交 `776c8cb93b60c7548b35e2a8963789032555aae7` 之前的冻结验证结果为
**1,409 passed / 1 expected xfail**；它不代表本报告之后仍在修改的 Git-history
license-binding 工作树。当前 diff 在最后两项 audit guard 之前完成全量
**1,419 passed / 1 expected xfail**；guard 加入后的 Git-history、CPT window 和
shared public-source governance 聚焦验证为 76 passed。对应命令覆盖
`test_githistory`、`test_cptwindow`、materialize、audit、filter、merge 和
`test_real_source_workflow` 七个文件；精确最终树的全量回归仍待重跑。Swift v2
deterministic validator 缺失仍保持 fail closed。

## 与公开 baseline 的诚实对照

下表只比较数据规模和论文原设置内报告的结果。模型、tokenizer、训练预算、提示、
上下文长度和 evaluator 均不一致，分数不可横向当作 LongWorld 的 ACC。

| 工作                                                            | 数据/训练规模                                    | 论文原设置内的证据                                                                                                                       | 与 LongWorld 的关系                                                                             |
| --------------------------------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| [LongAlign](https://arxiv.org/abs/2401.18058)                   | 10K long SFT，8K--64K，九类来源                  | 同底模 0K/5K/10K 的 LongBench-Chat 为 3.73/5.97/6.21；0K→10K 时 Single-Doc QA 58.7→64.0、Multi-Doc QA 41.1→44.4、summarization 38.4→44.2 | 支持做 scaling curve；LongWorld 规划 1K/5K/10K，但当前 12 条 SFT 不能证明效果                   |
| [LongCite](https://arxiv.org/abs/2409.02897)                    | 约 45K citation-oriented long QA                 | 报告 LongBench-Cite citation quality 提升                                                                                                | 证据定位监督与 LongWorld 相关，但 citation 专项指标不是通用 long-context accuracy               |
| [LongRLVR](https://arxiv.org/html/2603.02146)                   | 46K grounded QA，8K--64K                         | Qwen2.5-14B 上，verifiable context reward 相对 outcome-only RLVR 将 RULER-QA 73.17→88.90、LongBench v2 39.8→46.5                         | 方法上与 `essential evidence + replay sidecar` 相关；支持显式证据监督，不代表相同数据或训练设置 |
| [LongRecipe](https://arxiv.org/abs/2409.00509)                  | 约 1.8B training tokens                          | 论文在 RULER、LongBench 和一般能力上呈现混合结果，并报告训练资源节省                                                                     | token 规模约为当前 local-probe CPT 的 6.3 倍；同时说明不能只优化单一长上下文 stress test        |
| [ProLong](https://arxiv.org/abs/2410.02660)                     | 40B CPT tokens                                   | 128K HELMET 平均 49.4，对照 Llama-3.1-8B 为 46.5；但 RULER 为 71.9 对 81.3                                                               | token 规模约为当前 CPT 的 140 倍；不同 benchmark 结论冲突，必须使用组合评测                     |
| [Long-dependency Prospector](https://arxiv.org/html/2405.17915) | 对自然长书籍、论文和代码按 dependency score 选择 | 等量高分选择可优于随机选择；重复和随机组合可制造虚假 dependency                                                                          | 直接支持 LongWorld 的正文腐化、near-duplicate、dependency specificity 与 answer-changing 检查   |

[LongBench v2](https://aclanthology.org/2025.acl-long.183/) 论文给出的共同量尺是
25% random、53.7% 限时 human、50.1% 最佳 direct-answer model、57.7%
reasoning model；这些值仅描述该论文的评测快照。LongWorld 在该 benchmark 上仍为
**未运行**。[RULER](https://github.com/NVIDIA/RULER) 是可控 synthetic stress
test，而 [HELMET](https://arxiv.org/abs/2410.02694) 覆盖七类应用任务并明确指出
NIAH 不能代表广泛 downstream quality；两者都需要保留。

## 公平训练与评测矩阵

第一轮训练的研究问题不是“LongWorld 绝对分数有多高”，而是：在固定底模、固定
token 预算和固定训练配置下，source-bound executable workflow 是否比普通长文、
长度匹配的随机/结构化拼接和 answer-only supervision 更有效。

| 轨道                   | 对照组                                                                 | LongWorld 组                                                              | 必须固定                                                                                         | 主要输出                                                              |
| ---------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------- |
| CPT data-quality pilot | 等 token 的普通真实长文；等 token 的 length-matched structured packing | 当前 content-audited CPT                                                  | 同一 base checkpoint、50M/150M/286,634,406 token budgets、optimizer、tokenizer、steps、seed 集合 | HELMET、RULER 分长度、LongBench v2、一般能力 delta                    |
| SFT scaling            | 相同任务答案但无 evidence/replay；相同 token 的常规 long QA            | source→state→answer + essential evidence + CF/remove-one/corruption       | 同一底模、1K/5K/10K rows、上下文 band 分布、模板和 decoding                                      | 外部 accuracy、evidence recall/F1、answer accuracy、closed-book delta |
| sidecar ablation       | answer-only；producer-declared evidence                                | source-role-signed static `task_replay_sidecar`                           | 相同 candidate、ranking、训练样本和 evaluator                                                    | grounding 增益、CF replay、remove-one 与正文腐化失败率                |
| generalization         | seen-world random split                                                | unseen world/entity、topology/operator、source family、domain composition | 去重边界、benchmark snapshot、prompt、max length                                                 | 各 split 绝对分、相对 base delta、bootstrap CI                        |

统一评测至少包括：

- LongBench v2 overall、difficulty 和 length buckets；
- RULER 4K/8K/16K/32K/64K/128K；
- HELMET 的七个类别和 128K macro average；
- MRCR/GraphWalks 等长程检索与组合推理；
- MMLU、GSM8K、HumanEval 等短上下文一般能力；
- full-context、closed-book、4K/8K/16K window、BM25/embedding top-k；
- essential evidence recall、singleton/remove-one、actual/CF answer、正文腐化、
  answer-changing dependency、semantic near-duplicate 和 semantic-growth。

所有结果应同时报告绝对值、相对无 LongWorld control 的 delta、三个或更多固定 seed
或可复现 bootstrap 置信区间、短上下文 retention 和 closed-book 变化。当前 12 条 SFT
只适合 pipeline smoke test，不适合作为模型效果结论。

## 12→48→210 的容量预算

现有 release profile 只定义质量下限，不规定“每个 world 必须生成固定行数”。因此下表
将不可放松的代码门禁与为了形成可训练 scaling curve 的建议产能分开；建议数不是已经
通过的 release，也不能用 `band × view` 替代新的 semantic task、proof 或 source
relation。

| 阶段      | 当前事实、已有代码下限或明确 proposal                                                                                                                                               |                                                                                         建议可训练产能 | 建议语义规模                                                                                           |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -----------------------------------------------------------------------------------------------------: | ------------------------------------------------------------------------------------------------------ |
| 当前      | 2 worlds、12 rows、454,846 tokens；仅 arXiv/ResearchLab                                                                                                                             |                                                                                    只作 pipeline smoke | 2 query types、2 motifs、6 programs/proofs                                                             |
| 12-world  | 代码当前只约束 4 Company + 4 ResearchLab + 4 CodeForge 等总量下限；把 ResearchLab 再分成 2 paper + 2 KB、10 train/2 eval、48 条真实 64K 是本轮 proposal，尚未写入 selection receipt |                                                    每档 96 条 16/32/64K，共 288 rows，约 10.75M tokens | 每 world 4 个 source-independent tasks/programs；至少 48 semantic tasks、144 个 band-specific proofs   |
| 48-world  | 当前可发行 profile 尚未定义；历史 profile 只能作为 48 worlds、144 条真实 64K、12 programs 的下限参考                                                                                |                                                 每档 768 条 16/32/64K，共 2,304 rows，约 86.02M tokens | 建议至少 32 entities、384 semantic tasks、1,152 band proofs、16 relation kinds、24 program families    |
| 210-world | 当前可发行 profile 尚未定义；必须继承新 48-world receipt                                                                                                                            | 16/32/64K core 10,080 rows；真实增长通过后再加 840×128K 和 210×256K，共 11,130 rows、约 537.60M tokens | 建议至少 120 entities、1,680 semantic tasks、5,040 band proofs、32 relation kinds、48 program families |

首轮建议的 canonical 12-world 真实实体组合是：SEC 使用 Amazon、NVIDIA、Microsoft、
Apple；GitHub 使用 dprint、Pulumi、Deno、Ruff；paper 使用 Attention 与 MLRC；
Wikimedia/KB 使用 Jefferson 与 Newton。CISA KEV、Oxc、uv、Wasmtime、Godot、
Megatron-LM、Einstein 和 RFC 9421 是后续独立扩容来源；它们必须重新导出真实记录并
改变 state/answer/replay，不能只替换实体字符串。

从实验规模看，当前 0.286B CPT tokens 已足够运行 50M/150M/286.6M 的早期
equal-token 曲线，但低于 LongRecipe 的约 1.8B 和 ProLong 的 40B，不能直接用论文
原分数横比。CPT 的公平对照应是同源随机 packing、保留时间顺序但不做 dependency
筛选、长度匹配的普通连贯长文和 dependency-score 选择；ACC 则是 SFT 对照，不属于
CPT 轨道。LongWorld 达到至少 1,000 条 current-code promoted rows 后才可做趋势性
ACC pilot；达到约 10K rows、完整 unseen splits 和相同底模/token/steps 后，才适合
正式 matched comparison。

CPT 的建议扩容阶梯单独按 token 而不是 SFT world row 计数：C0 保留当前
50M/150M/286.6M 诊断曲线；C1 扩至约 1.8B tokens、至少 50 个真实实体和 6 个 source
families，并限制单实体不超过 5%、单 domain 不超过 35%；只有 C1 相对 equal-token
controls 在长上下文指标上有稳定增益且一般能力不下降，才进入约 5B 的 C2。ProLong
的 40B 只作为远期量级参考，不应在 0.286B 尚未验证有效性时直接追量。新增 token
必须来自新的真实时间段、filing/revision/release cycle 或跨源 workflow，不能靠重复
窗口、实体换名或随机文档拼接。

## 本轮真实执行结果

- Cyber v7：3 个 16/32/64K candidates（16,349 / 32,273 / 64,273）、3 个
  dense rankings、3 个 auditor-signed task replay/proof receipts；
  `pipeline_candidates.jsonl` / ranking / audit SHA-256 分别为
  `b532195b352e0fe65549bde2197d59c931f2291ea8f83d4c4c3ad25fba611e23`、
  `5040789faf2b9231c99b3ce0e91fdcde66b343cba9c73697fb1059d47e1dd779`、
  `5e93ad4d4a6aea77189591fba4ef8e55cc4476659fb8ef4cb11f1c896bb76ae9`。
- Finance v4：3 个 16/32/64K candidates（16,082 / 32,482 / 64,207）、3 个
  dense rankings、3 个 auditor-signed task replay/proof receipts；对应 SHA-256 为
  `bf8809a55cdb9ccf53f5908512cdb6875e7901b0bf07ed6f084f478f14fef357`、
  `263476810e19b550ba487ac19d050856fb65c333489ebf2b245eeda10ebd2fbe`、
  `baafab4992867ea44657c20cfc81cbc0121889dead67aa4d98a350f83524a66d`。
- 两个 adapter 共 6/6 产生 local-probe diagnostic auditor receipt，满足
  source-role exact-byte sidecar、signed candidate commitment、正文/分类/source
  relation 重放、full/minimal/CF、remove-one/single/empty、BM25/TF-IDF、dense
  top-3 不足和 full-pool strict replay。artifact-aligned 4K/8K/16K 子串计数已
  修复 BPE 前缀差值漏窗，但最终复审确认它不等价于任意 tokenizer offset 的滑窗
  穷举。新 receipt 因而明确记录 `window_scope=artifact_aligned`，不再把 generic
  local/contiguous window 与 `no_shortcut` 标成 true；这 6 条当前不能 promotion。
  `closed_book_unsolved` 仍只是 empty-evidence executable proxy，尚未运行真实模型
  closed-book eval。
- 收紧窗口语义、supporting-growth 与训练内容身份后，已用相同 signed
  candidates/rankings/sidecars 重跑最终 diagnostic receipt v5：
  Cyber scoped audit SHA-256 为
  `edd1c9cb2c566dce45707da23342baa47c7b83f4ea96a9f09e493302d94fefbd`，Finance 为
  `899f846e374f7092c4b68484aecff7fc4be5ff00857cb0bdb96e99c692247b2a`；两者均为
  3 audits / 3 diagnostic accepts / 0 adapter rejects，且
  `global_proof_green=false`、`contiguous_windows_insufficient=false`。
- Auditor 不再复制 candidate 自报的 growth 数字：Cyber 的 answer-bearing source
  relation targets 为 81→158→313、Finance 为 18→27→36，均通过跨档真包含检查；
  strict support replay truth 分别为 82→159→314 与 20→30→40，executable proof
  depth 分别为 81→158→313 与 4→5→6。`causal_supporting` 只能由 replay relation
  path 推导，背景重标会被拒绝；当前两条真实链均为 0。`semantic_tokens` 使用 pinned
  tokenizer 对完整必要 artifact 的 whole-artifact upper bound；在 adapter 输出精确
  语义 span 前，不把它声称为 span-level semantic token。
- 训练内容去重现在以实际导出的 `context+answer` 为准，不信任 producer
  `content_hash`，并在 preflight、无 profile audit、selection、promotion、report、
  quality gate 和 deterministic export 全链拒绝 metadata clone 与同 prompt 多答案。
  LLaMAFactory v4 可执行 transform 已接入训练校验；尚无 validator 的 Swift v2
  明确 fail closed。
- Task audit 的 v2 semantic commitment 现在同时绑定 replay/growth 和所有会进入
  diversity/source quota 的稳定字段：motif、base task、answer program、executable
  proof、semantic base task、真实 source family/workflow/token ratio 及 source
  relation identity。Signed selection 绑定 candidate→commitment；task promotion 缺少
  selection 会直接失败；train-ready report 从实际 row 重算，因此 promotion-role
  重签不能虚增 world 的语义或来源贡献。
- 训练入口不再在校验后继续读取可变 export path。Validator 对每个 manifest-bound
  output 以 1 MiB 分块复制并同步校验 bytes/SHA-256，保留 output-root 相对层级，
  全部通过后才以 0400/0500 权限原子发布私有 content-addressed snapshot；非 sticky
  或 foreign-owned 的 `TMPDIR` 会 fail closed。LLaMAFactory 只读取该 snapshot；
  Swift 的 snapshot/cache 隔离接线已完成，但因 Swift v2 尚无独立 executable
  deterministic transform validator，签名 Swift 入口仍按设计 fail closed，不能
  声称当前可训练。Tokenizer asset 的加载期原子快照仍是 production operational
  blocker。

## HF 暂不上传新训练数据的原因

不上传不是 Hugging Face 技术故障，而是正确执行发布边界：

1. 3,006 条 CPT 的 manifest 明确写有 `train_ready=false` 和
   `production_eligible=false`；上传到正式训练路径会把 local engineering probe
   误标为 production dataset。
2. 当前 promotion-v2 SFT 只有 2 worlds / 12 rows，尚未达到至少 12 个完整
   source-bound worlds 的统一 promotion 前置条件。
3. Finance/Cyber 现在已有 auditor-signed artifact-aligned window diagnostics、
   BM25/TF-IDF、closed-book 与 replay proof；尚缺真正 token-offset window proof。
   即使补齐该门禁，仍只有 2 个 task worlds，尚未进入满足 12-world/domain quota 的
   统一 signed release selection，也没有独立 production trust。
4. production/KMS eligible 数量仍为 0；独立 approval、生产信任根及其下游 digest
   绑定尚未形成可发布链。

在这些条件满足前，可以保留本地 immutable candidates、audit receipts 和 reject
ledger，但不得把它们作为正式 train split 上传。首次 HF 更新应只包含通过 production
manifest 的行，并同时上传 release manifest、source/sidecar digest、去重审计和
benchmark snapshot；不能通过更名或目录移动把 local-probe 升格。

## 当前 world/entity 阻塞表

这是本报告截点的可复现状态；历史单 band diagnostics 不计入完整 world。

| World/entity     | 已有真实资产                                                                                                                                                  | 当前硬阻塞                                                                                                 | 正确下一步                                                                                                                                                            |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Finance / Amazon | 3 个 multi-filing candidates，16,082 / 32,482 / 64,207 exact Qwen tokens；source commitment、dense、BM25/TF-IDF 与 artifact-aligned diagnostic audit 3/3 通过 | 缺少 token-offset window proof；之后仍只有 1 个 Finance world、缺少 12-world selection 与 production trust | 先实现原始 token 片段 replay 并重跑；再扩展真实 issuer/multi-filing worlds，不把单 world probe 升格                                                                   |
| Cyber / CISA KEV | 3 个 executable candidates，16,349 / 32,273 / 64,273 exact Qwen tokens；官方 source receipt、dense 与 artifact-aligned diagnostic audit 3/3 通过              | 16-shard 枚举不能证明 shard 内 token-offset shortcut；总 task worlds 仍不足 12                             | 将 raw token slice 接入 adapter replay 后重跑；扩展独立真实 Cyber workflows，继续保持低容量 domain reject                                                             |
| Deno             | pinned v3 rerun 保留 3 个 64K views，均 65,496 tokens；quality report 已签名                                                                                  | 16/32K 均因 `strict_support_overflow` 正确拒绝，retention 0.1667，仍非完整 world                           | 从真实 tags、CI/release recovery 和跨 cycle answer-changing closure 补足 lower bands；不截断 proof、不加无关正文                                                      |
| Ruff             | 已有真实 release/PR/CI history，但当前 retained rows 为 0                                                                                                     | 16/32K 有 contiguous-window shortcut；64K 真实 closure 仅 62,846 tokens                                    | 导出更多真实 release cycles、失败恢复和跨 cycle 依赖，改变答案程序后重新 strict replay；不得复制或降低长度/窗口门禁                                                   |
| Oxc              | 已有真实 monorepo history 和严格 tag-family 修复                                                                                                              | 16K 存在 contiguous-window shortcut，32/64K strict support overflow，无法组成完整 world                    | 在同一真实 tag family 内增加必要的跨 release failure/recovery 与 answer-changing source relation，重新做 lower-band evidence selection                                |
| Microsoft        | 已有 issuer-owned SEC/GCS 解析与 lower-band rows；当前真实 bundle 只有两份 filing                                                                             | 两份 filing 只能支撑 lower band；nested program 对 16/32/64/128K 分别需要 2/3/4/5 份真实 filing            | 从 SEC/issuer allowlist 继续导出同一公司的真实历史 filing、XBRL facts、certifications 和非重叠 sections，再构造 multi-filing state/answer；不能复制当前年份或扩写模板 |

## Promotion 与 scale-out 次序

1. 已完成静态、闭集 adapter registry 的 `task_replay_sidecar`，由 source role 对精确
   sidecar bytes 和每个 world/band 的 candidate content commitment 签名；audit
   从同一 exact bytes 验证来源，并拒绝 cross-world sidecar reuse、TOCTOU、未知
   adapter 和多重/缺失 replay binding。
2. Finance/Cyber 已经共享 candidate→dense ranking→auditor 重新计算 proof→audit
   路径，共得到 6 个 local-probe diagnostic receipts；这里的“重新计算”表示独立
   代码路径，不表示组织或 production trust 独立。candidate 声明 proof 字段会被
   直接拒绝。当前 receipt 诚实标记 artifact-aligned window scope，generic
   token-offset gate 保持失败，因此 promotion 在 12-world selection 之前就已阻塞。
3. 从 verified tag refs 构造真实 release cycles，并建立持久、内容寻址且失效时回退
   full replay 的 reference index；缓存只能加速，不能成为审计事实来源。
4. 补足 Deno/Ruff/Oxc/Microsoft 的真实历史和缺失 band，累计至少 12 个完整、
   source-bound、executable worlds。
5. 重跑 12-world 统一 promotion，并同时满足零复制/随机拼接、真实 64K retention、
   source relation、proof/retrieval/semantic-growth 和 production trust gates。
6. 只有这一步通过后才创建正式 HF train release；48/210 继续保持阻塞，不以 candidate
   数量或 CPT rows 替代 production-qualified world 数。

Git-history license-binding v2 已逐 commit 检查 canonical path/blob，并把历史
allowlist 整体 digest 作为受保护 approval pin、当前仓库 entry digest 作为必要身份。
生产审计拒绝 legacy v1--v4 降级；但 auditor 尚未独立重放 commit/tree object 到
license blob 的 Merkle 路径，因此新输出只能标记为 producer-executed local probe，
不能称作 strict independent replay 或 production promotion。

最终发布声明应同时列出 CPT/SFT、candidate/promoted/production 三组互斥计数。
“内容门禁通过”“本地可诊断训练”和“正式可训练发布”是三个不同状态，任何一个都不能
替代另一个。
