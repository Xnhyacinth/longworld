# P69/P70 — LongWorld 长文合成数据配方（可执行版）

Status: planning contract. 发给 compiler implementer 的施工图。
上游：`.hl/design/p68_longworld_compiler_plan.md`（架构与门）、
`.hl/design/p67_longworld_v2_synthesis.md`（§3 配比 / §4.2 消融）。
约束：`.hl/policy.md`、`.hl/failed_directions.md`、`.hl/regressions.md`。

**证据标注约定**：数字后标 (文件路径) = 本机读到的；标 (实测) = 本轮本机跑出的；
标 (目标) = 本配方设定的目标值；标 (假设) = 未验证的假设；标 (调研结论) = 来自
P68 §4 三个调研 agent，非本机实测。**不含任何未标注的引用。**

## 0. 三件必须先知道的实测事实

1. **门的口径比 P68 目标松。** `scripts/measure_sft_collapse_predictors.py:225-257`
   的实际 floor 是 shape_uniqueness ≥0.10、per-document exposure ≤50、
   top_instruction_share ≤0.10、shape-exposure ≤10；P68 G-D1 的目标是
   shape 唯一率 ≥60%、instruction 唯一率 ≥80%。**P69 必须同时满足两者**——门是
   fail-closed 的下限，60% 是设计目标。
2. **`mask_shape` 把一切带引号的串和数字折叠成 `"S"`/`"N"`**
   (`scripts/measure_sft_collapse_predictors.py:33-36`)，键名也被折叠。
   因此**固定 arity 的集合型答案在掩码后只有 1 个 shape**。答案形状多样性
   必须来自**结构性自由度**（键数、嵌套、arity、接口形式），不能来自取值。
   这是本配方所有多样性设计的硬前提。
   **量化后果（对所有族的统一设计律）**：gates 要求唯一率 ≥0.10 **且** G-D1
   目标是 ≥0.60。一个 120 行的族 ⇒ 至少 **72 个结构性 shape**。
   "4 种接口 × 4 档 K" 只给 16 ⇒ **每格必须再有 ≥5 个独立模板**
   （例：模板 = answer-program 形状，不是措辞）。**不满足这条的族，P69 实测
   唯一率会落在 20-30%，过 floor 但达不到目标**，须在 P69 报告里分列两个数。
3. **打包多问会使 instruction 指标失效。** `instruction_line()` 取
   `^Task: .*$`，取不到就取 user 末行 (:39-43)。`capability_curriculum_v2`
   288 行实测 `distinct_instructions=1`、`top_instruction_share=1.0` (实测)。
   ⇒ **P69/P70 默认一行一题**；多问打包只作为 ≤20% 的配对第二长度视图
   (`policy.md` scale_unit)。

## 1. 能力 → 任务族 → 评测对齐总表

能力层级沿用 `longworld_capability_reassessment.md:12` 的 L1–L5 定义
（定位/绑定、整合、推理/状态、上下文学习、持续执行）。L5 不在本轮 SFT 范围。

| 能力 | 任务族（P69/P70 合成对象） | 类型化操作 | 检测它的评测 | 现有生成器覆盖 | **缺什么** |
| --- | --- | --- | --- | --- | --- |
| **L1 定位/绑定** | F1 多目标别名召回（有序、含混淆实体） | LOOKUP + DEDUP | MRCR 2/4-needle（hash-prefix grader，`scripts/eval_vllm_mrcr_graphwalks.sh:73`）；NoLiMa 式低词法重叠控制 | `capability_curriculum.py` `recall` op；P64 finance scalar-off-a-subgraph 449/1296 行 (P67 §3.2a)；cyber 精确 ID 集合 36 行 | 别名表由生成器直接给出，**无"语义别名须从上下文推断"的难度轴**；仓库内无 ROUGE 实现（grep 零命中），NoLiMa 协议**未测量** |
| **L1** | F2 集合完备性（≥8 标识符、无序、exhaustive） | FILTER + DEDUP | set-completeness probe（§2 自建）；cyber 已有 scoped certificate | cyber 36 行；codeforge list 答案 60.2% 逐项 verbatim (P67 §1.4) | 无"漏项即错"的**完备性 gold**；无漏项惩罚的评分口径 |
| **L2 全局整合** | F3 跨文档 JOIN | JOIN | GraphWalks Parents+BFS **precision**（非 F1，`eval_readiness_20260917.md:160-164`） | 无 | **完全没有跨文档 JOIN 生成器**；现有"多文档"只是拼接背景 |
| **L2** | F4 分组聚合 GROUP+AGGREGATE | GROUP + AGGREGATE | Oolong 式 per-fragment 决策 + 全局分布 (调研结论, `longworld_capability_reassessment.md:151`) | `capability_curriculum.py` `aggregate` op（per-entity，非真分组）；finance v2 `conditional_cross_metric_sum` | 分组键须**从上下文读出**而非生成器给定；**无 per-fragment 中间监督**——三个现成 oracle 的 gold 都只给**终答**（`longworld/synthesis/capability_curriculum.py:101` 的 `_oracle_answer` 返回单个答案对象），本条据此新增 |
| **L3 推理/状态** | F5 AS_OF 版本冲突（fiscal valid-time / availability / source-version 三时间区分） | AS_OF + COMPARE | state replay；latest-only 反例行（`latest-only` 是命名控制，无独立 harness） | `longworld/core/finance_disclosure_versions.py` —— 34 行已物化 (`.hl/p65_execution.md:49`) | 三时间轴只在一个家族里出现；workflow 族的回滚不可执行（`policy.md` soft_gates：`every task having CF` 只是软门，且 P68 P0-4 记 `graft_artifacts` 为占位） |
| **L3** | F6 状态重放 + 独立多问契约 | AS_OF + TRANSFORM | `capability_curriculum` `state`/`active` + 独立可见解释器重放（108/108，`reports/capability_pilot_20260913.md:100`） | ledger/reservation 两族 | P68 P0-3：多问序列化让后答案可从前面答案续算；**独立 reader 契约尚未落地** |
| **L4 规则学习** | F7 未见组合规则 | APPLY_RULE + INFER_RULE | `scripts/build_unseen_eval.py` 的 `topology_operator` / `source_document_family` 轴（`longworld/core/unseen.py:29-34`）+ 未见系数转移 | `longworld/synthesis/capability_curriculum.py` 与 `capability_rules_workflow.py` 各有一族 rule_learning（**≤2 族**） | P68 P1 已确认 L4 实为单一规则族（252 组系数）；无 legal-unknown 例；**P67 §7 开放问题 3 指出 held-out rule transfer 尚未测量** |

