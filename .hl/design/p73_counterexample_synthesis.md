# P73 反例驱动的依赖合成 — 方法章程与实验设计(2026-09-23)

状态:设计冻结(用户 2026-09-23 裁定方向)。P0 已关闭(commit 0c6a7f8 / 42f4a19),
本文件是 P1/P2 的执行章程。GPU 训练仍用户门控。

## 1. 研究主张(候选,待验证)

> 可验证的长上下文数据仍可能让近似错误程序持续成功,并把监督集中在无助于
> 任务边界的输出上(ID 枚举占监督 ~73%)。我们提出**语义反例驱动的依赖合成**:
> 在可执行世界中区分正确程序与有代表性的错误程序,将区分实例一致地渲染为
> 长文,并通过明确的答案契约(answer-only / +evidence / +procedure)与受控
> 训练获得跨任务精确性与迁移。

三项可检验贡献:
1. **方法**:自动生成能区分正确/错误程序的合法世界与长文任务,不逐题手工
   造 hard negative(ProgramMutator + WitnessBuilder)。
2. **质量机制**:正文支持、操作可辨识、反事实响应、空集≠信息不足、
   长度与计算解耦,证书声明实际核验范围。
3. **训练证据**:同来源、同预算、同回放下,反例驱动采样(D)优于普通采样(C),
   且通用能力代价受控。

## 2. 已有证据基础(P0 核查结论,全部磁盘可复现)

- GW 失败解剖:FP 99.7% 是图内真实节点、too_deep 为主;空 gold 题训练后
  57–64% 答非空(Base 4.5%)。→ 目标 1:**空集回归 + 深度/边界判别**。
- ID 枚举占监督 token ~73–85%(records 族)。→ 目标 2:**答案契约分离**
  (消融定因果,不是一律删 ID)。
- H 深度可合并为合取,无数据依赖链。→ 目标 3:**变异程序作为难度来源**,
  而非堆叠常量 filter。
- 短锚从未进 A/B;arm_b 下游已补齐(B 的 MMLU-Pro 仅 −2.8pp vs A −17.6pp,
  归因前需抽验协议)。→ 目标 4:**真回放臂 R**。

## 3. 三个新模块(文件作用域互斥,全部走既有 spine 接口)

### 3.1 `longworld/synthesis/capability_mutations.py`(新)
- `mutants(family, program) -> list[dict]`:每个任务程序生成少量**语义变异**
  (可执行、可解释):
  - filter_aggregate/group_compare/join_lookup:忽略一个条件、AND→OR、
    忽略排除条件、按文本顺序代替值域;
  - 集合类(set_complete 目标):多报相关项(放松一个条件)、漏报(收紧)、
    ignore-emptiness;
  - asof_state:latest-text 替代 as-of 折叠、忽略撤销;
  - rule_holdout:使用另一规则族、忽略示例;
  - alias_locate:词面最近邻替代绑定、取最后一次声明。
- `witness_split(family, program, rows)`(纯函数):对一个已生成世界,算出
  每个变异 `P~` 的答案;`P(W) != P~(W)` 的变异即为**已区分**。输出
  `distinguished: list[str]`、`P(W)`、`P~(W)`。不修改银行——审计侧计算。

### 3.2 `scripts/measure_witness_coverage.py`(新)
- 输入银行目录,复用 runner 的索引与 parse_context/solve 机制;对每行算
  变异区分度,按 family×length 汇总:
  - `distinguished_fraction`(每行至少一个变异被区分的比例);
  - per-mutant 区分率(哪些错误程序"到处成功"——它们就是模型学到的捷径);
  - 空集/信息不足行占比。
- 输出 `verification.witness.json`(并入 manifest 引用)。

### 3.3 `scripts/export_contract_arms.py`(新)
- 对**冻结的 arm_a 行**(不可变)生成三种输出契约导出:
  - `answer-only`:用户问题要求的最终结果(filter_aggregate→aggregate 值;
    group_compare→groups+verdict;join_lookup→aggregate+by_entity;
    alias_locate→ids;asof→total+entities;rule→label+features;
    unanswerable→"UNKNOWN");
  - `answer+minimal-evidence`:+matched/pairs 的**数量与分组摘要**(非全 ID 清单);
  - `full-provenance`:原样(=现状)。
