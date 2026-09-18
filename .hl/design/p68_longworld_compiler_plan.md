# P68 — LongWorld 数据编译器：总体规划与目标（草案 v1）

Status: planning. 基于三份外部评审（含对 `65e4d0e` 的源码审查）、我方
四轮评测、以及本机复核。联网调研结果到达后更新为 v2。

## 0. 本机复核记录（2026-09-18）

外部评审的 P0/P1 断言全部在本树（领先其 10 commit，未动合成模块）独立复现：

| # | 断言 | 复核结果 |
| --- | --- | --- |
| P0-1 | `capability_rules_workflow` 的 `solve_visible()`/`validate_bundle()` 不校验自然语言 `prompt`/`protocol` | **确认**。篡改探针：把任务说明改成"返回空数组、不计算标签"→ validate 仍通过；把协议初值 x=y=0 改成 x=y=1（正确答案应切换）→ 仍通过。两个函数的源码均不含 `prompt`/`protocol` 字符串。 |
| P0-2 | ResearchLab reject schema 不一致 | **确认**。materialize 写 `admission_reason`（:289），统计端读 `row["reason"]`（:401）→ 一类拒绝触发 KeyError。 |
| P0-3 | 多问序列化使后答案可见前答案 | **确认**。records 带 `partition` 字段、答案在单 assistant JSON 中按 q000→q001 顺序输出；workflow 族 v_i=((v_{i-1}+p_i)m_i) mod p，partition 1 的 final_value 可从 partition 0 的答案直接续算，无需读原始记录。 |
| P0-4 | `graft_artifacts` 为占位 | **确认**。`longworld/core/graft.py:8`——`enabled=True` 也直接返回输入。 |
| P1 | L4 实为单一规则族（252 组系数） | 确认（系数组合≠规则族多样性）。 |
| P1 | 长度增长＝记录条数增长（L 与 K/H 耦合） | 确认（`n_records` 二分到容量 90-100%）。 |
| 公开提醒 | `.local-probe-env`（HMAC probe 材料在 worlds 树+历史） | **已由用户决定保留**（合成需要）；风险已记录于 eval-readiness 文档。 |

## 1. 总体目标（GOAL）

**把 LongWorld 从"世界模拟器附带几个问答"重构为"共享语义底座上的长上下文数据编译器"。**

可量化的长期目标（draft，调研后修订）：

- **G-D1 多样性**：任一 ~5K 行合成语料，answer-shape 唯一率 ≥60%（ACC 实测
  94%，P64 为 3.6-8.1%）；instruction 唯一率 ≥80%；单源文档曝光 ≤ 每文档
  50 行任务（P64 实测 989×，这是塌缩主因之一）。**[D 证据修订]** 监督 token
  占比不是设计变量（ACC 0.122% 成功 / LT 0.004% 未塌 / P64 0.057% 塌）；
  epoch 也不是（ACC 4 epochs 成功）。判别变量=shape 唯一率+行/文档比。
  **[A 证据修订]** 文献无 answer-熵目标——我们的 output-shape entropy 是
  原创指标；门=bank 级 shape 分散度 + per-doc 曝光上限 + TableLong 0<P<1
  带（N 次 rollout，P=0 弃/P=1 弃/0<P<1 留）。
- **G-D2 真实长依赖**：每行通过干预验证——改/删必要正文 span → 答案必变；
  等价支持的删除要删全部等价物。L（长度）与 K（必须读取量）/H（推理深度）
  独立采样，不再用 `n_records` 一根轴推全部。**[C 证据修订]** 六轴 (L,D,K,
  H,R,M) 中 D 已有先例（2405.17915 的 DDI，必须引用）；干预验证的成本阶梯
  已分级（窗口消融→LOO→随机化→ROUGE→语料级 DST），我们已做到第 2 级，
  升级路径明确；NoLiMa ROUGE 词法重叠协议（R-1 0.069 vs NIAH 0.905）应
  加入 audit——从"宣称"变"测量"。
- **G-D3 可批量**：一次源解析 → N 任务复用（Spark 式六阶段：source_prepare
  → semantic_index → task_expand → solve_filter → render_compose →
  audit_export）。昂贵 LLM 调用只出现在 render 层且幂等缓存。
- **G-D4 训练契约分离**：midtrain（NTP/目标区段）/ reader SFT（回答监督）/
  agent RL（闭环）三条出口，不混监督、不共享 loss mask 约定。