出口不在本节：**自然长文档 NTP midtrain** 是 G-D4 的独立出口（§3.2），不是能力族。

## 2. 逐配方（每族一张卡）

字段固定：来源数据 / 生成管线（六阶段，P68 §1：source_prepare → semantic_index →
task_expand → solve_filter → render_compose → audit_export）/ gold 机制 / 控制轴
(L,K,H 为 L 长度、K 必读记录数、H 推理深度) / 多样性机制 / 干预验证 / 规模目标
(P69 长行，按 §4 的 640 长行分配) / gate 预期。

### F1 多目标别名召回（L1）
- **来源**：`data/hf/LongWorld-Synthesis-Workspace/source_inventory/p57_ietf_*_family_v1`（7 族，RFC 正文）、`p13_company_*_annual_v*`（10-K）、`wikimedia_p7_*`。
  （`p13_company_microsoft_four_annual_v1_superseded_untrusted` 与
  `..._v3_superseded_root_trust_local_diagnostic` 两个 `*_superseded_*` 变体
  **不入选**——`regressions.md` 明令不得复活历史 trust 根。）
- **管线**：source_prepare 切 hunk/条款；semantic_index 建"别名→有序记录"表；task_expand 出 N 个目标；solve_filter 走 CPU 执行器 oracle；render_compose 注册表渲染为"邮件/工单/合同"三种 register；audit_export 写 provenance span。
- **gold**：oracle 出有序记录 ID；provenance span = 每条记录的原文偏移（复用 `longworld/core/taskbank_context.py` 的 `context_start/context_end`）。
- **控制轴**：K ∈ {4,8,16,32} 目标数（**采样**，不与 L 耦合——这是 P68 §0 P1 缺陷的直接修复）；L ∈ {64K,128K,256K}；H=1。
- **多样性机制**：≥24 条 instruction phrasing；答案 **arity 必须随 K 变化**（§0.2）；接口轮换 4 种（JSON 对象 / 纯 span / `Final Answer: [...]` / prose+答案）；别名表本身在 20%/80% 两档"上下文可推断 / 直接给出"之间采样。
- **干预验证**：remove-one（删任一目标记录 → 答案必变，`remove_one_fails`，`longworld/core/verify.py:56`）；窗口消融 4K/8K/16K（`taskbank_dependency_audit.py:42` `_coverage`）；**新增 NoLiMa ROUGE 协议**（当前缺口，P68 §4.3 明确要求从"宣称"变"测量"）。
- **规模目标**：P69 长行 120（**目标**）。
- **gate 预期**：按 §0.2 的设计律，需要 ≥72 个结构性 shape；本卡标称的组合
  （4 K 档 × 4 接口 × 2 别名模式 = 32）**只够 27% 唯一率**，因此模板维度必须
  补齐到每格 ≥5 个。top_instruction_share 目标 ≈ 1/24 = 0.042；rows/doc ≤33。