- 键名与预算字段与既有索引约定一致(`full_chat_tokens` 重测;
  `supervised_tokens` 重测;`contract` 字段进索引切片)。
- **指令一致性**:answer-only 行的 instruction 文本重渲染为只要求最终结果
  (PROMPTS 已有"report the aggregate"变体可复用;无合适措辞的族新增一条
  phrasing,写进 families.PROMPTS 并在 render_instruction 范围内)。

## 4. P1 实验臂(训练侧,用户门控后启动)

| 臂 | 数据 | 回放 | 回答 |
|---|---|---|---|
| R | 无 LongWorld(短锚纯回放) | ~23%→100% | 短锚回放下 Base 自身训练的退化底线 |
| A' | arm_a 行 full-provenance + 真回放 | 固定 | 与 B' 对照输出契约 |
| B' | arm_a 行 answer-only(同 instr) + 真回放 | 与 A' 相同 | ID 枚举监督是否导致集合膨胀 |
| C | p73 普通合法实例(匹配配额) | 固定 | 反例驱动采样对照 |
| D | p73 witness-rich 实例(≥k 区分) | 与 C 相同 | 语义反例是否产生增益 |

匹配纪律:C 与 D 同 family 配额、同长度分布、同空集比例、同答案规模分布、
同预算四口径;D 的选择规则 = `distinguished_fraction ≥ 阈值`(先 0.5);
分层数进 arms.json。R/A'/B' 复用 C1 短锚池(DocQA-RL-1.6K 现货 13,485 行,
配比四口径实测入 manifest——**输入 token 占比≠梯度占比**,如实分报)。

## 5. P73 pilot 银行(`configs/p73_counterexample_v1.json`)

- 家族:records 三族 + alias/asof/rule/join_unanswerable + **set_complete
  (重新纳入,55.2% 形状唯一率如实记录,不再作硬门)**。
- 规模:~250 世界 × 4 行 ≈ 1,000 行(筛选用,不追求统计功效;P2 扩)。
- 深度:1/2(records 族);F 族按其可行面。
- 长度:8K/32K/64K(覆盖 p72 短档与 p71 双峰之间)。
- 生成后必跑:求解器全量复核、塌缩门(`--index` 双口径)、
  witness 覆盖审计(3.2)、曝光(group_id=world)。
- seed_base 860000(远离 p71/p72 段)。

## 6. 成功标准(预注册)

1. **D > C** 于:内部 held-out 精确集合(exact/precision)、GW 式空集/深度
   错误下降、未见 renderer 视图;监督 token 比 A' 少且 recall 不降。
2. **回放保护**:R/A'/B'/C/D 的 IFEval/GPQA/MMLU-Pro 相对 Base 的损失回到
   预先声明带宽(≤3pp?)——数字训练前定。
3. 负结果照实进 `.hl`,定位为可发表资产(捷径覆盖不减、契约无差异皆可能)。
4. **不做的**:256K、十倍放量、全 domain 重构、Agent/L5。

## 7. 与已有工作的差异声明(写作时核对,不是现在声称)

MuSiQue(组合多跳)/LongCrafter(任务+证据图)/π²(表格双路径)/
Context-DPO(反事实替换)/RACES(类型化组合)/CheckList(行为测试)各覆盖
其一;本方法的主张是**"区分正确程序与合理错误程序"作为生成准则** +
**区分度的一致渲染** + **契约化监督**三者合成的端到端管线,并在受控训练中
验证。论文 novelty 检索推迟到 P2 有数据后做。

## 8. 濒危决策点(执行中触发)

- witness 选择导致 family 配额失真(某族区分度天然低)→ 如实记录,不硬凑。
- set_complete 深度只支持 1 → 接受,不扩深度。
- answer-only 使某族监督 token 掉 >80%(学习信号太薄)→ 该族降级为
  minimal-evidence,记录。