- **G-D5 诚实标注**：`strict_long_dependency_verified` 等旗标必须由真实验
  证置位，`unmeasured` 不得默认 True；外部（非我方）测试集命名清晰。

## 2. 已验证的数据事实（盘点）

**已在本机、未训练的后续波次**：
- `p66_multidomain_candidates`（cyber 18 / finance_disclosure 18 /
  codeforge 96 训练行）——多样性显著好于 P64（shape 56% vs 8%），
  但规模太小（132 行）且**若按 680×GBS16 预算将产生 82 epoch**——
  同样的曝光灾难。
- `p65_govinfo_taskbank_v2` 已物化（7 长 SFT 行 + 21 短）。
- 本地重建 `p66_researchlab_taskbank_v1`（47 行，shape 19%，231 epoch
  同灾难）。
- `p66_ietf / p66_macro_bea` 仅有 config，未物化。
- `capability_curriculum_v2`（288 行，4 族，shape 87.5% 唯一）——多样性
  最好的合成资产，但 100% 长、无短锚。

**核心教训（P64 事故已归因）**：response-prior capture——11 文档×989×曝
光 + 47 答案模板 + 0.057% 监督 token → 模型对一切问题输出 finance JSON
（PPL 完好 2.98，知识未损，输出先验被捕获）。详
见 [[longworld-p64-diagnosis]] 与 `.hl/design/p67_longworld_v2_synthesis.md`。

## 3. 架构方向（待调研证实后定稿）

```
真实文档 / 表格 / 代码 / 对话 / 模拟事件 / 规则示例
                    ↓
          Source & Observation Registry（版本冻结、来源、许可）
                    ↓
     语义抽取：事实/规则/事件/表格/span（结构化解析+缓存LLM）
                    ↓
      TaskSpec + 类型化操作库（LOOKUP/FILTER/JOIN/GROUP/AGGREGATE/
        AS_OF/APPLY_RULE/INFER_RULE/TRANSFORM/CHECK_SUFFICIENCY）
                    ↓
        执行 + provenance + 非退化检查（CPU 执行器）
                    ↓
     renderer（注册制：JSON/表格/自然记录/邮件/工单）+ 语义一致性校验
                    ↓
       上下文组合 + 干预控制（紧凑证据/完整/错误/缺失）
                    ↓
      midtrain / reader SFT / agent RL 三出口（不同 loss mask 契约）
```

**优先修复顺序（P0 契约先行，不先扩数据）**：
1. `capability_rules_workflow` 绑定 prompt/protocol（篡改探针进测试，
   我复现的两种篡改必须被拒绝）。
2. ResearchLab 统一 reject schema。
3. 多问输出改为两种显式契约（独立 reader / 联合+依赖标注）。
4. 训练入口读取 manifest eligibility（未合格候选不得仅凭文件名消费）。

## 4. 调研输入

### 4.1 Agent A 已返回：L2-L4 合成架构（2026-09-18）

**最重要的负结果：answer-space 多样性在整个文献里是空白。** π²、LongCrafter、
TableLong、ProofWriter 均不度量答案分布熵——只有 π² 要求"deterministic,
unique, short"。我们的 output-shape entropy 指标（P67 §4.2）是原创贡献，
47-shape 发现没有可抄的先例。可借的两块料：**TableLong 的 0<P<1 通过率带**
（N 次 rollout，P=0 丢弃=歧义、P=1 丢弃=平凡、只留 0<P<1——唯一已发表的
answer-side 机制，且自带难度过滤）和我们已有的 shape 掩码约定。

**L2 生成器装配方案（A 的推荐，已采纳为 G-D3 主体）：**

| 组件 | 来源 | 作用 | 证据 |
| --- | --- | --- | --- |
| 验证脊柱 | **TableLong** (2603.21719) | SQL 执行器出 gold，零 LLM 成本，任务即 L2（filter/aggregate/JOIN over 数百 cells） | cells 0~30→0~300+ 提升 46.30→48.36；tables 1→1~30 提升 46.66→48.36 |
| 控制律 | **iGSM** (2407.20311) | `ip`（文本参数数→长度）与 `op`（必需运算数→难度）双旋钮独立采样；先建必要子图再填充不必要参数 | med 模板 ≥77B、hard ≥90T；模板哈希防污染；解法 parser 即验证器 |
| 多样性先验 | **LongCrafter** (2607.06160) | 32 类型分类学（12 浅+20 深）× 最小充分证据图（节点=带引用 span，边=依赖） | 去掉证据图 −12.6 分；低多样性 −7.7 分；位置鲁棒性优势显著 |
| 防退化 | **TableLong 0<P<1 带** + CLUTRR 均匀标签 | 分类式标签空间（CLUTRR 22 关系均匀采样）天然抗模板塌缩 | CLUTRR K-way 分类"避免了问题泄露答案" |