### F2 集合完备性（L1/L2）
- **来源**：`p12_wave3_cyber_log4shell_v1`、`p66_cyber_osv`（若可用）、`p12_codeforge_deno/dprint/oxc`（release-cited PR 集合）。
- **管线**：同 F1，但 task_expand 产出**穷尽集合**任务；solve_filter 计算全集并要求 |gold| ≥8。
- **gold**：全集 ID 列表（排序规范化），来自结构化源记录，非 LLM 生成。
- **控制轴**：K = |全集| ∈ {8,12,16,24}；L ∈ {64K,128K}；H=1。
- **多样性机制**：集合**无序呈现**并在 50% 的行里打乱答案顺序（迫使按内容而非位置记忆）；漏项型干扰项（同 repo/同 CVE 的兄弟项）按 30% 比例注入。
- **干预验证**：remove-one 删任一成员；**加一**（把兄弟项若注入正文而不应进入答案）；窗口消融。
- **规模目标**：P69 长行 120（**目标**）。
- **gate 预期**：arity 4 档 × 顺序 2 档 × 接口 4 种 = 32（27%）；**须补键数/嵌套
  自由度才够 60%**——这是 F2 的主要设计风险，P69 必须实测后再放大。

### F3 跨文档 JOIN（L2）——**全新**
- **来源**：需要"两个文档族共享一个外部键"。在盘上可用：`p13_company_*_annual_v*`（同一 CIK 跨年）× `p57_finance_*_ir_fy*`（issuer IR）；`p12_codeforge_*`（repo × release-tag）；`p57_ietf_*_family_v1`（RFC × 引用它的 draft）。
- **管线**：source_prepare 解析两侧键；semantic_index 建键索引；task_expand 出 JOIN 任务；solve_filter 用 CPU 执行器做真实的键匹配（不调用 LLM）；render_compose 把两侧放在同一上下文的不同区段；audit_export 记录两侧 provenance。
- **gold**：执行器的匹配结果（集合），零 LLM 成本——对标 P68 §4.1 采纳的 TableLong"SQL 执行器出 gold"。
- **控制轴**：K = JOIN 命中数 ∈ {3,6,12}；L ∈ {128K,256K}（两侧必须各占 ≥16K 才排除单窗口解）；H=2。
- **多样性机制**：INNER/LEFT 语义轮换；键的呈现形式（CIK vs ticker vs 文件名）；答案 arity 随命中数。
- **干预验证**：remove-one **必须删两侧同一键的两处**（否则只测单侧）；这是本卡最易做错的地方。
- **规模目标**：P69 长行 80（**目标**）——**最小**，因为这是唯一没有先例的族。
- **gate 预期**：80 行需要 ≥48 个结构性 shape；命中数 3 档 × 语义 2 档 × 接口 4 = 24
  ⇒ 同样要靠 **answer-program 形状**（"列出命中键"vs"列出配对"vs"给出差异"）
  补齐。**风险最高**：若唯一率 <60%，先修形状程序再增量，不做规模放大。
- **前置依赖**：JOIN 的两侧必须在同一上下文的不同区段且各 ≥16K（否则单窗口可解），
  这一条 P69 要显式测（`taskbank_dependency_audit.py:42` 的 `_coverage` 用
  4K/8K/16K 三档，恰好覆盖）。

### F4 分组聚合（L2）
- **来源**：`p57_finance_*_ir_fy*`（多 issuer 多年度）、`p59_finance_*_reconstruction_v1`、`p57_ietf_*_family_v1`（按 requirement 等级分组）。
- **管线**：扩展 `capability_curriculum.py` 的 `aggregate` op；**分组键从上下文读出**（新增），不预先给出。
- **gold**：per-group 计数/求和 + 全局分组键列表。
- **控制轴**：K = 参与聚合的记录数 ∈ {32,64,128}；组数 ∈ {3,5,8}；L ∈ {128K,256K}；H=2。
- **多样性机制**：分组键的语义类型轮换（年度/segment/机构/requirement-level）；答案形状在 {dict-of-counts, list-of-(key,value), 嵌套} 三模板 × 组数间变化。
- **干预验证**：remove-one（删一条记录 → 某组计数必变）；**换组**（把一条记录改到另一组 → 两组都变）。
- **规模目标**：P69 长行 100（**目标**）。对标 P68 §4.1 的 TableLong cells 0→300+ 控制律：K 独立采样。
- **gate 预期**：组数 3-8 × 3 模板 × 接口 4 = 72-192 shapes ⇒ 60-160%，
  唯一率目标可达（这是本配方里机制最稳的 L2 族）。
- **额外要求**：per-fragment 中间标签**不进监督**（避免把推理过程当成答案形状），
  只作为 audit 的机器可验证项。

