# P71 执行章程(基于 plans/9-rippling-stearns.md 批准版)

Status: active. 2026-09-19 启动。前序:P70 章程已 superseded(G70-4/G70-6 诚实修正见该文件状态行)。

**总目标(P71)**:同一个版本化银行,稳定导出语义可比的主臂/格式臂/机制臂;一致正文/依赖验证;相同训练设置下可直接比较。GPU 训练保持用户门控。

## Track A — 实验完整性

- [x] **A1** L4 结构 split:config `trained_rule_family` + `split_for_rule_family` 接线;调度解耦(cell_index 奇偶替代 seed%2);manifest `split_basis` + structure×split 计数;两侧 depth/target 覆盖断言(测试)。v2 不可变,章程已修正。commit e6df788。
- [x] **A2** sample_index.jsonl:runner finalize 写入 + `build_record_bank_index.py` v2 事后回填(3,476 行,869 group);字面 `group_id`/`full_message_tokens`;group_id=world_id 语义注记;`configs/p70_superset_v2.json` 追溯钉定。v2 首个真实曝光数:696 文档/6.0@1.5ep。commit e6df788/aeca553。
- [x] **A3** budget_recommendation 进 manifest:`derive_budget_report` 库化 + runner 合并(gbs+规则文案)。commit e6df788。
- [x] **A4** hash 分层取臂:`scripts/extract_arms.py`(world 原子性 hash;stratum=family×depth×target×split×rule_family;每臂 train.jsonl+索引切片+预算;arms.json 绑定银行 shas+overlap 声明);字节一致重跑验证。commit ad30fb7。**待办**:格式臂渲染视图(A5 后)+ token 重测。
- [ ] **A5** renderer F 族扩展(renderer-track agent 进行中)。
- [x] **A6** verification.json:solve_visible 全量复核收据(micro bank 16/16)。commit e6df788。**待办**:v2 跑一次进收据(生成侧无脚本,收据随 v3 波)。
- [x] **resume 修复**:--resume 采纳已完成 shard(receipt+指纹+哈希三重验证);ledger 原子重写。中断恢复演练通过。commit e6df788。

## Track B — 数据供给

- [ ] **B1** v3 扩容波:等 A1(已落地)+ B2/B4 定型;逐族池算术(split 规则×~40% 损耗×4 行/shard);`configs/p71_pool_v1.json`;新 seed_base;分阶段计时。
- [ ] **B2** F2 集合完备性(f2-track agent 进行中;用户裁定:小批实测达标并入 v3)。
- [x] **B3-a** 错题上下文基线:`measure_wrong_context_baseline.py`,v2 实测 198/0 泄漏。commit bf12f0b。
- [ ] **B3-b** 元特征可预测性(可顺延)。**B3-c** 词法重叠 audit 化(可顺延)。
- [ ] **B4** unanswerable 脊柱适配器(unanswerable-track agent 进行中)。

## Track C — 真实性与训练准备

- [ ] **C1** IETF + Finance IR grounded 适配器(里程碑外,用户已选)。
- [ ] **C2** 短锚诚实降配比 ~23%(用户已裁定;配比文档待改)。
- [ ] **C3** 训练实验冻结文档(依赖 A4 格式臂 + A5 + B1)。

## Track D — 卫生

- [x] 评测脚本 + 汇总器提交。commit ed8556c。
- [ ] security hook 静默失败可见化(hook 配置不在本仓,需 `.claude/` 侧查)。

## 红线(继承 P70 + 用户裁定)

不调门/不放松 fail-closed/padding 不计语义规模;π² 词汇门是渲染契约校验非难度门;GPU 用户门控;不为 30-40% 短锚虚报;solver 过≠模型会学,门只作风险诊断,行为结论由 C3 预注册实验回答。