**结构对照表（我们的映射）**：TableLong 的 cells-per-task ≙ 我们的 K（必须
读取量）；iGSM 的 op ≙ H（推理深度）；CLUTRR 的 paraphrase/clause 双轴
holdout（留 20% 释义+10% 逻辑子句）≙ 我们 G-D5 的未见组合切分。

**编译-验证双半环（A 的关键区分，已采纳）：**
- **语义半环**（π² 双路径：SQL+Python 共识才保留）只保证 gold 对，**不防
  我们的事故**——两个执行器在简单标量读取上最易共识，恰好就是 47-shape
  finance 族。π² 连过滤保留率都没公布。
- **多样性半环**才抓我们的失败，且几乎免费：① shape 分散度门（bank 级，
  ~40 行，P64 finance 3.6% vs ACC 94% 已实测）② per-document 曝光上限
  （989×→硬上限）③ loss-margin 探针（带/不带文档的 gold NLL）④ 0<P<1 带。
  **执行顺序 ①→②→④：先在现有 P64 语料上跑 ①②，零合成成本。**

**不可照搬**：π² 用 Instruct 底座（我们 Base 缺 chat 接口）、922 样本即训
（我们 5.57 epoch 记忆区间）；TableLong 的 GRPO×group16×64×H20 与 4k-16k
训练长度都不是我们的 regime——**但它的数据半边（SQL 执行）完全可迁移且便宜**。

**对 A 简报的三处事实修正（已吸收）**：TableLong 不是"短表扩展成长文"而
是拼接 1-30 张真表+行主序线性化；iGSM 无 distractor 旋钮（解耦是结构性的
ip/op）；"GSM-Infinite" 不是可查的论文（只是 TableLong 表格里的列名）。

### 4.2 Agent D 已返回：训练契约与配比（2026-09-18）

**改写前提的测量（本机复核，精确复现 agent D）**：用 Qwen3.5 实分词器
测的监督 token 占比——ACC **0.12183%**、LongTrace **0.00405%**、
LongMit 0.20037%、P64 0.0572%。**监督占比不是塌缩变量**：LongTrace 比我们
低 14 倍没塌，ACC 只比我们高 2.1 倍就成功。且 ACC 实跑了 **4 epochs**（43,080
样本 vs 我们 10,880）——epoch 也不是解释变量。判别性变量回到：
shape 唯一率（100% vs 3.6%）与 行数/文档数（10,770 行 vs 11 文档×989×）。
本地 ACC 副本 = 论文数据集（10,770 = 10,802−32 val，manifest 来源计数吻合）。

**配比证据**：
- **Nemotron 3 Nano §2.5**（最强单点）：LC-Phase 121B token，先只用 512K
  序列→短任务受损；混入 4K 序列后**短长都改善**。混比 20% docQA / 1% 合成
  检索 / 79% 前段数据。**短锚在 midtrain 阶段就是 load-bearing**。
- **Qwen2.5-1M**：75%-at-max 是**预训练**规则；两阶段 SFT（先纯短≤32K、
  再混合）属实，但 MMLU-Pro/IFEval 数字是 128K-vs-1M 对比而非 SFT 消融。
  RL 用**纯短对（≤8192）**——短锚不因进入 RL 消失。
- **ProLong Table 8**（CPT 侧）：合成长数据 0% 55.7 / 1% 54.1 / 50% 43.3
  ——已发表的害阈值 1%。**LongWriter 2408.07055**（SFT 侧）：6k 长输出行
  /180k 总 = **~3.2% 是唯一找到的工作比例**。锚量下限 ~1k 行（Dong 2310.05492）。
- **ACC 未发表的关键差异**：π²/LongCrafter 都用 Instruct 底座；我们的 Base
  缺 chat 接口——ACC(0.122% sup) 证明数据形态可比配比更重要。