### F5 AS_OF 版本冲突（L3）
- **来源**：`data/candidates/p65_finance_disclosure_versions_v1`（34 行，已物化）、`p57_finance_*_ir_fy*`、`p13_company_microsoft_five_annual_v2`。
- **管线**：source_prepare 抽 fiscal valid-time / publication availability / source-version 三字段；task_expand 出 cutoff 任务；solve_filter 用 as-of 执行器（`longworld/core/asof.py`）。
- **gold**：cutoff 时点的可见值 + 该值当时的 source-version 标识。
- **控制轴**：cutoff 位置 ∈ 5 档（跨越版本切换点）；L ∈ {64K,128K,256K}；H=2。
- **多样性机制**：三时间轴**各自独立**成为 cutoff 基准（当前只在一个家族里做）；答案须同时给出值与版本，二者组合即 shape。
- **干预验证**：**latest-only 反例**（用最新版本回答 → 必错）；remove-one 删除切换点后的记录；窗口消融。
- **规模目标**：P69 长行 60（**目标**）。
- **gate 预期**：口径风险在 `policy.md` reading_contract——**不得把 source-computed 数值答案算成"非文本阅读"**。本卡的形状自由度最小（值与版本的二键对象在掩码后只剩少数 shape），60% 目标**须靠接口与键数自由度补齐**，否则降级为 L3 探针而非规模族。

### F6 状态重放 + 独立多问契约（L3）
- **来源**：合成世界（`longworld/synthesis/capability_world.py`，ledger/reservation 语法），**不依赖外部源**。
- **管线**：全部六阶段在合成侧；render_compose 新增 **独立 reader 契约**（P68 §3 优先修复 #3）：多问的输出拆成两种显式契约之一，不再让后答案可从前面答案续算。
- **gold**：oracle 状态轨迹；可见解释器独立重算（已验 108/108）。
- **控制轴**：M（状态容量）= 事件数 ∈ {200,800,3200}；K = 问到的实体数；H ∈ {1,2}。
- **多样性机制**：字段名（`period_ends`/`unit`/`value` 这组 P64 finance shape 必须**禁用或大幅降权**）；答案长度分位（P67 §4.2 LongWriter 结论：答案长度分布是独立设计轴）。
- **干预验证**：P68 §4.3 五级阶梯的第 2 级（leave-one-out span 删除 + 答案必变）已具备；**升级到第 4 级**（ROUGE 词法重叠）。
- **规模目标**：P69 长行 100（**目标**）。
- **gate 预期**：`capability_curriculum_v2` 实测 shape 50.7%、shape-exposure 74.5 过不了门 (实测)；本卡必须先把 shape 程序改对再扩容。

### F7 未见组合规则（L4）
- **来源**：合成（`capability_rules_workflow.py`），**不依赖外部源**。
- **管线**：task_expand 引入**新的规则族**而不只是新系数——P68 P1 已确认当前 252 组系数是同一族。新增：非仿射算子（阈值/取模/条件分支）、规则歧义例、legal-unknown 例。
- **gold**：生成器系数 oracle；标签空间按 **CLUTRR 均匀标签** 构造——分类式标签数量固定且均匀采样，使"问题不泄露答案" (调研结论, P68 §4.1)。
- **控制轴**：R（规则新颖度）= 同族未见系数 / 未见算子 / 未见组合 三档；K = 演示数 ∈ {4,8,16}；H=2-3。
- **多样性机制**：标签数 K-way 固定（**分布**均匀，但 shape 仍只有一个——
  uniform-label 保证的是**答案值分布**不塌缩、以及"问题不泄露答案"，
  **不贡献 shape 多样性**，P69 报告里不得把它记为 shape 成绩）；
  shape 来源是标签数 × 接口 × ORACLE 形状；instruction ≥24 phrasing。
- **干预验证**：删任一演示 → 规则不再唯一可定；**未见组合切分**必须走 split 隔离（coefficient-instance overlap 不等于结构族泛化，
  `docs/CAPABILITY_CURRICULUM.md:94-97` 已写明）。
- **规模目标**：P69 长行 60（**目标**）。
- **gate 预期**：标签空间均匀 ⇒ 答案**值**分布不塌缩（P68 §0.1 的"监督 token 占比
  无法被排除"是另一个未分离变量，不是同一件事——不要混为一谈）；但 shape 只能
  来自标签数与接口 ⇒ 60 行需要 ≥36 个结构 shape，**不得**用均匀标签冒充 shape 成绩。

## 3. 数据源清单与角色

`data/hf/LongWorld-Synthesis-Workspace/source_inventory/` 共 **130 个家族**，
**1.8 GB** (实测 `du`)。按族计数：wikimedia 28 / ietf 22 / codeforge 20 /
company 13 / finance 12 / paper 11 / arxiv 7 / sec 8 / cyber 2 / clinical 1 /
regulation 1 / govinfo 1。`candidates/` 59 个目录，按域：ietf 38 /
govinfo 9 / finance 6 / codeforge 3 / researchlab 3。
`configs/` 453 个，按域：codeforge 94 / finance 57 / wiki 50 / ietf 44 /
researchlab 22 / sec 22 / govinfo 11 / macro 8 / cyber 6。

**每源曝光预算 ≤50 行/文档的来源与推导**：门口径 `exposure = effective_epochs × rows / distinct_docs ≤ 50`
（`scripts/measure_sft_collapse_predictors.py:169,230-236`），按 ≤1.5 epoch（P67 G2）
⇒ **rows/doc ≤ 33**。P64 实测 1953 行 / 11 文档 = 177.5 行/文档，
1.5 epoch 下 samples seen = 2,930 → **266 次/文档**（`data/sft/p64_primary_training_v2/sample_index.jsonl`；
P67 §1.3 的 ~989 对应的是它实际跑的 680×16 预算，不是 1.5 epoch 口径）。
ACC 的真实标定（**本轮实测**，`data/external/llamafactory/`）：
**acc_sql 1.046 行/证据块、acc_swe 1.000、acc_search 30.8（其最大单块 3,249 行）
—— search 子集自己就是单文档塌缩的先例，不要把 ACC 整体当作 1.02 的标定。**
swe 的仓粒度 = 8.77 行/repo（497 个 repo）。
**注意区分**：cap 是**文档粒度**指标，SQL 的 1.046 是**证据块粒度**；若 ACC 的
search 证据块真属于约 100 个文档，则文档粒度是 ~33 ——两种读法都要在 P69 报告里
分开写，不得合并成一个"1.02"。

| 来源族（盘上路径前缀） | 域 | 规模 | 当前用途 | P69/P70 计划 | 曝光预算 |
| --- | --- | --- | --- | --- | --- |
| `p57_ietf_*_family_v1`（7）+ `*_graph_v1`（7） | ietf | RFC+draft 文本，单文本 16-338 KB | 选型研究，未物化候选 | F1/F2/F4/F5 主力；自然长 NTP | ≤33 行/文档 |
| `p57_finance_*_ir_fy2022_2025_v1`（4：amazon/meta/micron/nvidia） | finance | 每族 ~19-57 MB，4 年 10-K/IR | P64/P65/P66 已用 | F1/F4/F5；F6 反面 shape 来源 | ≤33 行/文档 |
| `p58/p59_finance_*_reconstruction_v1`（4：meta/alphabet/micron/nvidia） | finance | 年报重建 | P58/P59 已用 | F4 分组聚合 | ≤33 |
| `p13/p14_company_*_annual*`（≈12，含 2 个 superseded） | finance | 19-95 MB/族 | P13/P14 已用 | F3 JOIN 的一侧 | ≤33 |
| `p12_codeforge_{oxc,ruff,dprint,deno,pulumi}` + signed | codeforge | PR 级 JSON | P64/P65 主力 | F2 集合完备性 | ≤33 |
| `p14_codeforge_wasmtime_release_cycles_v1` | codeforge | release 周期 | P14 已用 | F2/F3 | ≤33 |
| `p12_wave3_cyber_log4shell_v1`、`p13_cyber_cross_vendor_remediation_v1` | cyber | 2 族 | p66 cyber 36 行已入库 | F2 | ≤33 |
| `p65_govinfo_derivatives_v1` | govinfo | 1 族 | P65 有授权保留 (`.hl/p65_execution.md:54`) | **P69 不用**（授权冲突） | — |
| `p13/p14_paper_*`（9）+ `arxiv_p*_mlrc_*`（8） | researchlab/paper | 23-88 MB/族 | P13/P14/P24 | 自然长 NTP；F3 的 draft 侧 | ≤33 |
| `wikimedia_p7/p9/p10_*`（28 族） | wiki | 单条目修订链 | P7/P9/P10 已用 | 自然长 NTP；F1 别名 | ≤33 |
| `sec_p7/p10/p12_*`（8 族） | sec | 10-K/多文件 | P7/P12 | F3/F5 | ≤33 |
| `clinical_veklury_v2`、`regulation_p12_ftc_noncompete_v1` | clinical/regulation | 各 1 族 | 探针 | P69 不用（授权窄） | — |
| `macro_bea_*`（8 个 config，0 个 inventory 族） | macro | **未物化** | P66 held：无关关系块造长度 (`.hl/p66_execution.md:40`) | **P69 不用** | — |

**授权列（不可从 API 策略推断 license，`regressions.md`）**：每个源族自带
`authorization{record_id, scope, basis, allowed_actions}`（实例：
`p57_ietf_tls13_family_v1/ietf_fetch_inventory.json` 的 "IETF open-records research
export"；`p57_finance_meta_ir_fy2022_2025_v1/issuer_ir_inventory.json` 的
"User-authorized authentic source synthesis"），且 `production_eligible=false`。
**合成的行沿用其源族的授权记录；未列出的域不得凭空生成。**

### 3.1 短锚（SHORT ANCHOR）来源计划
- 需求算术。**两个基准必须分开写，混用会算错一个数量级**：
  - **基准 A = P67 §3.1 的原始算式**（1.5k tok/row，分母是 P64 的 258.9M 长
    token）：10% ⇒ **~17,000-19,000 行**。这是"若长行都是短文本"的假设。
  - **基准 B = 本配方的实际长度**（长行 128k/row）：分母变成 §4.2 的 640M
    ⇒ 10% 锚 = 64M tokens，30-40% ⇒ 192-256M tokens。
  - 短锚 starter **不吃 1.5k/row 假设**：DocQA-RL-1.6K 3,597 行（card 读取，
    预览 input_length 2.6k-19.5k）、LongAlign-10k 9,888 行（`length` 8.19k-65.5k，
    0% ≥64k；**尚未下载**，`.hl/findings.md:974-979`）。
    按 ~11k tok/row ⇒ 现有 13,485 行 = **~148M tokens ≈ 基准 B 的 23%**。