- **无证据支持 natural-midtrain→task-midtrain 的分阶段**：NExtLong/ICLM 都是
  纯 NTP 且是其设定下的 SOTA；"task-conditioned midtrain 优于纯 NTP"是
  空白——正是可发表声明所在（P67 §6 一致）。
- **格式混合**：无论文给出目标比例；原则是 SFT 的格式范围=后续一切的上限
  （2501.17161 §5.4），RL 在 SFT 后引入跨格式脆性（2509.20866）。
  **LongWriter 证明答案长度分布是独立设计轴**：滤掉 >500 词输出→天花板 ~600
  词；我们的 ~56 字符答案训练不出长答案。
- **否定结论**：0.05% 监督占比 regime 并非已知有害；无任何论文公布监督
  占比作为设计变量。

### 4.3 Agent C 已返回：长依赖验证（2026-09-18）

**最重要发现：arXiv 2405.17915（ACL 2024，"Long Context is Not Long at
All"）两年前就发表了我们的失败模式与解法。** LDS 三因子分解：
- **DST**（依赖强度）= (PPL(c_i)−PPL(c_i|c_j))/PPL(c_i) —— **就是 leave-
  one-out 干预**（删段 c_j 看 c_i 损失变化）；
- **DDI**（依赖距离）= (i−j)/(N−1) —— 就是我们的 D 轴；
- **DSP**（特异性）= softmax ΔPPL 的熵惩罚。
保留 top 50%：**KV 检索 300 对 86.0 vs 全量 59.5——50% 筛选数据胜过 100%
未筛**。OPT-350m 打分、Llama2-7b/13b 训练（跨模型有效）。这是唯一一篇
"干预验证数据上训练 vs 未验证对照"的受控实验。**必须引用而非当原创。**

**(L,D,K,H,R,M) 六轴无人发表**；最近的是 2405.17915（三轴）与
100-LongBench（2505.19293，命名了 base-能力/长文能力混淆）。我们的 K/H/R
无文献先例——H 对应 iGSM 的 `op`、K 对应 BABILong 的 facts-per-task
（qa1 = 2-10 事实中 1 条相关）。

**干预验证五级成本阶梯**（升序）：① 截断/窗口消融（Ada-LEval §4.5.4）
② **leave-one-out span 删除+答案必变**（ContextCite §C.4 称之为 oracle 且
指出为何少人做：每源一次推理；我们的 `remove_one_fails` 已是此级）③ 位置
随机化（NoLiMa/DCDS）④ 词法重叠 ROUGE 协议（NoLiMa Table 1：R-1 0.069
vs NIAH 0.905——衡量而非宣称）⑤ 全语料损失差（2405.17915 的 DST）。

**本仓库现状（C 读码结论，与我的复核一致）**：`taskbank_dependency_audit.py`
已实现部分分解且**诚实拒绝越权**——`_coverage()` 算 4K/8K/16K 窗口即 L 轴+
窗口消融，`span_width`=D，`len(evidence)`=K，且 `strict_long_dependency_
verified=False` 硬门在四个 prepare 脚本中 fail-closed。**我们的 13 门套件
在确定性程序 oracle 层面强于已发表的任何东西**——缺的是测得的距离轴、
新颖性 R 轴、状态容量 M 轴，以及把"no LiMa-style 词法重叠协议"补上。

**对我们的直接修正**：NoLiMa 的重叠是**测量的**（ROUGE 精度表），我们的
捷径检查是宣称的。把 ROUGE 协议加进 `audit_*` 是低成本高可信改进。

### 4.4 Agent B 待返回

多样性度量与质量门。其范围已被 A（answer-side 空白+TableLong 0<P<1 带）
与 D（配比证据）部分覆盖。

## 5. 训练实验设计（沿用 P67 §4.2，并入外部评审三实验）

- 实验一（机制 vs 表达）：操作难度 × 表达形式 2×2。
- 实验二（长度 vs 计算量）：紧凑证据 / 长干扰 / 更多必读 / 更深依赖。
- 实验三（midtrain vs SFT 贡献分解）。
- P67 的 A0-A5 消融（ckpt200 先评——仍未跑）。

**训练纪律**：不再用固定 680 步；按任务级曝光与实际 epoch 记录 checkpoint
曲线；P64 语料保留为失败对照。