- **结论（诚实上限）**：按基准 B，两个 on-recipe starter **只能到 ~23%**；
  要冲进 30-40% 需再 fetch UltraChat 类（`download_external.sh` 无该条目）。
  若 P70 按基准 A 的更长行数（长行更短、行数更多）走，30-40% 需 40k-70k 短行，
  **更加不可能**。⇒ **P70 的短锚现实上限是 ~23%，30-40% 是须先扩容来源的目标，
  不是可以直接写的配比。** 这一条要在 P70 启动前显式裁决。
- 入口：`scripts/download_external.sh` 的 `DocQA-RL-1.6K|Tongyi-Zhiwen/DocQA-RL-1.6K`
  与 `LongAlign-10k|zai-org/LongAlign-10k`（该脚本 8 个条目，**除 ACC/LongTrace/LongMIT
  外均未 fetch**，P67 §4.2 E6 已记录；`data/external/` 现只有 `swift/` 与
  `llamafactory/` 两个目录）。
- **licence 列**：DocQA-RL-1.6K = `apache-2.0`（HF card，2026-09-18 读取）；
  LongAlign-10k = **card 上无 license 标签**（2026-09-18 读取）⇒ 标注为
  "未声明"，进口前须补授权判断，不得默认可用。

### 3.2 自然长 midtrain 源（NTP 出口，G-D4）
合格条件（`policy.md` / `regressions.md`）：**真实长文档本体**、
非视图拼接、非 padding、非 view 乘法。盘上合格：`p57_ietf_*_family_v1`
（RFC 原文，单族 0.2-1.5 MB 连续正文）、`p13_paper_aevb/gopher/gpt3/llama3/palm`
（论文全文本）、`sec_p7_amazon_v1`/`sec_p12_apple_v1`（10-K 全文）、
`wikimedia_p7/p9/p10_*`（单条目修订链的正文）。
**不合格**：`p40_ietf_oauth_semantic_growth_v*`（视图增长）、`p52_govinfo_*`（授权保留）、
任何 64/128/256K 区间重标。
**p57 IETF 族单文本容量核对**：最大单文本 `rfc8446.txt` 337,736 B (实测 `ls`)；
按 ~4 字符/token **假设** ⇒ ~84k tokens，**进不了 128K bucket** ⇒ IETF 只能作
64K 自然长源，**不得靠拼接兄弟 RFC 凑容量**。同理 `rfc5246.txt` 222 KB (~56k tok)。
诚实标注：natural-midtrain → task-midtrain 分阶段**无发表证据**
（P67 §4.2 否定结论）；本出口是**假设**，须与纯 NTP 对照跑。

## 4. 两个波次的量与配比

### 4.1 P69 — validation wave（**目标 800 行**，区间 500-1,000）
目的：在小 bank 上证明**门 + 编译器**成立，不是产出可用 SFT 语料。
组成：**长行 640 / 短行 160**（80/20）；长行分配 = F1 120 · F2 120 ·
F3 80 · F4 100 · F5 60 · F6 100 · F7 60（§2 各卡）。短行 160 = 外部锚切片，
只用于打通短导出路径。
**P69 的绑定约束是文档数（本文档最重要的一条）**：门口径是
`epochs × rows / docs`，800 行、1.5 epoch（samples seen = 1,200）时
**docs ≥ 24 ⇒ 曝光 ≤ 50**；docs = 11 时曝光 109 —— 正是 P64 事故的形状。
因此 P69 必须 **≥3 个域 × ≥8 个 group_id（合计 ≥24）**，且**按族分配文档而非按行**：
分配器读的是 group_id 而不是 rows，不满足就 fail-closed。

**验收检查（逐字执行）**：
```bash
# $L = bank 的长行数（目标 640；短锚行是 L 的 25%，在分母里）
# index 必须是整 bank（train+eval）的 sample_index；脚本只对 split=="train" 的行计数
# steps = ceil(1.0 × train 行数 / 16)        # P69 按 1.0 epoch 检查
# steps_1p5 = ceil(1.5 × train 行数 / 16)    # 训练上限口径，作为一个额外数据点
uv run --no-sync python scripts/measure_sft_collapse_predictors.py \
  --train  <bank>/train.jsonl \
  --index  <bank>/sample_index.jsonl \
  --steps 50 --gbs 16 --gate     # 640 长 + 160 短 = 800 train 行 → ceil(800/16)=50
```
**两个口径都要跑**：`--steps 50`（1.0 epoch）与 `--steps 75`（1.5 epoch）；
前者是提交判据，后者是红线检查。Bash 记 `$?`，**必须为 0**。
逐条 floor：shape_uniqueness ≥0.10、per_document_exposure ≤50、
top_instruction_share ≤0.10、shape-exposure ≤10（`scripts/measure_sft_collapse_predictors.py:225-257`）。
**并**须自报 P68 G-D1 目标值（shape 唯一率 ≥60%、instruction 唯一率 ≥80%）。
**诚实标注**：P69 按 **1.0 epoch** 提交、**1.5 epoch** 作为红线
（P67 G2 的 ≤1.5 是训练上限）。两者都读 `per_document_exposure_at_budget`，
不合并成一个数。
第二半：**同分布 held-out eval split**——用 `scripts/build_unseen_eval.py`
（轴 `world_entity`/`topology_operator`/`source_document_family`/`domain_composition`，
`longworld/core/unseen.py:29`），要求 eval 与 train **同族不同文档**，group 原子。

### 4.2 P70 — scale wave（**目标 5,000 行 + 短锚**）
Token 算术（**假设** mean context = 128k tok/row；P64 实测 mean 132,568、
p66 实测 151,947，128k 是偏保守的下游目标）：

| 项 | 值 | 来源 |
| --- | --- | --- |
| 长行 | 5,000 行 × 128k = **640M tokens** | 假设 |
| 其中配对第二长度视图 | 10-20% ⇒ 500-1,000 行 | `policy.md` |
| **语义任务数（记账单位）** | **4,000-4,500** | 派生 |
| 训练预算 ≤1.5 epoch | samples seen = 7,500 ⇒ **steps = floor(7,500/16) = 468** @ GBS16 | P67 G2 |
| 消耗 token | 7,500 × 128k ≈ **960M** | 派生 |
| 实测吞吐 | P64 消费 **1.37 G** ctx tokens / 16h50m（680 iter × 89.1 s/it）⇒ **~81M tok/会话·小时** | P67 §1.3；`.../v1-20260914-155129/logging.jsonl` |
| **墙钟代价** | 960M / 81M ≈ **11.9 小时**（同配置 4×H200 TP4/SP2，seq 262144） | 派生 |

**结论：步数必须按行数重推，不用 680。** 680 步是 P64 语料（1,953 行）的
**5.57 epoch**（10,880 / 1,953，P67 §1.3 实测）；照搬到 5,000 行只剩 2.18 epoch，
**仍然超过 1.5 的上限**，所以不是"残留"而是**仍然超标**——必须按行数重算：
**steps = floor(1.5 × rows / GBS)**。
计算量不是约束（<1 个 4 卡日，且小于 P64 的 1.37 G）；
**约束是源广度**：1.5 epoch 下
`docs ≥ 7,500 / 50 = 150 个 distinct group_id`（1.0 epoch 时，samples seen = 5,000
⇒ `docs ≥ 100`）。
**P70 的第一位需求是文档数，不是行数** —— 与 P67 §1.3"真正的差异因子是 11 个文档"
同源。

**60-70% 长 / 短锚 ~23%（按 token,2026-09-19 用户裁定:诚实降配比)**：
- **[P71 更新]** 30-40% 是扩源后的目标,不是本轮配比。现货两个 on-recipe
  starter 合计 ~148M tokens,对 640M 长分母即 **~23% 现实上限**;按
  ~11k tok/row 即 13,485 行。短锚扩源是独立后续任务,不阻塞实验冻结
  (A5 短锚修复验证臂相应缩小并如实标注)。**不得用"行数不够就重复采样"
  来凑比例**——重复 = 曝光,正是 `regressions.md` 禁止的那条。
- 配比四种口径分报(用户要求):按样本数 / 按输入 token / 按监督 token /
  按训练步(FLOPs)——少量长行可能支配监督 token,样本占比会误导。
- 阶段结构:stage 1 短-only ≤32K,stage 2 混合(Qwen2.5-1M 形状)。
  机制用 ms-swift 的 `dataset=path#count`,**不是** `write_b5w_v1_sampler`
  (那是 LLaMA-Factory B 路径,swift 拒收 B5w;P67 §4.2 E6 已纠正)。
  触碰 mix 即触碰 `scripts/prepare_p64_training.py:52` 的 `RECIPE` 冻结契约
  ——这是**管线契约变更**,不是配置改动。

**喂哪些消融**(P67 §4.2):**A3**(P64 shape-rotated,~1.0 epoch,shape ≥60%)
与 **A5**(A3 + 锚,锚占比按现货 ~23% 起算,扩源后回升)。A2 只在
ckpt200(A1')显示未塌缩时才需要。三臂共用本配方产出的 bank;
A1/A1' 用现有 P64 ckpt680/ckpt200 作对照。

## 5. 明确不合成什么

- **不合成 benchmark 格式行来"修"接口塌缩**（MRCR 前缀、GPQA 字母）——这是
  paper-over 一个已塌缩的 instruction-following 能力，CLAUDE.md VII 明令禁止
  （P67 §5 首条）。
- **不把 needle-copy 配额当能力杠杆**——NExtLong 零 needle 行到 100% NIAH；
  我方 LongTrace-Base 0.3686 vs 未训 0.3774（−0.0088，无增益）。仅保留
  ≤10-15% 少数族用于 grader 格式合规（P67 §5）。
- **不为纯时序关系编因果链标签**——`policy.md` reading_contract：不得把
  byte-hash 计算或未披露源标识当作纯文本阅读目标；缺记录不等于"未知"，
  legal-unknown 须由多个合法补全的答案分歧证明
  （`longworld_capability_reassessment.md:66-68`）。
- **不用 padding 当容量**——`policy.md` soft_gates 与 `failed_directions.md`
  反复点名（RFC 填充、pulse、unique leftover 撑 128k）；长度增长必须带
  proof-bearing 内容。
- **不把视图/长度乘法计成语义规模**——`regressions.md` 第 2 条；P64 已因
  989×/文档 塌缩。一个语义任务一个主长度 + 10-20% 配对第二长度。
- **不把 64/128/256K 数值区间落点当作强度证书**（P64 closeout 原话），
  也不把 `capability_curriculum` 的 coefficient-instance 切分当作结构族泛化。
- **不合成 P69/P70 未经干预验证的行**：`strict_long_dependency_verified`
  由实测置位，`unmeasured` 不得默认 True（G-D5）；三个 prepare 脚本的
  fail-closed 硬门保持不动。


## 6. P69 验证波生成记录（2026-09-18 实测）

- 银行：`data/capability_records/p69_validation_wave_v1/`（348 行 = 278 train
  / 70 eval；174 完整 shard；rejects 空；6 个 infeasible 单元如实记录于
  infeasible.json——join_lookup 在 8K/K=20 与 32K/K=60 的 L 不可行，是
  编译器诚实拒绝而非截断）。
- 门（`--steps 26` 即 1.5-epoch 预算）：**exit 0 通过**。硬指标：
  shape_uniqueness **0.8597**（P64 finance 为 0.036）、top3 覆盖 4.32%、
  278 条指令（1.0 行/指令）、最差任务脚手架 0.002、监督占比 1.48%。
- **held-out 独立复核：348/348 行的 assistant 答案由 solve_visible 从可见
  正文重算一致**（train 与 eval 都过）。
- 注意：shape-exposure 是预算函数——278 行的银行在 680×16 历史预算下为
  9.2 epoch（10.7 exposure），会触红线；P69 波的训练预算必须按行数重定
  （26 steps @ GBS16 = 1.5 epoch），这正是 P67 训练纪律条款的执行。

## 附录 A. P70 调研轮 1 机制摘要（2026-09-18，调研结论）

四篇全部原文核验（含 v2 交叉），两处前提纠偏：
- **ISG 的"五类不可答"不存在**：§3.2 只有一句四理由 + 两个有构造规则的机制
  （过滤器零命中、非连续变量取中位数）。五分类是难度分类学（§A.5），
  与不可答无关。勿按五类规划。
- **π² 的推理链无验证回路**：≤3 次重跑只守问题质量（Likert≥3/4/4），
  双路径共识只守答案；链本身从不被检查——LLM 中间步骤的幻觉被当
  "推理风格"教给模型。**不采纳自由文本链为训练目标**。

可执行采纳（带出处）：
1. **π² §2.1 自包含规则 → 词汇门**（调研结论）：prose 问题文本禁
   column/row/table/field/section/metadata 等容器词——机械可查。
2. **LongCrafter 引用格式升级为逐字子串断言**（`assert snippet in
   rendered_line`）替代其 LLM 评判——我们可机械验证，采纳 LLM 评判是倒退。
3. **ISG path-as-condition 编码**采纳（variant/L/K/H/族/seed 作路径段，
   免费获得 L1-locate 家族）；**不采纳** LLM 撰写的因变量函数。
4. **Oolong 反捷径**：我们的 group_compare 已实现目标裁决→构造实现
   （裁决均匀采样后由组规模/金额带实现，validate 重执行验证实现值）——
   **严格强于 Oolong**（其采样器未指定、实现值从不验证）。缺口是**测量**：
   receipt 需加 verdict_marginal + 多数裁决基线，否则 90% GT 的银行能过
   现有全部门。
5. **不可答构造**（比 ISG 更强的两世界补全式，对齐 G70-7 红线）：
   最廉价实例 = join_lookup 对"出现在记录但无 reference 行"的实体——
   补全 A 无参照行→不定；补全 B 参照行 amount=v→答案 v。
6. **数值答案禁部分计分**（Oolong 0.75^|Δ| 不采纳）：我们的任务是精确
   算术，near-miss 恰是过滤步骤错误的签名，部分计分会原谅要检测的失败。
